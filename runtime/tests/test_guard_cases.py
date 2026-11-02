from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.guard.observer import GuardObserver
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


def make_run(store: RuntimeStore):
    return store.create_run({
        "goal_id": "goal-japan", "goal_title": "Plan a Japan trip",
        "goal_description": "Find practical travel information.", "task": "Research entry requirements.",
        "source_url": "", "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"], ["browser.read"])


def test_observer_correlates_denial_and_deduplicates_case(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {
        "outcome": "deny", "reason": "Navigation to an unauthorized origin was blocked.",
    }, "browser.navigate")
    observer = GuardObserver(store)

    case = observer.observe(event)
    duplicate = observer.observe(event)

    assert case["trigger_event_id"] == event["id"]
    assert case["run_id"] == run["id"]
    assert case["goal_id"] == "goal-japan"
    assert case["agent_id"] == run["agent_id"]
    assert case["provider"] == "meta"
    assert case["model"] == "muse-spark-test"
    assert case["signal"] == "unauthorized_origin"
    assert duplicate["id"] == case["id"]
    assert len(store.get_guard_case(case["id"])["evidence"]) == 1


def test_runtime_service_connects_aip_denials_to_guard_observer(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    service = RuntimeService(store=store)

    service._event(run["id"], "aip.decision", "denied", {"reason": "permission denied"}, "browser.click")

    cases = service.guard_observer.cases.list_cases()
    assert len(cases) == 1
    assert cases[0]["run_id"] == run["id"]
    assert cases[0]["evidence"][0]["payload"]["type"] == "aip.decision"


def test_guard_evidence_is_retrievable_hash_chained_and_persistent(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    store = RuntimeStore(db_path)
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "permission denied"}, "browser.click")
    case = GuardObserver(store).observe(event)
    store.append_guard_evidence(case["id"], "runtime_event", "evt-followup", event["timestamp"], {"note": "Follow-up evidence."})

    assert store.verify_guard_case_integrity(case["id"])
    assert len(store.get_guard_case(case["id"])["evidence"]) == 2
    reopened = RuntimeStore(db_path)
    loaded = reopened.get_guard_case(case["id"])
    assert loaded["model"] == "muse-spark-test"
    assert len(loaded["evidence"]) == 2
    assert reopened.verify_guard_case_integrity(case["id"])


def test_guard_case_transitions_are_validated_and_audited(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "policy boundary"}, "browser.navigate")
    case = GuardObserver(store).observe(event)

    updated = store.transition_guard_case(case["id"], "triaged", "Observer signal reviewed.", "user")
    assert updated["status"] == "triaged"
    assert updated["transitions"][0]["from_status"] == "detected"
    assert updated["transitions"][0]["to_status"] == "triaged"
    try:
        store.transition_guard_case(case["id"], "interviewing", "Skip investigation steps.")
    except ValueError as exc:
        assert "Invalid Guard case transition" in str(exc)
    else:
        raise AssertionError("Invalid case transition should be rejected")


def test_guard_api_requires_runtime_auth_and_serves_cases(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "capability denied"}, "browser.navigate")
    case = GuardObserver(store).observe(event)
    service = RuntimeService(store=store)
    app = create_app(service)

    with TestClient(app) as client:
        assert client.get("/guard/cases").status_code == 401
        headers = {"Authorization": "Bearer test-runtime-token-with-32-characters"}
        listing = client.get("/guard/cases?goal_id=goal-japan", headers=headers)
        detail = client.get(f"/guard/cases/{case['id']}", headers=headers)
        integrity = client.get(f"/guard/cases/{case['id']}/integrity", headers=headers)
        transition = client.post(f"/guard/cases/{case['id']}/transition", headers=headers,
                                 json={"status": "triaged", "reason": "Reviewed the AIP evidence."})

    assert listing.status_code == detail.status_code == integrity.status_code == transition.status_code == 200
    assert listing.json()[0]["id"] == case["id"]
    assert len(detail.json()["evidence"]) == 1
    assert integrity.json() == {"case_id": case["id"], "valid": True}
    assert transition.json()["status"] == "triaged"
    store.revoke_guard_case_permission(case["id"], "local_user")
    with TestClient(app) as client:
        assert client.get(f"/guard/cases/{case['id']}", headers=headers).status_code == 403
        assert client.get("/guard/cases", headers=headers).json() == []
