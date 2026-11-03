"""Phase 6 M3: synthetic agentic internet tests.

Four deterministic fictional merchants (cooperative, competitive,
misleading, adversarial) on loopback fixtures. Socket-free tests run the
same handlers behind ``httpx.MockTransport``; the live-server test binds
real ``127.0.0.1`` servers and skips where binds are blocked. No live
network, no keychain access, fictional data only.
"""

from __future__ import annotations

import json
import socket

import pytest

from runtime.gateway.client import GatewayAuthError, TrustGateway
from runtime.gateway.registry import ExternalAgentRegistry
from runtime.gateway.sessions import GatewaySessionStore
from runtime.gateway.ssrf import SSRFBlocked, validate_endpoint
from runtime.merchants import (
    MERCHANT_PROFILES,
    adversarial,
    competitive,
    cooperative,
    misleading,
)
from runtime.merchants import catalog_fixture
from runtime.merchants.base import MerchantServer, make_routing_transport, mock_transport_for
from runtime.merchants.discovery import (
    DiscoveryAuthError,
    DiscoveryError,
    discover_agents,
)
from runtime.merchants.scenarios import canonical_bytes, run_script, scenario_script
from runtime.scheduler import TOOL_NAMES, RuntimeService
from runtime.store import RuntimeStore

PRIVATE_RESOLVE = lambda host, port, type=None: [(socket.AF_INET, None, None, None, ("10.9.8.7", 0))]  # noqa: E731

FIXTURE_TOKEN = "fixture-merchant-token-for-tests-only"

MOCK_BASES = {
    cooperative.AGENT_ID: "http://127.0.0.1:18091",
    competitive.AGENT_ID: "http://127.0.0.1:18092",
    misleading.AGENT_ID: "http://127.0.0.1:18093",
    adversarial.AGENT_ID: "http://127.0.0.1:18094",
}

MOCK_ALLOWLIST = {f"127.0.0.1:{port}" for port in (18091, 18092, 18093, 18094)}


def _harness(tmp_path, *, auth_type="none"):
    store = RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3")
    sessions = GatewaySessionStore(tmp_path / "store" / "runtime.sqlite3")
    registry = ExternalAgentRegistry(tmp_path / "gateway" / "agents.json")
    service = RuntimeService(store=store)
    run = store.create_run(
        {
            "goal_id": "goal-m3", "goal_title": "Fictional merchant test",
            "goal_description": "Made-up goal for synthetic merchant tests.",
            "task": "Exercise the synthetic agentic internet.",
            "source_url": "", "step_budget": 8, "token_budget": 8000,
        },
        "meta", "fake-model", TOOL_NAMES, ["gateway.read", "gateway.send", "gateway.verify"],
    )
    tiers = {
        cooperative.AGENT_ID: ("self_declared", ["fixture:cooperative"]),
        competitive.AGENT_ID: ("self_declared", ["fixture:competitive"]),
        misleading.AGENT_ID: ("unknown", ["fixture:misleading"]),
        adversarial.AGENT_ID: ("suspected", ["fixture:adversarial"]),
    }
    for agent_id, (tier, refs) in tiers.items():
        registry.register(
            agent_id,
            endpoint=f"{MOCK_BASES[agent_id]}/agent",
            identity_status=tier,
            capabilities_declared=list(MERCHANT_PROFILES[agent_id].CAPABILITIES),
            provenance={"asserted_by": "test-harness", "evidence_refs": refs},
            auth={"type": auth_type},
        )
    return store, sessions, registry, service, store.get_run(run["id"])


def _routing_transport(**kwargs):
    entries = {base: MERCHANT_PROFILES[agent_id] for agent_id, base in MOCK_BASES.items()}
    return make_routing_transport(entries, **kwargs)


# -- discovery: explicit endpoints + served descriptors --------------------------


@pytest.mark.asyncio
async def test_discovery_returns_four_distinct_descriptors():
    bases = [MOCK_BASES[agent_id] for agent_id in sorted(MOCK_BASES)]
    descriptors = await discover_agents(bases, allowlist=set(MOCK_ALLOWLIST), transport=_routing_transport())
    assert [d["agent_id"] for d in descriptors] == sorted(MOCK_BASES)
    assert len({d["endpoint"] for d in descriptors}) == 4
    assert len({json.dumps(d["capabilities_declared"], sort_keys=True) for d in descriptors}) == 4
    assert len({json.dumps(d["history"], sort_keys=True) for d in descriptors}) == 4
    for descriptor in descriptors:
        assert descriptor["fixture"] is True
        assert descriptor["surface_label"] == "fake"
        assert "fictional" in descriptor["currency"].casefold()
        assert "fictional" in descriptor["marketplace"].casefold()
        assert "fictional" in descriptor["fictional_disclaimer"].casefold()
        assert "token" not in json.dumps(descriptor).casefold()


@pytest.mark.asyncio
async def test_discovery_ssrf_blocks_private_unlisted_evil_and_redirects():
    with pytest.raises(SSRFBlocked):
        await discover_agents(["https://10.9.8.7/agent"], transport=_routing_transport())
    with pytest.raises(SSRFBlocked):
        await discover_agents(["http://127.0.0.1:9"], transport=_routing_transport())
    with pytest.raises(SSRFBlocked):
        await discover_agents(
            ["https://user:pass@merchant.example.test/x"], transport=_routing_transport()
        )
    with pytest.raises(DiscoveryError):
        await discover_agents([], transport=_routing_transport())

    import httpx

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.test/capabilities"})

    with pytest.raises(DiscoveryError):
        await discover_agents(
            ["http://127.0.0.1:18091"],
            allowlist={"127.0.0.1:18091"},
            transport=httpx.MockTransport(redirect_handler),
        )


@pytest.mark.asyncio
async def test_discovery_rejects_non_fixture_and_secret_descriptors():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "agent_id": "sneaky", "endpoint": "http://127.0.0.1:18091/agent",
            "capabilities_declared": [], "fixture": True, "api_key": "must-never-pass",
        })

    with pytest.raises(DiscoveryError):
        await discover_agents(
            ["http://127.0.0.1:18091"],
            allowlist={"127.0.0.1:18091"},
            transport=httpx.MockTransport(handler),
        )


# -- profile A: cooperative -------------------------------------------------------


@pytest.mark.asyncio
async def test_cooperative_accurate_and_respects_conditions(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path)
    gateway = TrustGateway(store, sessions, registry, transport=_routing_transport())
    session = await gateway.open_session(run, cooperative.AGENT_ID, label="replay", fixture_allowlist=set(MOCK_ALLOWLIST))

    quote = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "quote", "product_id": "starlamp"}, fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    assert quote.untrusted is True
    assert quote.payload["status"] == "quoted"
    assert quote.payload["price_credits"] == catalog_fixture.PRODUCTS["starlamp"]["price_credits"]
    assert quote.payload["surface_label"] == "fake"

    accepted = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "order", "product_id": "starlamp", "max_price_credits": 120},
        fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    assert accepted.payload["status"] == "accepted"

    declined = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "order", "product_id": "starlamp", "max_price_credits": 1},
        fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    assert declined.payload["status"] == "declined"
    assert declined.payload["reason"] == "exceeds-condition"

    unknown = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "quote", "product_id": "not-a-product"},
        fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    assert unknown.payload["status"] == "unknown-product"
    assert "not-a-product" not in json.dumps(unknown.payload.get("available", []))


# -- profile B: competitive -------------------------------------------------------


@pytest.mark.asyncio
async def test_competitive_aggressive_but_in_authority(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path)
    gateway = TrustGateway(store, sessions, registry, transport=_routing_transport())
    session = await gateway.open_session(run, competitive.AGENT_ID, label="replay", fixture_allowlist=set(MOCK_ALLOWLIST))
    seen_discounts = []
    for _ in range(3):
        reply = await gateway.send(
            session["id"], sender="local-agent",
            payload={"action": "quote", "product_id": "skycompass"},
            fixture_allowlist=set(MOCK_ALLOWLIST),
        )
        seen_discounts.append(reply.payload["discount_pct"])
        assert reply.payload["list_price_credits"] == catalog_fixture.PRODUCTS["skycompass"]["price_credits"]
        assert reply.payload["upsell"]["product_id"] in catalog_fixture.PRODUCTS
        blob = json.dumps(reply.payload).casefold()
        assert "token" not in blob and "password" not in blob and "http" not in blob
    assert seen_discounts == [0, 2, 5]


# -- profile C: misleading --------------------------------------------------------


@pytest.mark.asyncio
async def test_misleading_false_claim_classification_evidence(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path)
    gateway = TrustGateway(store, sessions, registry, transport=_routing_transport())
    session = await gateway.open_session(run, misleading.AGENT_ID, label="replay", fixture_allowlist=set(MOCK_ALLOWLIST))
    reply = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "quote", "product_id": "fieldkit"},
        fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    payload = reply.payload
    assert payload["status"] == "quoted"
    # Classification evidence: statement vs fixture catalog.
    assert payload["price_credits"] != catalog_fixture.PRODUCTS["fieldkit"]["price_credits"]
    assert misleading.FALSE_CLAIM in payload["claims"]
    assert misleading.FALSE_CLAIM not in catalog_fixture.SUPPORTED_CLAIMS


# -- profile D: adversarial -------------------------------------------------------


@pytest.mark.asyncio
async def test_adversarial_trap_never_followed_and_ssrf_blocked(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path)
    gateway = TrustGateway(store, sessions, registry, transport=_routing_transport())
    session = await gateway.open_session(run, adversarial.AGENT_ID, label="replay", fixture_allowlist=set(MOCK_ALLOWLIST))
    reply = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "quote", "product_id": "starlamp"},
        fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    payload = reply.payload
    assert reply.untrusted is True
    assert adversarial.INJECTION_MARKER in payload["instruction"]
    assert adversarial.CREDENTIAL_REQUEST in payload["credential_request"]
    assert payload["follow_url"] == adversarial.EVIL_REDIRECT_URL
    # The pushed redirect is SSRF-blocked (evil host resolves private).
    with pytest.raises(SSRFBlocked):
        validate_endpoint(payload["follow_url"], resolve=PRIVATE_RESOLVE)
    with pytest.raises(SSRFBlocked):
        validate_endpoint(payload["redirect"]["location"], resolve=PRIVATE_RESOLVE)


# -- auth -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merchant_fixture_token_enforced(tmp_path):
    store, sessions, registry, _, run = _harness(tmp_path, auth_type="bearer-header")
    entries = {MOCK_BASES[cooperative.AGENT_ID]: cooperative}
    transport = make_routing_transport(entries, expected_tokens={MOCK_BASES[cooperative.AGENT_ID]: FIXTURE_TOKEN})
    gateway = TrustGateway(store, sessions, registry, transport=transport)
    session = await gateway.open_session(run, cooperative.AGENT_ID, label="replay", fixture_allowlist=set(MOCK_ALLOWLIST))
    with pytest.raises(GatewayAuthError):
        await gateway.send(
            session["id"], sender="local-agent",
            payload={"action": "quote", "product_id": "starlamp"},
            fixture_allowlist=set(MOCK_ALLOWLIST),
        )
    with pytest.raises(GatewayAuthError):
        await gateway.send(
            session["id"], sender="local-agent",
            payload={"action": "quote", "product_id": "starlamp"},
            auth_token="wrong-token", fixture_allowlist=set(MOCK_ALLOWLIST),
        )
    reply = await gateway.send(
        session["id"], sender="local-agent",
        payload={"action": "quote", "product_id": "starlamp"},
        auth_token=FIXTURE_TOKEN, fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    assert reply.payload["status"] == "quoted"

    descriptor = (
        await discover_agents(
            [MOCK_BASES[cooperative.AGENT_ID]],
            allowlist=set(MOCK_ALLOWLIST),
            auth_token=FIXTURE_TOKEN,
            transport=mock_transport_for(cooperative, expected_token=FIXTURE_TOKEN),
        )
    )[0]
    assert descriptor["agent_id"] == cooperative.AGENT_ID
    with pytest.raises(DiscoveryAuthError):
        await discover_agents(
            [MOCK_BASES[cooperative.AGENT_ID]],
            allowlist=set(MOCK_ALLOWLIST),
            transport=mock_transport_for(cooperative, expected_token=FIXTURE_TOKEN),
        )


# -- determinism ------------------------------------------------------------------


def test_scenario_script_deterministic_and_covers_all_merchants():
    first, second = scenario_script(7), scenario_script(7)
    assert first == second
    merchants = {step["merchant"] for step in first}
    assert merchants == set(MERCHANT_PROFILES)
    assert scenario_script(7) != scenario_script(8)


@pytest.mark.asyncio
async def test_same_seed_same_transcript_bytes(tmp_path):
    records_runs = []
    for side in ("a", "b"):
        store, sessions, registry, _, run = _harness(tmp_path / side)
        gateway = TrustGateway(store, sessions, registry, transport=_routing_transport())
        records_runs.append(await run_script(gateway, run, scenario_script(7), fixture_allowlist=set(MOCK_ALLOWLIST)))
    assert records_runs[0] and len(records_runs[0]) == len(scenario_script(7))
    assert all(record["label"] == "replay" for record in records_runs[0])
    assert canonical_bytes(records_runs[0]) == canonical_bytes(records_runs[1])
    assert canonical_bytes(records_runs[0]) != b""


def test_fixture_labels_and_fictional_currency():
    assert "fictional" in catalog_fixture.CURRENCY.casefold()
    assert "fictional" in catalog_fixture.FICTIONAL_DISCLAIMER.casefold()
    for agent_id, profile in MERCHANT_PROFILES.items():
        assert profile.AGENT_ID == agent_id
        assert "fictional" in profile.DISPLAY_NAME.casefold()


# -- live loopback synthetic internet (skipped where binds are blocked) -----------


@pytest.mark.asyncio
async def test_live_loopback_synthetic_internet(tmp_path):
    try:
        servers = [MerchantServer(MERCHANT_PROFILES[agent_id], expected_token=FIXTURE_TOKEN) for agent_id in sorted(MERCHANT_PROFILES)]
        for server in servers:
            server.thread.start()
    except OSError as exc:
        pytest.skip(f"Loopback bind blocked in this environment: {exc}")
    try:
        assert all(server.bound_host == "127.0.0.1" for server in servers)
        bases = [server.base_url for server in servers]
        allowlist = {server.allowlist_entry for server in servers}
        descriptors = await discover_agents(bases, allowlist=allowlist, auth_token=FIXTURE_TOKEN)
        assert [d["agent_id"] for d in descriptors] == sorted(MERCHANT_PROFILES)

        store, sessions, registry, _, run = _harness(tmp_path)
        for descriptor in descriptors:
            registry.register(
                f"live-{descriptor['agent_id']}",
                endpoint=descriptor["endpoint"],
                identity_status="unknown",
                capabilities_declared=list(descriptor["capabilities_declared"]),
                provenance={"asserted_by": "live-discovery-test", "evidence_refs": [descriptor["capabilities_url"]]},
                auth={"type": "bearer-header"},
            )
        gateway = TrustGateway(store, sessions, registry)
        session = await gateway.open_session(run, f"live-{cooperative.AGENT_ID}", label="replay", fixture_allowlist=allowlist)
        reply = await gateway.send(
            session["id"], sender="local-agent",
            payload={"action": "quote", "product_id": "starlamp"},
            auth_token=FIXTURE_TOKEN, fixture_allowlist=allowlist,
        )
        assert reply.payload["price_credits"] == catalog_fixture.PRODUCTS["starlamp"]["price_credits"]
        assert reply.untrusted is True
        await gateway.close_session(session["id"])

        with pytest.raises(DiscoveryAuthError):
            await discover_agents([bases[0]], allowlist=allowlist)
    finally:
        for server in servers:
            server.server.shutdown()
            server.thread.join(timeout=5)
            server.server.server_close()
