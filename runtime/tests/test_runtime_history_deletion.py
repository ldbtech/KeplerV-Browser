from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.gateway.sessions import GatewaySessionStore
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


def test_runtime_history_reset_is_authenticated_and_erases_research_and_gateway_records(tmp_path, monkeypatch):
    monkeypatch.setenv("KEPLERV_RUNTIME_TOKEN", "local-test-token")
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = store.create_run(
        {
            "goal_id": "goal-private", "goal_title": "Private goal", "goal_description": "User context",
            "task": "Research this", "source_url": "https://example.org/", "step_budget": 8, "token_budget": 5000,
        },
        "fixture", "fixture-model", [], ["browser.read"],
    )
    store.add_evidence(run, "A quoted finding", "A source quote", "https://example.org/source")
    sessions = GatewaySessionStore(store.db_path)
    sessions.open_session(run_id=run["id"], agent_id=run["agent_id"], external_agent_id="public-agent", endpoint="https://agent.example/rpc", provenance={})
    service = RuntimeService(store=store)

    with TestClient(create_app(service)) as client:
        assert client.post("/privacy/runtime-history/reset").status_code == 401
        response = client.post("/privacy/runtime-history/reset", headers={"Authorization": "Bearer local-test-token"})
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        snapshot = client.get("/snapshot", headers={"Authorization": "Bearer local-test-token"}).json()

    assert snapshot["agents"] == []
    assert snapshot["events"] == []
    assert snapshot["evidence"] == []
    assert snapshot["guard_cases"] == []
    assert GatewaySessionStore(store.db_path).list_sessions() == []
    assert store.db_path.exists()
