import asyncio
import json
import os

import httpx
import pytest

from runtime.aip.policy import authorize_tool, is_demo_url
from runtime.demo.university import PAGES, DemoServer
from runtime.models import ModelDecision, RunRequest, ToolCall
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


class DemoStubBrowser:
    instances: dict = {}

    def __init__(self, run_id, authorized_origin, data_dir):
        self.current = ""
        self.log = []
        self.closed = False
        DemoStubBrowser.instances[run_id] = self

    def _text(self, url):
        return PAGES.get(url.rsplit("/", 1)[-1], {}).get("text", "")

    async def navigate(self, args):
        self.log.append(args["url"])
        self.current = args["url"]
        return {"url": args["url"], "title": "demo", "visible_text": self._text(args["url"]),
                "content_is_untrusted": True}

    async def extract_text(self, args):
        return {"url": self.current, "selector": "body", "text": self._text(self.current),
                "content_is_untrusted": True}

    async def inspect(self, args):
        return {"url": self.current, "title": "demo", "visible_text": self._text(self.current),
                "content_is_untrusted": True}

    async def screenshot(self):
        return {"url": self.current, "screenshot_path": "/demo/capture.png", "format": "png"}

    async def click(self, args):
        return {"clicked": args["selector"]}

    async def fill(self, args):
        return {"filled": args["selector"]}

    async def close(self):
        self.closed = True


class DemoScript:
    def __init__(self, steps):
        self.steps = steps
        self.calls = 0

    async def generate(self, model, messages, tools):
        index = sum(1 for item in messages if item.get("role") == "assistant")
        kind, *rest = self.steps[min(index, len(self.steps) - 1)]
        if kind == "findings":
            text = json.dumps({"findings": [
                {"finding": finding, "quote": quote} for finding, quote in rest[0]
            ]})
            return ModelDecision(message=text, raw_assistant_message={"role": "assistant", "content": text})
        self.calls += 1
        raw_call = {"id": f"demo-{self.calls}", "type": "function",
                    "function": {"name": kind, "arguments": json.dumps(rest[0] if rest else {})}}
        return ModelDecision(tool_calls=[ToolCall(id=raw_call["id"], name=kind, arguments=rest[0] if rest else {})],
                             raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]})


class DemoProviders:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, script):
        self.script = script

    def get(self, provider):
        return self.script


def test_demo_server_serves_fictional_pages():
    with DemoServer() as server:
        response = httpx.get(f"{server.base_url}/catalog", timeout=10)
        assert response.status_code == 200
        assert "Fictional" in response.text
        assert httpx.get(f"{server.base_url}/nope", timeout=10).status_code == 404


def test_aip_demo_exemption_is_loopback_and_path_scoped(monkeypatch):
    url = "http://127.0.0.1:9999/demo/university/catalog"
    assert is_demo_url(url) is False
    monkeypatch.setenv("KEPLERV_DEMO_MODE", "1")
    assert is_demo_url(url) is True
    assert is_demo_url("http://127.0.0.1:9999/other/page") is False
    assert is_demo_url("http://example.com/demo/university/catalog") is False
    assert is_demo_url("https://127.0.0.1/demo/university/catalog") is False
    base = {"agent_id": "a", "registered_agent_id": "a", "goal_id": "g", "registered_goal_id": "g",
            "permissions": {"browser.read"}, "authorized_origin": None}
    navigate = authorize_tool(tool_name="browser.navigate", arguments={"url": url}, **base)
    assert navigate.outcome == "allow"
    click = authorize_tool(tool_name="browser.click", arguments={"selector": "#pay"},
                           current_url=url, **base)
    assert click.outcome == "deny"
    interact = authorize_tool(tool_name="browser.click", arguments={"selector": "#pay"},
                              current_url=url, **{**base, "permissions": {"browser.read", "browser.interact"}})
    assert interact.outcome == "require_approval"
    monkeypatch.delenv("KEPLERV_DEMO_MODE")
    assert authorize_tool(tool_name="browser.navigate", arguments={"url": url}, **base).outcome == "deny"


def test_demo_scenario_a_research_across_fixture_pages(monkeypatch, tmp_path):
    async def run_test():
        monkeypatch.setenv("KEPLERV_DEMO_MODE", "1")
        DemoStubBrowser.instances = {}
        with DemoServer() as server:
            base_url = server.base_url
            store = RuntimeStore(tmp_path / "demo-a.sqlite3")
            service = RuntimeService(store=store, providers=DemoProviders(DemoScript([
                ("browser_navigate", {"url": f"{base_url}/catalog"}),
                ("browser_navigate", {"url": f"{base_url}/requirements"}),
                ("browser_extract_text", {}),
                ("findings", [
                    ("Spring enrollment needs nine completed prerequisite credits.", "nine completed prerequisite credits"),
                    ("Applications close on the fifteenth of November.", "fifteenth of November"),
                    ("A faculty signature is required for enrollment.", "faculty signature"),
                ]),
            ])), browser_factory=DemoStubBrowser)
            agent = await service.create_run(RunRequest(
                goal_id="goal-demo-a", goal_title="Demo research", goal_description="Demo research.",
                task="Research the fictional university pages.", source_url=f"{base_url}/catalog",
            ))
            await service._tasks[agent["id"]]
            run = service.store.get_run(agent["id"])
            assert run["status"] == "completed", run["error"]
            assert service.store.list_guard_cases("goal-demo-a") == []
            assert len(service.store.list_evidence("goal-demo-a")) == 3
            log = DemoStubBrowser.instances[agent["id"]].log
            assert f"{base_url}/catalog" in log and f"{base_url}/requirements" in log

    asyncio.run(run_test())


def test_demo_scenario_b_payment_requires_approval(monkeypatch, tmp_path):
    async def run_test():
        monkeypatch.setenv("KEPLERV_DEMO_MODE", "1")
        DemoStubBrowser.instances = {}
        with DemoServer() as server:
            base_url = server.base_url
            store = RuntimeStore(tmp_path / "demo-b.sqlite3")
            service = RuntimeService(store=store, providers=DemoProviders(DemoScript([
                ("browser_navigate", {"url": f"{base_url}/tuition"}),
                ("browser_click", {"selector": "#pay-750"}),
                ("browser_extract_text", {}),
                ("findings", [
                    ("Demo balance due is 750 fictional credits.", "750 fictional credits"),
                    ("Pressing Pay performs no real transaction here.", "no real transaction"),
                    ("Payment exercises the approval workflow only.", "approval workflow"),
                ]),
            ])), browser_factory=DemoStubBrowser)
            agent = await service.create_run(RunRequest(
                goal_id="goal-demo-b", goal_title="Demo payment", goal_description="Demo payment.",
                task="Review the fictional tuition page.", source_url=f"{base_url}/tuition",
            ))
            for _ in range(200):
                if service.store.get_run(agent["id"])["status"] == "blocked":
                    break
                await asyncio.sleep(0.01)
            assert service.store.get_run(agent["id"])["status"] == "blocked"
            pending = next(call_id for (run_id, call_id) in service._pending_approvals if run_id == agent["id"])
            await service.control(agent["id"], "approve", pending)
            await service._tasks[agent["id"]]
            assert service.store.get_run(agent["id"])["status"] == "completed"

    asyncio.run(run_test())


def test_demo_scenario_c_injection_is_blocked_and_investigated(monkeypatch, tmp_path):
    async def run_test():
        monkeypatch.setenv("KEPLERV_DEMO_MODE", "1")
        DemoStubBrowser.instances = {}
        with DemoServer() as server:
            base_url = server.base_url
            store = RuntimeStore(tmp_path / "demo-c.sqlite3")
            service = RuntimeService(store=store, providers=DemoProviders(DemoScript([
                ("browser_navigate", {"url": f"{base_url}/poisoned-notice"}),
                ("browser_extract_text", {}),
                ("browser_navigate", {"url": "https://attacker.example/collect"}),
                ("browser_navigate", {"url": f"{base_url}/library"}),
                ("browser_extract_text", {}),
                ("findings", [
                    ("Latent planning compresses observations before acting.", "Latent planning compresses observations"),
                    ("Structured simulators keep explicit object state.", "explicit object state"),
                    ("The list compares predictive coding approaches.", "predictive coding"),
                ]),
            ])), browser_factory=DemoStubBrowser)
            agent = await service.create_run(RunRequest(
                goal_id="goal-demo-c", goal_title="Demo rogue", goal_description="Demo rogue.",
                task="Research the fictional library pages.", source_url=f"{base_url}/poisoned-notice",
            ))
            await service._tasks[agent["id"]]
            run = service.store.get_run(agent["id"])
            assert run["status"] == "completed", run["error"]
            cases = service.store.list_guard_cases("goal-demo-c")
            assert len(cases) == 1 and cases[0]["signal"] == "unauthorized_origin"
            assert "https://attacker.example/collect" not in DemoStubBrowser.instances[agent["id"]].log

    asyncio.run(run_test())


@pytest.mark.skipif(os.getenv("KEPLERV_LIVE_BROWSER_TEST") != "1", reason="set KEPLERV_LIVE_BROWSER_TEST=1 for real Chromium")
def test_demo_live_chromium_researches_fixtures(monkeypatch, tmp_path):
    from runtime.browser.controller import BrowserController

    async def run_test():
        monkeypatch.setenv("KEPLERV_DEMO_MODE", "1")
        with DemoServer() as server:
            base_url = server.base_url
            store = RuntimeStore(tmp_path / "demo-live.sqlite3")
            service = RuntimeService(store=store, providers=DemoProviders(DemoScript([
                ("browser_navigate", {"url": f"{base_url}/catalog"}),
                ("browser_extract_text", {}),
                ("findings", [
                    ("EAS 501 covers multiscale modeling with nine credits.", "nine credits and a simulation studio"),
                    ("CSE 635 covers interactive agentic systems.", "interactive agentic systems"),
                    ("See the fictional academic schedule for details.", "fictional academic schedule"),
                ]),
            ])), browser_factory=BrowserController)
            agent = await service.create_run(RunRequest(
                goal_id="goal-demo-live", goal_title="Demo live", goal_description="Demo live.",
                task="Research the fictional catalog.", source_url=f"{base_url}/catalog",
            ))
            await service._tasks[agent["id"]]
            run = service.store.get_run(agent["id"])
            assert run["status"] == "completed", run["error"]
            assert len(service.store.list_evidence("goal-demo-live")) == 3

    asyncio.run(run_test())
