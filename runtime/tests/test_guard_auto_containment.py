"""Policy-gated Guard auto-containment (fake signals only, no live models)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.guard.auto_containment import (
    AUTO_MODES,
    DEFAULT_AUTO_MODE,
    permitted_auto_actions,
    plan_auto_actions,
    requires_approval,
    resolve_auto_mode,
    should_auto_contain,
)
from runtime.guard.observer import GuardObserver
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore

HEADERS = {"Authorization": "Bearer test-runtime-token-with-32-characters"}


def make_run(store: RuntimeStore):
    return store.create_run({
        "goal_id": "goal-auto", "goal_title": "Auto-containment test",
        "goal_description": "Fictional goal for auto-containment tests.",
        "task": "Research a fictional topic.", "source_url": "",
        "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"],
        ["browser.read", "browser.interact", "delegate"])


def open_high_case(store: RuntimeStore):
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {
        "reason": "Navigation to an unauthorized origin was blocked.",
    }, "browser.navigate")
    case = GuardObserver(store).observe(event)
    assert case["severity"] == "high"
    store.update_run(run["id"], status="running")
    return store.get_run(run["id"]), store.get_guard_case(case["id"])


def test_mode_matrix_permits_pause_only_by_default(monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    assert DEFAULT_AUTO_MODE == "pause-only"
    assert resolve_auto_mode() == "pause-only"
    assert set(AUTO_MODES) == {"off", "pause-only", "full"}

    assert permitted_auto_actions("off") == frozenset()
    assert permitted_auto_actions("pause-only") == frozenset({"pause_agent", "deny_pending"})
    assert permitted_auto_actions("full") == frozenset(
        {"pause_agent", "deny_pending", "isolate_browser", "revoke_capability"})

    assert requires_approval("pause_agent", "pause-only") is False
    assert requires_approval("deny_pending", "pause-only") is False
    assert requires_approval("isolate_browser", "pause-only") is True
    assert requires_approval("revoke_capability", "pause-only") is True
    assert requires_approval("isolate_browser", "full") is False
    assert requires_approval("revoke_capability", "full") is False
    assert requires_approval("pause_agent", "off") is True

    monkeypatch.setenv("KEPLERV_GUARD_AUTO_CONTAINMENT", "full")
    assert resolve_auto_mode() == "full"
    assert resolve_auto_mode("off") == "off"  # explicit setting wins over env
    with pytest.raises(ValueError):
        resolve_auto_mode("panic")
    monkeypatch.setenv("KEPLERV_GUARD_AUTO_CONTAINMENT", "panic")
    with pytest.raises(ValueError):
        resolve_auto_mode()


def test_plan_triggers_only_on_high_risk_signals():
    assert should_auto_contain(classification="injection") is True
    assert should_auto_contain(classification="unauthorized-request") is True
    assert should_auto_contain(classification="policy-violation") is True
    assert should_auto_contain(classification="misleading") is False
    assert should_auto_contain(classification="legitimate-negotiation") is False
    assert should_auto_contain(severity="high") is True
    assert should_auto_contain(severity="medium") is False
    assert should_auto_contain() is False

    assert plan_auto_actions(classification="injection", mode="off") == []
    assert plan_auto_actions(classification="legitimate-negotiation", severity="low",
                             mode="pause-only") == []
    assert plan_auto_actions(classification="injection", mode="pause-only") == ["deny_pending"]
    assert plan_auto_actions(severity="high", mode="pause-only") == ["deny_pending"]
    assert plan_auto_actions(classification="injection", mode="full") == ["deny_pending", "isolate_browser"]
    assert plan_auto_actions(classification="injection", mode="full",
                             capability="browser.interact") == [
        "deny_pending", "isolate_browser", "revoke_capability"]


def test_pause_only_auto_pause_emits_audit_case_update_and_notice(tmp_path, monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = open_high_case(store)
    service = RuntimeService(store=store)
    assert service.guard_auto_containment_mode == "pause-only"

    result = asyncio.run(service.auto_contain_case(
        case["id"], classification="injection",
        reason="Untrusted reply carries instruction-override markers."))

    assert result["triggered"] is True
    assert [item["action"] for item in result["actions"]] == ["deny_pending"]
    assert result["status"] == "awaiting_human_review"
    assert store.get_run(run["id"])["status"] == "paused"

    updated = store.get_guard_case(case["id"])
    actions = [item for item in updated["evidence"] if item["source_type"] == "guard_containment_action"]
    assert len(actions) == 1
    payload = actions[0]["payload"]
    assert payload["authorization"] == "policy auto-containment (mode=pause-only)"
    assert payload["authorized_by"] == "guard_auto_containment"
    assert payload["aip_policy_modified"] is False
    assert any(t["actor"] == "guard_auto_containment" for t in updated["transitions"])

    notices = [event for event in store.list_events() if event["type"] == "guard.auto_containment"]
    assert len(notices) == 1
    notice = notices[0]["payload"]
    assert notice["mode"] == "pause-only"
    assert notice["action"] == "deny_pending"
    assert "automatically" in notice["summary"]
    assert "override" in notice["notice"]
    assert store.verify_guard_case_integrity(case["id"])


def test_off_mode_abstains_and_changes_nothing(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = open_high_case(store)
    service = RuntimeService(store=store, guard_auto_containment_mode="off")

    result = asyncio.run(service.auto_contain_case(
        case["id"], classification="injection", reason="High-risk markers."))

    assert result["triggered"] is False
    assert result["actions"] == []
    assert store.get_run(run["id"])["status"] == "running"
    assert store.get_guard_case(case["id"])["status"] == "detected"
    assert not [event for event in store.list_events() if event["type"] == "guard.auto_containment"]
    with pytest.raises(PermissionError):
        asyncio.run(service.guard_containment.apply_auto(
            case["id"], "pause_agent", "Must stay manual.", mode="off"))


def test_full_mode_auto_isolate_and_revoke_need_no_approval(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = open_high_case(store)
    service = RuntimeService(store=store, guard_auto_containment_mode="full")

    with pytest.raises(PermissionError):
        asyncio.run(service.guard_containment.apply_auto(
            case["id"], "isolate_browser", "Needs approval in pause-only.", mode="pause-only"))
    with pytest.raises(PermissionError):
        asyncio.run(service.guard_containment.apply_auto(
            case["id"], "revoke_capability", "Needs approval in pause-only.",
            mode="pause-only", capability="browser.interact"))

    result = asyncio.run(service.auto_contain_case(
        case["id"], classification="unauthorized-request", capability="browser.interact",
        reason="Untrusted reply requests credentials."))

    assert [item["action"] for item in result["actions"]] == [
        "deny_pending", "isolate_browser", "revoke_capability"]
    assert all(item["authorization"] == "policy auto-containment (mode=full)"
               for item in result["actions"])
    fresh = store.get_run(run["id"])
    assert fresh["status"] == "paused"
    assert fresh["browser_isolated"] is True
    assert fresh["permissions"] == ["browser.read", "delegate"]
    assert len([event for event in store.list_events() if event["type"] == "guard.auto_containment"]) == 3
    assert len([item for item in store.get_guard_case(case["id"])["evidence"]
                if item["source_type"] == "guard_containment_action"]) == 3
    assert store.verify_guard_case_integrity(case["id"])


class OverrideRuntime:
    """Stub lifecycle control so resume stays a state change, not a relaunch."""

    def __init__(self, service: RuntimeService):
        self.service = service

    async def control(self, run_id, action):
        store = self.service.store
        status = "paused" if action == "pause" else "running"
        store.update_run(run_id, status=status)
        return {"id": run_id, "status": status}

    def deny_pending_approvals(self, run_id):
        return self.service.deny_pending_approvals(run_id)

    async def isolate_browser(self, run_id):
        await self.service.isolate_browser(run_id)

    async def restore_browser(self, run_id):
        await self.service.restore_browser(run_id)


def test_human_override_resumes_and_restores_after_auto(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = open_high_case(store)
    service = RuntimeService(store=store, guard_auto_containment_mode="full")
    service.guard_containment.runtime = OverrideRuntime(service)

    asyncio.run(service.auto_contain_case(
        case["id"], classification="policy-violation", reason="Coercion markers."))
    assert store.get_run(run["id"])["status"] == "paused"

    resumed = asyncio.run(service.guard_containment.apply(
        case["id"], "resume_agent", "Reviewed; the agent may continue."))
    restored = asyncio.run(service.guard_containment.apply(
        case["id"], "restore_browser", "Reviewed; the session looks safe."))
    assert resumed["authorization"] == "explicit user action"
    assert restored["authorization"] == "explicit user action"
    fresh = store.get_run(run["id"])
    assert fresh["status"] == "running"
    assert fresh["browser_isolated"] is False
    assert store.get_guard_case(case["id"])["status"] == "awaiting_human_review"
    assert store.verify_guard_case_integrity(case["id"])


def test_low_risk_signal_does_not_trigger(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = open_high_case(store)
    service = RuntimeService(store=store)

    result = asyncio.run(service.auto_contain_case(
        case["id"], classification="legitimate-negotiation", severity="low",
        reason="Routine check."))
    assert result["triggered"] is False
    assert store.get_guard_case(case["id"])["status"] == "detected"


def test_auto_containment_mode_api_is_explicit_and_authenticated(tmp_path, monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    service = RuntimeService(store=RuntimeStore(tmp_path / "runtime.sqlite3"))
    with TestClient(create_app(service)) as client:
        assert client.get("/guard/auto-containment").status_code == 401
        assert client.post("/guard/auto-containment", json={"mode": "full"}).status_code == 401
        assert client.get("/guard/auto-containment", headers=HEADERS).json() == {
            "mode": "pause-only", "modes": ["off", "pause-only", "full"], "default": "pause-only"}
        assert client.post("/guard/auto-containment", headers=HEADERS,
                           json={"mode": "full"}).json() == {"mode": "full"}
        assert service.guard_auto_containment_mode == "full"
        assert client.post("/guard/auto-containment", headers=HEADERS,
                           json={"mode": "panic"}).status_code == 422
