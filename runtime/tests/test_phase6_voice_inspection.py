"""Phase 6 M8: voice inspection intents + gateway session close route.

Intent validation uses scripted fake adapters (no live model). The close
route is exercised through the authenticated test client against a temp
store; closing is human-confirmed in the UI and never fires from voice.
"""

from __future__ import annotations

import asyncio
import json
import os

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.gateway.sessions import GatewaySessionStore
from runtime.models import ModelDecision
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore
from runtime.voice.intent import INTENT_TYPES, VoiceContext, interpret


class ScriptedModel:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def generate(self, model, messages, tools):
        return ModelDecision(message=json.dumps(self._payload), usage={})


class ScriptedProviders:
    active_provider = "meta"
    active_model = "scripted-test-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def get(self, provider_id):
        return ScriptedModel(self._payload)


def _run(payload: dict, context: VoiceContext):
    return asyncio.run(interpret("inspect that merchant", context, ScriptedProviders(payload)))


def _context(**kwargs) -> VoiceContext:
    return VoiceContext(**kwargs)


def _session_context() -> VoiceContext:
    return _context(
        sessions=[{"id": "gsess-1", "external_agent_id": "merchant-x", "status": "open"}],
        external_agents=[{"id": "merchant-x", "agent_id": "merchant-x",
                          "identity_status": "self_declared"}],
    )


def test_inspection_intent_types_are_registered():
    for intent_type in (
        "WHO_IS_TALKING_TO", "VERIFY_OFFER", "WHY_FLAGGED",
        "STOP_TALKING_TO", "COMPARE_OFFERS",
    ):
        assert intent_type in INTENT_TYPES


def test_verify_offer_accepts_a_known_session():
    intent = _run(
        {"type": "VERIFY_OFFER", "target_id": "gsess-1", "args": {},
         "summary_for_speech": "Checking the offer."},
        _session_context(),
    )
    assert intent.needs_clarification is False
    assert intent.target_id == "gsess-1"


def test_inspection_intent_rejects_invented_session_ids():
    intent = _run(
        {"type": "WHY_FLAGGED", "target_id": "gsess-invented", "args": {},
         "summary_for_speech": "Checking flags."},
        _session_context(),
    )
    assert intent.needs_clarification is True
    assert intent.target_id is None


def test_stop_talking_to_accepts_agent_id_alias():
    intent = _run(
        {"type": "STOP_TALKING_TO", "target_id": "merchant-x", "args": {},
         "summary_for_speech": "Opening the session to close it."},
        _session_context(),
    )
    assert intent.needs_clarification is False
    assert intent.target_id == "merchant-x"


def test_compare_offers_needs_no_target():
    intent = _run(
        {"type": "COMPARE_OFFERS", "target_id": None, "args": {},
         "summary_for_speech": "Comparing offers."},
        _session_context(),
    )
    assert intent.needs_clarification is False


def _harness(tmp_path):
    store = RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3")
    service = RuntimeService(store=store)
    sessions = GatewaySessionStore(store.db_path)
    session = sessions.open_session(
        run_id="run-x", agent_id="agent-x", external_agent_id="merchant-x",
        endpoint="http://127.0.0.1:1/agent",
        provenance={"asserted_by": "test"}, label="replay",
    )
    return service, sessions, session


def test_verification_route_reports_real_records(tmp_path):
    service, sessions, session = _harness(tmp_path)
    sessions.append_message(
        session["id"], message_id="m1", direction="outbound", sender="agent-x",
        recipient="merchant-x", kind="request", label="replay", untrusted=False,
        payload={"action": "quote", "product_id": "starlamp"},
    )
    sessions.append_message(
        session["id"], message_id="m2", direction="inbound", sender="merchant-x",
        recipient="agent-x", kind="response", label="replay", untrusted=True,
        payload={"status": "quoted", "product_id": "starlamp", "price_credits": 120},
        reply_to="m1",
    )
    with TestClient(create_app(service)) as client:
        denied = client.get(f"/gateway/sessions/{session['id']}/verification")
        assert denied.status_code == 401
        headers = {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}
        missing = client.get("/gateway/sessions/gsess-nope/verification", headers=headers)
        assert missing.status_code == 404
        response = client.get(
            f"/gateway/sessions/{session['id']}/verification", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["classification"]["classification"] == "legitimate-negotiation"
        assert body["verdicts"][0]["verdict"] == "independently-observed"
        assert body["abstained"] is False
        assert "fake-rules-v1" in body["analysis_label"]


def test_close_session_route_requires_auth_and_closes(tmp_path):
    service, sessions, session = _harness(tmp_path)
    with TestClient(create_app(service)) as client:
        denied = client.post(f"/gateway/sessions/{session['id']}/close")
        assert denied.status_code == 401
        headers = {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}
        missing = client.post("/gateway/sessions/gsess-nope/close", headers=headers)
        assert missing.status_code == 404
        closed = client.post(f"/gateway/sessions/{session['id']}/close", headers=headers)
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"
        again = client.post(f"/gateway/sessions/{session['id']}/close", headers=headers)
        assert again.status_code == 200
        assert sessions.get_session(session["id"])["status"] == "closed"
