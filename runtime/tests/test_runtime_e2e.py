import json
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.models import ModelDecision, RunRequest, ToolCall
from runtime.providers.base import ModelProviderError
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


PAGE_TEXT = (
    "The Python asyncio library provides infrastructure for writing single-threaded concurrent code. "
    "The event loop runs asynchronous tasks and callbacks, performs network IO, and runs subprocesses. "
    "Coroutines are declared with async def and can suspend execution with await."
)


class FakeBrowser:
    navigations = []
    interactions = []
    def __init__(self, run_id, authorized_origin, data_dir): self.origin = authorized_origin
    async def navigate(self, args):
        self.navigations.append(args["url"])
        return {"url": args["url"], "title": "asyncio — Python docs", "visible_text": PAGE_TEXT, "content_is_untrusted": True}
    async def extract_text(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "selector": "body", "text": PAGE_TEXT, "content_is_untrusted": True}
    async def inspect(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "title": "asyncio", "visible_text": PAGE_TEXT, "content_is_untrusted": True}
    async def screenshot(self): return {"url": "https://docs.python.org/", "screenshot_path": "/test/capture.png", "format": "png"}
    async def click(self, args):
        self.interactions.append(("click", args["selector"]))
        return {"clicked": args["selector"]}
    async def fill(self, args):
        self.interactions.append(("fill", args["selector"]))
        return {"filled": args["selector"]}
    async def close(self): pass


class FakeModel:
    name = "Fake model for deterministic tests"
    def __init__(self): self.turn = 0
    async def generate(self, model, messages, tools):
        self.turn += 1
        if self.turn == 1:
            call = {"id":"unsafe-1","type":"function","function":{"name":"browser_navigate","arguments":json.dumps({"url":"https://attacker.example/collect"})}}
            return ModelDecision(tool_calls=[ToolCall(id="unsafe-1", name="browser_navigate", arguments={"url":"https://attacker.example/"})],
                raw_assistant_message={"role":"assistant","content":None,"tool_calls":[call]})
        if self.turn == 2:
            call = {"id":"navigate-2","type":"function","function":{"name":"browser_navigate","arguments":json.dumps({"url":"https://docs.python.org/3/library/asyncio.html"})}}
            return ModelDecision(tool_calls=[ToolCall(id="navigate-2", name="browser_navigate", arguments={"url":"https://docs.python.org/3/library/asyncio.html"})],
                raw_assistant_message={"role":"assistant","content":None,"tool_calls":[call]})
        if self.turn == 3:
            call = {"id":"extract-3","type":"function","function":{"name":"browser_extract_text","arguments":"{}"}}
            return ModelDecision(tool_calls=[ToolCall(id="extract-3", name="browser_extract_text", arguments={})],
                raw_assistant_message={"role":"assistant","content":None,"tool_calls":[call]})
        findings = {"findings": [
            {"finding":"Asyncio supports concurrent code in a single thread.","quote":"single-threaded concurrent code"},
            {"finding":"The event loop manages asynchronous work and I/O.","quote":"runs asynchronous tasks and callbacks"},
            {"finding":"Coroutines can suspend using await.","quote":"can suspend execution with await"},
        ]}
        text = json.dumps(findings)
        return ModelDecision(message=text, raw_assistant_message={"role":"assistant","content":text}, usage={"total_tokens":120})


class FakeRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"
    def __init__(self): self.adapter = FakeModel()
    def get(self, provider_id): return self.adapter


class PausableModel:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.turn = 0
    async def generate(self, model, messages, tools):
        self.turn += 1
        if self.turn == 1:
            self.entered.set()
            await self.release.wait()
            return ModelDecision(message='{"findings":[]}')
        raise ModelProviderError("Muse service is unavailable.")


class PausableRegistry(FakeRegistry):
    def __init__(self): self.adapter = PausableModel()


class FailingModel:
    async def generate(self, model, messages, tools):
        raise ModelProviderError("Muse service is unavailable.")


class FailingRegistry(FakeRegistry):
    def __init__(self): self.adapter = FailingModel()


class BudgetModel:
    async def generate(self, model, messages, tools):
        return ModelDecision(message="Budget-bound response.", usage={"total_tokens":1200})


class BudgetRegistry(FakeRegistry):
    def __init__(self): self.adapter = BudgetModel()


class ApprovalModel:
    def __init__(self): self.turn = 0
    async def generate(self, model, messages, tools):
        self.turn += 1
        if self.turn == 1:
            name, args = "browser_navigate", {"url":"https://docs.python.org/3/library/asyncio.html"}
        elif self.turn == 2:
            name, args = "browser_click", {"selector":"#continue"}
        elif self.turn == 3:
            name, args = "browser_extract_text", {}
        else:
            findings = {"findings": [
                {"finding":"Asyncio supports concurrent code in a single thread.","quote":"single-threaded concurrent code"},
                {"finding":"The event loop manages asynchronous work and I/O.","quote":"runs asynchronous tasks and callbacks"},
                {"finding":"Coroutines can suspend using await.","quote":"can suspend execution with await"},
            ]}
            text = json.dumps(findings)
            return ModelDecision(message=text, raw_assistant_message={"role":"assistant","content":text})
        raw_call = {"id":f"approval-{self.turn}","type":"function","function":{"name":name,"arguments":json.dumps(args)}}
        return ModelDecision(tool_calls=[ToolCall(id=raw_call["id"], name=name, arguments=args)],
            raw_assistant_message={"role":"assistant","content":None,"tool_calls":[raw_call]})


class ApprovalRegistry(FakeRegistry):
    def __init__(self): self.adapter = ApprovalModel()


@pytest.mark.asyncio
async def test_real_runtime_loop_records_events_evidence_and_denies_bad_origin(tmp_path):
    FakeBrowser.navigations = []
    service = RuntimeService(RuntimeStore(tmp_path / "runtime.sqlite3"), FakeRegistry(), FakeBrowser, public_host_check=lambda _: True)
    agent = await service.create_run(RunRequest(
        goal_id="goal-test", goal_title="Research asyncio", goal_description="Summarize the article.",
        task="Find three findings and cite exact page text.", source_url="https://docs.python.org/3/library/asyncio.html",
    ))
    run_id = agent["id"]
    await service._tasks[run_id]
    run = service.store.get_run(run_id)
    events = service.store.list_events()
    evidence = service.store.list_evidence("goal-test")
    assert run["status"] == "completed", run["error"]
    assert len(evidence) == 3
    assert all(item["source_url"].startswith("https://docs.python.org/") for item in evidence)
    assert FakeBrowser.navigations == ["https://docs.python.org/3/library/asyncio.html"]
    assert any(item["type"] == "aip.decision" and item["status"] == "denied" for item in events)
    assert any(item["type"] == "agent.completed" for item in events)


@pytest.mark.asyncio
async def test_pause_holds_model_response_until_resume_and_errors_are_visible(tmp_path):
    registry = PausableRegistry()
    service = RuntimeService(RuntimeStore(tmp_path / "pause.sqlite3"), registry, FakeBrowser, public_host_check=lambda _: True)
    agent = await service.create_run(RunRequest(
        goal_id="goal-pause", goal_title="Pause test", goal_description="Check safe pause behavior.",
        source_url="https://docs.python.org/3/library/asyncio.html",
    ))
    task = service._tasks[agent["id"]]
    await asyncio.wait_for(registry.adapter.entered.wait(), timeout=1)
    await service.control(agent["id"], "pause")
    registry.adapter.release.set()
    await asyncio.sleep(0.02)
    paused = service.store.get_run(agent["id"])
    assert paused["status"] == "paused"
    assert paused["steps"] == 0
    assert not task.done()
    await service.control(agent["id"], "resume")
    await task
    failed = service.store.get_run(agent["id"])
    assert failed["status"] == "failed"
    assert "unavailable" in failed["error"]
    event_types = [event["type"] for event in service.store.list_events()]
    assert "agent.paused" in event_types and "agent.resumed" in event_types and "model.error" in event_types


@pytest.mark.asyncio
async def test_resume_restores_browser_through_aip_before_navigation(tmp_path):
    FakeBrowser.navigations = []
    service = RuntimeService(RuntimeStore(tmp_path / "restore.sqlite3"), FailingRegistry(), FakeBrowser, public_host_check=lambda _: False)
    agent = await service.create_run(RunRequest(
        goal_id="goal-restore", goal_title="Restore test", goal_description="Check the browser recovery boundary.",
        source_url="https://docs.python.org/3/library/asyncio.html",
    ))
    service.store.update_run(agent["id"], current_url="https://docs.python.org/3/library/asyncio.html", browser_visited=True, status="paused")
    await service._tasks[agent["id"]]
    run = service.store.get_run(agent["id"])
    assert run["status"] == "failed"
    assert FakeBrowser.navigations == []
    assert any(event["type"] == "aip.decision" and event["status"] == "denied" for event in service.store.list_events())


@pytest.mark.asyncio
async def test_token_budget_stops_run_after_cost_is_reported(tmp_path):
    service = RuntimeService(RuntimeStore(tmp_path / "budget.sqlite3"), BudgetRegistry(), FakeBrowser, public_host_check=lambda _: True)
    agent = await service.create_run(RunRequest(
        goal_id="goal-budget", goal_title="Budget test", goal_description="Check token usage limits.",
        source_url="https://docs.python.org/3/library/asyncio.html", token_budget=1000,
    ))
    await service._tasks[agent["id"]]
    run = service.store.get_run(agent["id"])
    assert run["status"] == "failed"
    assert run["tokens_used"] == 1200
    assert "token budget" in run["error"]


@pytest.mark.asyncio
async def test_page_interaction_waits_for_explicit_approval(tmp_path):
    FakeBrowser.interactions = []
    service = RuntimeService(RuntimeStore(tmp_path / "approval.sqlite3"), ApprovalRegistry(), FakeBrowser, public_host_check=lambda _: True)
    agent = await service.create_run(RunRequest(
        goal_id="goal-approval", goal_title="Approval test", goal_description="Check human review before click.",
        source_url="https://docs.python.org/3/library/asyncio.html",
    ))
    for _ in range(100):
        if service.store.get_run(agent["id"])["status"] == "blocked": break
        await asyncio.sleep(0.01)
    assert service.store.get_run(agent["id"])["status"] == "blocked"
    assert FakeBrowser.interactions == []
    pending = next(call_id for (run_id, call_id) in service._pending_approvals if run_id == agent["id"])
    await service.control(agent["id"], "approve", pending)
    await service._tasks[agent["id"]]
    assert service.store.get_run(agent["id"])["status"] == "completed"
    assert FakeBrowser.interactions == [("click", "#continue")]
    assert any(event["type"] == "tool.approval_required" for event in service.store.list_events())


def test_runtime_api_requires_bearer_token(tmp_path, monkeypatch):
    from runtime.api import create_app
    service = RuntimeService(RuntimeStore(tmp_path / "api.sqlite3"), FakeRegistry(), FakeBrowser)
    app = create_app(service)
    monkeypatch.setenv("KEPLERV_RUNTIME_TOKEN", "test-runtime-token-with-32-characters")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 401
        assert client.get("/healthz", headers={"Authorization":"Bearer invalid"}).status_code == 401
        assert client.get("/healthz", headers={"Authorization":"Bearer test-runtime-token-with-32-characters"}).json()["status"] == "ready"


def test_run_request_requires_standard_https_source():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunRequest(goal_id="g", goal_title="Research", goal_description="Details", source_url="http://docs.python.org/")
    with pytest.raises(ValidationError):
        RunRequest(goal_id="g", goal_title="Research", goal_description="Details", source_url="https://user:secret@docs.python.org/")


@pytest.mark.asyncio
async def test_delegation_count_and_depth_are_bounded(tmp_path):
    store = RuntimeStore(tmp_path / "delegation.sqlite3")
    values = {"goal_id":"g", "goal_title":"Research", "goal_description":"Details", "task":"Research task",
        "source_url":"https://docs.python.org/", "step_budget":8, "token_budget":5000}
    root = store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read", "delegate"])
    service = RuntimeService(store, FakeRegistry(), FakeBrowser)
    assert await service._has_delegation_budget(root)
    child_a = store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read", "delegate"], parent_agent_id=root["agent_id"])
    assert await service._has_delegation_budget(root)
    store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read"], parent_agent_id=root["agent_id"])
    assert not await service._has_delegation_budget(root)
    grandchild = store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read"], parent_agent_id=child_a["agent_id"])
    assert not await service._has_delegation_budget(grandchild)


@pytest.mark.asyncio
async def test_stopping_parent_stops_active_delegates(tmp_path):
    store = RuntimeStore(tmp_path / "stop-tree.sqlite3")
    values = {"goal_id":"g", "goal_title":"Research", "goal_description":"Details", "task":"Research task",
        "source_url":"https://docs.python.org/", "step_budget":8, "token_budget":5000}
    root = store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read", "delegate"])
    child = store.create_run(values, "meta", "muse-spark-1.3", [], ["browser.read"], parent_agent_id=root["agent_id"])
    service = RuntimeService(store, FakeRegistry(), FakeBrowser)
    await service.control(root["id"], "stop")
    assert store.get_run(root["id"])["status"] == "stopped"
    assert store.get_run(child["id"])["status"] == "stopped"


def test_sqlite_store_persists_runs_events_and_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "persistent.sqlite3")
    run = store.create_run({"goal_id":"g", "goal_title":"T", "goal_description":"D", "task":"Task", "source_url":"https://docs.python.org/", "step_budget":8, "token_budget":5000}, "meta", "muse-spark-1.3", [], ["browser.read"])
    store.add_event(run, "agent.created", "success", {"summary":"created"})
    store.add_evidence(run, "A finding with enough detail.", "A direct source quote.")
    reopened = RuntimeStore(tmp_path / "persistent.sqlite3")
    assert reopened.get_run(run["id"])["agent_id"] == run["agent_id"]
    assert reopened.get_run(run["id"])["status"] == "paused"
    assert [event["type"] for event in reopened.list_events()] == ["agent.created", "agent.recovered"]
    assert len(reopened.list_evidence("g")) == 1
