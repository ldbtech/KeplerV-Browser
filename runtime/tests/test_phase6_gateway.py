"""Phase 6 M1+M2: External-agent registry + Trust Gateway tests.

Fictional fixtures only (``*.example.test`` hosts, loopback fixture
allowlist). No live network, no keychain access.
"""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest
from pydantic import ValidationError

from runtime.aip.policy import authorize_tool
from runtime.gateway.client import (
    BudgetExceeded,
    GatewayAuthError,
    GatewayCancelled,
    GatewayRateLimited,
    GatewaySSRFBlocked,
    GatewayTimeout,
    GatewayValidationError,
    TrustGateway,
)
from runtime.gateway.registry import ExternalAgentRegistry
from runtime.gateway.schemas import GatewayMessage, parse_message
from runtime.gateway.sessions import GatewaySessionStore
from runtime.gateway.ssrf import SSRFBlocked, validate_endpoint
from runtime.guard.risk import assess_rule_risk
from runtime.scheduler import TOOL_NAMES, RuntimeService
from runtime.store import RuntimeStore

PUBLIC_RESOLVE = lambda host, port, type=None: [(socket.AF_INET, None, None, None, ("93.184.216.34", 0))]  # noqa: E731
PRIVATE_RESOLVE = lambda host, port, type=None: [(socket.AF_INET, None, None, None, ("10.9.8.7", 0))]  # noqa: E731


def _harness(tmp_path):
    store = RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3")
    sessions = GatewaySessionStore(tmp_path / "store" / "runtime.sqlite3")
    registry = ExternalAgentRegistry(tmp_path / "gateway" / "agents.json")
    service = RuntimeService(store=store)
    run = store.create_run(
        {
            "goal_id": "goal-gw", "goal_title": "Fictional gateway test",
            "goal_description": "Made-up goal for gateway tests.",
            "task": "Exercise the trust gateway against fakes.",
            "source_url": "", "step_budget": 4, "token_budget": 4000,
        },
        "meta", "fake-model", TOOL_NAMES, ["gateway.read", "gateway.send", "gateway.verify"],
    )
    return store, sessions, registry, service, store.get_run(run["id"])


def _register(registry, agent_id="merchant-alpha", endpoint="https://merchant-alpha.example.test/agent", **extra):
    params = {
        "endpoint": endpoint, "identity_status": "self_declared",
        "capabilities_declared": ["catalog.quote"],
        "provenance": {"asserted_by": "test-harness", "evidence_refs": ["fixture:alpha"]},
    }
    params.update(extra)
    return registry.register(agent_id, **params)


def _echo_transport(correlate_reply=True):
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        body = json.loads(request.content.decode())
        payload = {
            "protocol": "keplerv-gateway/1", "session_id": body["session_id"],
            "message_id": f"resp-{counter['n']}", "reply_to": body["message_id"] if correlate_reply else "wrong-id",
            "sender": body["recipient"], "recipient": body["sender"],
            "kind": "response", "label": body.get("label", "live"),
            "payload": {"echo": body["payload"], "capabilities": ["catalog.quote"]},
        }
        return httpx.Response(200, json=payload)
    return httpx.MockTransport(handler)


# -- registry: identity, provenance, capabilities ----------------------------


def test_registry_requires_provenance_and_tier(tmp_path):
    _, _, registry, _, _ = _harness(tmp_path)
    with pytest.raises(ValueError):
        registry.register("no-prov", endpoint="https://x.example.test/a")
    with pytest.raises(ValueError):
        _register(registry, agent_id="bad-tier", identity_status="trusted-friend")
    record = _register(registry)
    assert record["identity_status"] == "self_declared"
    assert record["provenance"]["asserted_by"] == "test-harness"
    assert record["capabilities_declared"] == ["catalog.quote"]
    assert record["capabilities_observed"] == []


def test_registry_rejects_secrets_and_tracks_observed(tmp_path):
    _, _, registry, _, _ = _harness(tmp_path)
    with pytest.raises(ValueError):
        registry.register(
            "sneaky", endpoint="https://x.example.test/a",
            provenance={"asserted_by": "t", "token": "should-never-store"},
        )
    _register(registry)
    registry.record_observed_capability("merchant-alpha", "catalog.quote")
    record = registry.get("merchant-alpha")
    assert record["capabilities_observed"] == ["catalog.quote"]
    assert "token" not in json.dumps(record)
    updated = registry.set_identity_status("merchant-alpha", "suspected", asserted_by="guard-test")
    assert updated["identity_status"] == "suspected"
    assert updated["provenance"]["asserted_by"] == "guard-test"


# -- schemas: strict validation -------------------------------------------------


def test_schema_rejects_extra_fields_and_bad_kind():
    with pytest.raises(ValidationError):
        parse_message({
            "session_id": "s", "message_id": "m", "sender": "a",
            "recipient": "b", "payload": {}, "smuggled": "nope",
        })
    with pytest.raises(ValidationError):
        parse_message({
            "session_id": "s", "message_id": "m", "sender": "a",
            "recipient": "b", "kind": "mind-control", "payload": {},
        })
    assert isinstance(parse_message({
        "session_id": "s", "message_id": "m", "sender": "a",
        "recipient": "b", "payload": {"hello": "world"},
    }), GatewayMessage)


# -- SSRF guard ------------------------------------------------------------------


def test_ssrf_denies_localhost_private_metadata_and_creds():
    with pytest.raises(SSRFBlocked):
        validate_endpoint("http://127.0.0.1:9999/agent")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("https://127.0.0.1/agent")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("https://10.1.2.3/agent")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("https://169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("https://user:pass@merchant.example.test/agent")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("http://merchant.example.test/agent")
    with pytest.raises(SSRFBlocked):
        validate_endpoint("https://evil.example.test/agent", resolve=PRIVATE_RESOLVE)


def test_ssrf_allows_public_https_and_explicit_loopback_fixture(monkeypatch):
    info = validate_endpoint("https://merchant-alpha.example.test/agent", resolve=PUBLIC_RESOLVE)
    assert info["fixture"] == "false"
    info = validate_endpoint("http://127.0.0.1:8091/agent", allowlist={"127.0.0.1:8091"})
    assert info["fixture"] == "true"
    monkeypatch.setenv("KEPLERV_GATEWAY_FIXTURE_ALLOWLIST", "127.0.0.1:8092")
    info = validate_endpoint("http://127.0.0.1:8092/agent")
    assert info["fixture"] == "true"
    # Same host, different port: still blocked.
    with pytest.raises(SSRFBlocked):
        validate_endpoint("http://127.0.0.1:8093/agent")


# -- sessions + messaging -----------------------------------------------------------


@pytest.mark.asyncio
async def test_session_multi_round_transcript_and_events(tmp_path, monkeypatch):
    monkeypatch.setattr("runtime.gateway.client.validate_endpoint", lambda url, allowlist=None: {"scheme": "https", "host": "x", "port": 443})
    store, sessions, registry, service, run = _harness(tmp_path)
    _register(registry)
    gateway = TrustGateway(store, sessions, registry, transport=_echo_transport())
    session = await gateway.open_session(run, "merchant-alpha")
    assert session["status"] == "open"
    first = await gateway.send(session["id"], sender="local-agent", payload={"ask": "quote fictional beans"})
    assert first.reply_to is not None and first.untrusted is True
    second = await gateway.send(session["id"], sender="local-agent", payload={"ask": "quote fictional rice"})
    assert second.reply_to is not None
    transcript = sessions.list_transcript(session["id"])
    assert [message["direction"] for message in transcript] == ["outbound", "inbound", "outbound", "inbound"]
    assert all(message["untrusted"] == (message["direction"] == "inbound") for message in transcript)
    kinds = {event["type"] for event in store.list_run_events(run["id"])}
    assert {"gateway.session.opened", "gateway.message.sent", "gateway.message.received"} <= kinds
    record = registry.get("merchant-alpha")
    assert record["reliability"]["messages_sent"] == 2
    assert record["reliability"]["messages_received"] == 2
    assert "catalog.quote" in record["capabilities_observed"]
    closed = await gateway.close_session(session["id"])
    assert closed["status"] == "closed"


@pytest.mark.asyncio
async def test_send_enforces_budgets_rate_timeout_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr("runtime.gateway.client.validate_endpoint", lambda url, allowlist=None: {"scheme": "https", "host": "x", "port": 443})
    store, sessions, registry, _, run = _harness(tmp_path)
    _register(registry)
    gateway = TrustGateway(store, sessions, registry, transport=_echo_transport())

    tiny = await gateway.open_session(run, "merchant-alpha", max_messages=2, max_tokens=10**9)
    await gateway.send(tiny["id"], sender="local-agent", payload={"n": 1})
    with pytest.raises(BudgetExceeded):
        await gateway.send(tiny["id"], sender="local-agent", payload={"n": 2})

    poor = await gateway.open_session(run, "merchant-alpha", max_tokens=1)
    with pytest.raises(BudgetExceeded):
        await gateway.send(poor["id"], sender="local-agent", payload={"big": "x" * 100})

    pricey = await gateway.open_session(run, "merchant-alpha", max_cost_micros=5)
    with pytest.raises(BudgetExceeded):
        await gateway.send(pricey["id"], sender="local-agent", payload={"n": 1}, cost_micros=50)

    paced = TrustGateway(store, sessions, registry, transport=_echo_transport(), rate_per_minute=1)
    paced_session = await paced.open_session(run, "merchant-alpha")
    await paced.send(paced_session["id"], sender="local-agent", payload={"n": 1})
    with pytest.raises(GatewayRateLimited):
        await paced.send(paced_session["id"], sender="local-agent", payload={"n": 2})

    spaced = TrustGateway(store, sessions, registry, transport=_echo_transport(), min_interval_s=3600.0)
    spaced_session = await spaced.open_session(run, "merchant-alpha")
    await spaced.send(spaced_session["id"], sender="local-agent", payload={"n": 1})
    with pytest.raises(GatewayRateLimited):
        await spaced.send(spaced_session["id"], sender="local-agent", payload={"n": 2})

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("fictional stall")
    stalled = TrustGateway(store, sessions, registry, transport=httpx.MockTransport(timeout_handler))
    stalled_session = await stalled.open_session(run, "merchant-alpha")
    with pytest.raises(GatewayTimeout):
        await stalled.send(stalled_session["id"], sender="local-agent", payload={"n": 1}, timeout_s=0.05)

    cancellable = await gateway.open_session(run, "merchant-alpha")
    gateway.cancel(cancellable["id"])
    with pytest.raises(GatewayCancelled):
        await gateway.send(cancellable["id"], sender="local-agent", payload={"n": 1})


@pytest.mark.asyncio
async def test_redirect_to_evil_blocked_and_mismatched_correlation_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runtime.gateway.client.validate_endpoint",
        lambda url, allowlist=None: {"scheme": "https", "host": "x", "port": 443},
    )
    real_redirect = __import__("runtime.gateway.ssrf", fromlist=["validate_redirect"]).validate_redirect
    monkeypatch.setattr("runtime.gateway.client.validate_redirect", lambda location, allowlist=None: real_redirect(location, allowlist=allowlist or set(), resolve=PRIVATE_RESOLVE))
    store, sessions, registry, _, run = _harness(tmp_path)
    _register(registry)

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.test/steal"})

    gateway = TrustGateway(store, sessions, registry, transport=httpx.MockTransport(redirect_handler))
    session = await gateway.open_session(run, "merchant-alpha")
    with pytest.raises(GatewaySSRFBlocked):
        await gateway.send(session["id"], sender="local-agent", payload={"n": 1})

    sloppy = TrustGateway(store, sessions, registry, transport=_echo_transport(correlate_reply=False))
    sloppy_session = await sloppy.open_session(run, "merchant-alpha")
    with pytest.raises(GatewayValidationError):
        await sloppy.send(sloppy_session["id"], sender="local-agent", payload={"n": 1})


@pytest.mark.asyncio
async def test_auth_required_when_supported_and_ssrf_blocks_open(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path)
    _register(registry, agent_id="vaulted", auth={"type": "bearer-header"})
    gateway = TrustGateway(store, sessions, registry, transport=_echo_transport())
    # SSRF check runs against the real resolver for fictional hosts and must fail closed.
    with pytest.raises(GatewaySSRFBlocked):
        await gateway.open_session(run, "vaulted")
    kinds = {event["type"] for event in store.list_run_events(run["id"])}
    assert "gateway.ssrf_blocked" in kinds


@pytest.mark.asyncio
async def test_bearer_auth_header_sent_when_provided(tmp_path, monkeypatch):
    monkeypatch.setattr("runtime.gateway.client.validate_endpoint", lambda url, allowlist=None: {"scheme": "https", "host": "x", "port": 443})
    store, sessions, registry, _, run = _harness(tmp_path)
    _register(registry, agent_id="vaulted", auth={"type": "bearer-header"})
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={
            "session_id": body["session_id"], "message_id": "resp-auth",
            "reply_to": body["message_id"], "sender": body["recipient"],
            "recipient": body["sender"], "kind": "response", "payload": {},
        })

    gateway = TrustGateway(store, sessions, registry, transport=httpx.MockTransport(handler))
    session = await gateway.open_session(run, "vaulted")
    with pytest.raises(GatewayAuthError):
        await gateway.send(session["id"], sender="local-agent", payload={"n": 1})
    await gateway.send(session["id"], sender="local-agent", payload={"n": 1}, auth_token="explicit-caller-token")
    assert seen["auth"] == "Bearer explicit-caller-token"


# -- AIP + Guard wiring -----------------------------------------------------------------


def test_gateway_aip_matrix():
    def decide(tool, permissions, **extra):
        return authorize_tool(
            agent_id="a", registered_agent_id="a", goal_id="g", registered_goal_id="g",
            permissions=set(permissions), tool_name=tool, arguments={},
            authorized_origin=None, **extra)

    assert decide("gateway.discover", {"gateway.read"}).outcome == "allow"
    assert decide("gateway.discover", set()).outcome == "deny"
    assert decide("gateway.send", {"gateway.send"}).outcome == "require_approval"
    assert decide("gateway.send", {"gateway.send"}, user_approved=True).outcome == "allow"
    assert decide("gateway.open_session", {"gateway.send"}).outcome == "require_approval"
    assert decide("gateway.verify", {"gateway.verify"}).outcome == "require_approval"


def test_guard_gateway_signals():
    assert assess_rule_risk({"type": "gateway.ssrf_blocked"})[0] == "high"
    assert assess_rule_risk({"type": "gateway.provenance_unknown_session"})[1] == "provenance_unknown_session"
    assert assess_rule_risk({"type": "gateway.limit_exceeded"})[0] == "medium"
    assert assess_rule_risk({"type": "gateway.validation_failed"})[0] == "medium"


def test_gateway_api_routes(tmp_path):
    from fastapi.testclient import TestClient

    from runtime.api import create_app

    store, sessions, registry, service, run = _harness(tmp_path)
    _register(registry)
    session = sessions.open_session(
        run_id=run["id"], agent_id=run["agent_id"],
        external_agent_id="merchant-alpha",
        endpoint="https://merchant-alpha.example.test/agent",
        provenance={"identity_status": "self_declared"},
    )
    with TestClient(create_app(service)) as client:
        headers = {"Authorization": "Bearer test-runtime-token-with-32-characters"}
        assert client.get("/gateway/agents", headers=headers).status_code == 200
        assert client.get("/gateway/agents").status_code == 401
        response = client.get(f"/gateway/sessions/{session['id']}", headers=headers)
        assert response.status_code == 200
        transcript = client.get(f"/gateway/sessions/{session['id']}/transcript", headers=headers)
        assert transcript.status_code == 200
        assert transcript.json()["session_id"] == session["id"]
