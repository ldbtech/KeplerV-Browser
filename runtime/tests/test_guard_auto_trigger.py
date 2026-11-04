"""Auto-containment triggering from the live gateway flow (fake signals only)."""

from __future__ import annotations

import asyncio

from runtime.guard.observer import GuardObserver
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


def make_running_run(store: RuntimeStore):
    run = store.create_run({
        "goal_id": "goal-trigger", "goal_title": "Trigger test",
        "goal_description": "Fictional goal for auto-containment trigger tests.",
        "task": "Research a fictional topic.", "source_url": "",
        "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"],
        ["browser.read", "browser.interact", "delegate"])
    store.update_run(run["id"], status="running")
    return store.get_run(run["id"])


def open_flagged_case(store: RuntimeStore, run, classification: str):
    event = store.add_event(run, "gateway.external_behavior_flagged", "flagged", {
        "session_id": "session-trigger", "external_agent_id": "external-trigger",
        "classification": classification, "evidence_refs": ["msg-1"],
        "summary": "Guard flagged observable risk markers in an external reply.",
    }, "gateway.send")
    case = GuardObserver(store).observe(event)
    assert case is not None
    return store.get_guard_case(case["id"])


def auto_notices(store: RuntimeStore):
    return [event for event in store.list_events()
            if event["type"] == "guard.auto_containment"]


def test_flagged_injection_reply_auto_pauses_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_running_run(store)
    case = open_flagged_case(store, run, "injection")
    assert case["severity"] == "high"
    service = RuntimeService(store=store)
    assert service.guard_auto_containment_mode == "pause-only"

    asyncio.run(service._maybe_auto_contain_flagged_exchange(run))

    assert store.get_run(run["id"])["status"] == "paused"
    assert store.get_guard_case(case["id"])["status"] == "awaiting_human_review"
    notices = auto_notices(store)
    assert len(notices) == 1
    assert notices[0]["status"] == "success"
    assert notices[0]["payload"]["case_id"] == case["id"]
    assert notices[0]["payload"]["action"] == "deny_pending"


def test_routine_denial_never_auto_triggers(tmp_path, monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_running_run(store)
    denied = store.add_event(run, "service.access_denied", "denied", {
        "reason": "Missing grant.", "summary": "Service access denied.",
    }, "gmail.read")
    case = GuardObserver(store).observe(denied)
    assert case is not None and case["severity"] == "high"
    service = RuntimeService(store=store)

    asyncio.run(service._maybe_auto_contain_flagged_exchange(run))

    assert store.get_run(run["id"])["status"] == "running"
    assert store.get_guard_case(case["id"])["status"] == "detected"
    assert auto_notices(store) == []


def test_off_mode_abstains_on_flagged_reply(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_running_run(store)
    case = open_flagged_case(store, run, "unauthorized-request")
    service = RuntimeService(store=store, guard_auto_containment_mode="off")

    asyncio.run(service._maybe_auto_contain_flagged_exchange(run))

    assert store.get_run(run["id"])["status"] == "running"
    assert store.get_guard_case(case["id"])["status"] == "detected"
    assert auto_notices(store) == []


def test_repeated_trigger_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("KEPLERV_GUARD_AUTO_CONTAINMENT", raising=False)
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_running_run(store)
    open_flagged_case(store, run, "policy-violation")
    service = RuntimeService(store=store)

    asyncio.run(service._maybe_auto_contain_flagged_exchange(run))
    asyncio.run(service._maybe_auto_contain_flagged_exchange(
        store.get_run(run["id"])))

    notices = auto_notices(store)
    assert len(notices) == 1
    assert store.get_run(run["id"])["status"] == "paused"
