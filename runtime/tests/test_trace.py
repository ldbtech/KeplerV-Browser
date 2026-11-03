import asyncio
import json
import os

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.experiments.replay import format_playback, verify_trace
from runtime.models import ModelDecision, RunRequest
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


PAGE = "The asyncio event loop runs asynchronous tasks and callbacks for concurrent code. Coroutines suspend execution with await while waiting."


class TraceBrowser:
    def __init__(self, run_id, authorized_origin, data_dir):
        pass

    async def navigate(self, args):
        return {"url": args["url"], "title": "t", "visible_text": PAGE, "content_is_untrusted": True}

    async def extract_text(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "selector": "body",
                "text": PAGE, "content_is_untrusted": True}

    async def inspect(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "title": "t",
                "visible_text": PAGE, "content_is_untrusted": True}

    async def screenshot(self):
        return {"url": "https://docs.python.org/", "screenshot_path": "/t.png", "format": "png"}

    async def click(self, args):
        return {"clicked": args["selector"]}

    async def fill(self, args):
        return {"filled": args["selector"]}

    async def close(self):
        pass


class TraceScript:
    def __init__(self):
        self.calls = 0

    async def generate(self, model, messages, tools):
        from runtime.models import ToolCall
        steps = sum(1 for item in messages if item.get("role") == "assistant")
        if steps == 0:
            from runtime.models import ToolCall as TC
            raw = {"id": "t1", "type": "function", "function": {
                "name": "browser_navigate", "arguments": json.dumps({"url": "https://docs.python.org/3/library/asyncio.html"})}}
            return ModelDecision(tool_calls=[TC(id="t1", name="browser_navigate",
                                                 arguments={"url": "https://docs.python.org/3/library/asyncio.html"})],
                                 raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [raw]},
                                 usage={"total_tokens": 100})
        if steps == 1:
            from runtime.models import ToolCall as TC
            raw = {"id": "t2", "type": "function", "function": {"name": "browser_extract_text", "arguments": "{}"}}
            return ModelDecision(tool_calls=[TC(id="t2", name="browser_extract_text", arguments={})],
                                 raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [raw]},
                                 usage={"total_tokens": 120})
        text = json.dumps({"findings": [
            {"finding": "The event loop runs asynchronous tasks for concurrent code.", "quote": "runs asynchronous tasks and callbacks"},
            {"finding": "Coroutines suspend while waiting for results.", "quote": "suspend execution with await"},
            {"finding": "Asyncio supports single-threaded concurrency.", "quote": "concurrent code"},
        ]})
        return ModelDecision(message=text, raw_assistant_message={"role": "assistant", "content": text},
                             usage={"total_tokens": 80})


class TraceProviders:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self):
        self.adapter = TraceScript()

    def get(self, provider):
        return self.adapter


def _completed_run(tmp_path, name="trace"):
    store = RuntimeStore(tmp_path / f"{name}.sqlite3")
    service = RuntimeService(store=store, providers=TraceProviders(), browser_factory=TraceBrowser,
                             public_host_check=lambda _: True)

    async def run_test():
        agent = await service.create_run(RunRequest(
            goal_id="goal-trace", goal_title="Trace test", goal_description="Trace test.",
            source_url="https://docs.python.org/3/library/asyncio.html",
        ))
        await service._tasks[agent["id"]]
        return agent["id"]

    run_id = asyncio.run(run_test())
    assert service.store.get_run(run_id)["status"] == "completed"
    return service, run_id


def test_trace_endpoint_serves_run_history_with_metrics(tmp_path):
    service, run_id = _completed_run(tmp_path)
    with TestClient(create_app(service)) as client:
        assert client.get(f"/runs/{run_id}/trace").status_code == 401
        assert client.get("/runs/run-missing/trace",
                          headers={"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}).status_code == 404
        response = client.get(f"/runs/{run_id}/trace",
                              headers={"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"})
    assert response.status_code == 200
    trace = response.json()
    assert trace["trace_version"] == 1
    assert trace["run"]["status"] == "completed"
    assert len(trace["evidence"]) == 3
    assert trace["metrics"]["steps"] == 3
    assert trace["metrics"]["tokens_used"] == 300
    assert len(trace["metrics"]["model_calls"]) == 3
    assert {call["tool"] for call in trace["metrics"]["tool_calls"]} == {"browser.navigate", "browser.extract_text"}
    assert trace["metrics"]["denials"] == 0


def test_replay_verifies_valid_trace_and_rejects_corruption(tmp_path):
    service, run_id = _completed_run(tmp_path)
    trace = service.trace_run(run_id)
    assert verify_trace(trace) == []
    playback = format_playback(trace)
    assert "[REPLAY] Recorded KeplerV run" in playback
    assert "agent.completed" in playback
    corrupted = json.loads(json.dumps(trace))
    corrupted["events"] = [event for event in corrupted["events"] if event["type"] != "agent.created"]
    assert any("agent creation" in error for error in verify_trace(corrupted))
    reordered = json.loads(json.dumps(trace))
    reordered["events"] = list(reversed(reordered["events"]))
    assert any("out of order" in error for error in verify_trace(reordered))


def test_replay_cli_labels_and_exits(tmp_path):
    import subprocess
    import sys
    service, run_id = _completed_run(tmp_path)
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(service.trace_run(run_id)), encoding="utf-8")
    completed = subprocess.run([sys.executable, "-m", "runtime.experiments.replay", str(path)],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0
    assert "[REPLAY] Trace verified" in completed.stdout
