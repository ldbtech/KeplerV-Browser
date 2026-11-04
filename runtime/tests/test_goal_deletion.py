"""Per-goal deletion and browser-data clearing (local fixtures only)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.gateway.sessions import GatewaySessionStore
from runtime.guard.observer import GuardObserver
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore

HEADERS = {"Authorization": "Bearer local-test-token"}


def make_run(store: RuntimeStore, goal_id: str, run_no: int = 1):
    run = store.create_run({
        "goal_id": goal_id, "goal_title": f"Goal {goal_id}",
        "goal_description": "Fictional goal for deletion tests.",
        "task": "Research a fictional topic.", "source_url": "https://example.org/",
        "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"],
        ["browser.read", "browser.interact", "delegate"])
    store.update_run(run["id"], status="running", current_url="https://example.org/page",
                     browser_visited=3, text_extracted=1200)
    store.add_event(store.get_run(run["id"]), "tool.execution.completed", "success",
                    {"summary": f"evidence {run_no}"}, "browser.extract_text")
    store.add_evidence(store.get_run(run["id"]), f"Finding {run_no}",
                       "A quoted passage.", "https://example.org/source")
    denied = store.add_event(store.get_run(run["id"]), "aip.decision", "denied",
                             {"reason": "Navigation to an unauthorized origin was blocked."},
                             "browser.navigate")
    GuardObserver(store).observe(denied)
    return store.get_run(run["id"])


def make_session(store: RuntimeStore, run):
    sessions = GatewaySessionStore(store.db_path)
    return sessions.open_session(run_id=run["id"], agent_id=run["agent_id"],
                                 external_agent_id="public-agent",
                                 endpoint="https://agent.example/rpc", provenance={})


def make_screenshot(store: RuntimeStore, run_id: str):
    folder = store.db_path.parent / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}.png"
    path.write_bytes(b"\x89PNG-fake-bytes")
    return path


def test_delete_goal_removes_only_that_goal(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run_a = make_run(store, "goal-a")
    run_b = make_run(store, "goal-b")
    session_a = make_session(store, run_a)
    session_b = make_session(store, run_b)
    shot_a = make_screenshot(store, run_a["id"])
    shot_b = make_screenshot(store, run_b["id"])
    service = RuntimeService(store=store)
    service._searched_urls[run_a["id"]] = {"https://example.org/"}

    result = asyncio.run(service.delete_goal("goal-a"))

    assert result["goal_id"] == "goal-a"
    assert result["runs"] == 1 and result["evidence"] == 1
    assert store.get_run(run_a["id"]) is None
    assert store.get_run(run_b["id"]) is not None
    assert store.list_evidence("goal-a") == []
    assert len(store.list_evidence("goal-b")) == 1
    assert [c for c in store.list_guard_cases() if c["goal_id"] == "goal-a"] == []
    assert [c for c in store.list_guard_cases() if c["goal_id"] == "goal-b"]
    sessions = GatewaySessionStore(store.db_path)
    assert session_a["id"] not in {s["id"] for s in sessions.list_sessions()}
    assert session_b["id"] in {s["id"] for s in sessions.list_sessions()}
    assert not shot_a.exists() and shot_b.exists()
    assert run_a["id"] not in service._searched_urls
    assert store.verify_guard_case_integrity(
        [c for c in store.list_guard_cases() if c["goal_id"] == "goal-b"][0]["id"])


def test_delete_goal_unknown_goal_raises_key_error(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    service = RuntimeService(store=store)
    try:
        asyncio.run(service.delete_goal("goal-missing"))
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unknown goal")


def test_delete_goal_route_is_authenticated_and_mapped(tmp_path, monkeypatch):
    monkeypatch.setenv("KEPLERV_RUNTIME_TOKEN", "local-test-token")
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    make_run(store, "goal-a")
    service = RuntimeService(store=store)
    with TestClient(create_app(service)) as client:
        assert client.post("/goals/goal-a/delete").status_code == 401
        missing = client.post("/goals/goal-missing/delete", headers=HEADERS)
        assert missing.status_code == 404
        response = client.post("/goals/goal-a/delete", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["goal_id"] == "goal-a"
        assert store.list_runs() == []


def test_clear_browser_data_keeps_history_and_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store, "goal-a")
    shot = make_screenshot(store, run["id"])
    service = RuntimeService(store=store)
    cleared = []

    class FakeBrowser:
        async def clear_site_data(self):
            cleared.append(True)
            return True

    service._browsers[run["id"]] = FakeBrowser()
    service._visited_urls[run["id"]] = {"https://example.org/"}

    result = asyncio.run(service.clear_browser_data())

    assert result == {"contexts_cleared": 1, "screenshots_deleted": 1, "runs_reset": 1}
    assert cleared == [True]
    assert not shot.exists()
    fresh = store.get_run(run["id"])
    assert fresh["current_url"] is None
    assert fresh["browser_visited"] == 0 and fresh["text_extracted"] == 0
    assert len(store.list_evidence("goal-a")) == 1
    assert service._visited_urls == {}
    assert run["id"] in service._browsers  # live contexts stay open


def test_clear_browser_data_route_is_authenticated(tmp_path, monkeypatch):
    monkeypatch.setenv("KEPLERV_RUNTIME_TOKEN", "local-test-token")
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    service = RuntimeService(store=store)
    with TestClient(create_app(service)) as client:
        assert client.post("/privacy/browser-data/clear").status_code == 401
        response = client.post("/privacy/browser-data/clear", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["screenshots_deleted"] == 0


def test_clear_all_data_removes_screenshots(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store, "goal-a")
    shot = make_screenshot(store, run["id"])
    store.clear_all_data()
    assert not shot.exists()
    assert store.list_runs() == []
