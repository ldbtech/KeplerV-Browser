"""Phase 6 final demo: committed end-to-end federation test.

Shopping goal -> discover 2 merchants -> gateway offers -> legit branch
completes while the unsupported branch is stopped -> unauthorized branch is
denied by AIP -> Guard investigates -> catalog comparison -> findings ->
everything still inspectable after a restart.

Fake models only: socket-free fixture transports, ``replay`` sessions,
fictional marketplace data, deterministic fake assertions (no live model).
No live network, no keychain access.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.gateway.registry import ExternalAgentRegistry
from runtime.gateway.sessions import GatewaySessionStore
from runtime.guard.external_investigation import (
    investigate_external_exchange,
    record_investigation,
)
from runtime.merchants import (
    MERCHANT_PROFILES,
    cooperative,
    misleading,
)
from runtime.merchants import catalog_fixture, trusted_catalog
from runtime.merchants.base import make_routing_transport
from runtime.scheduler import TOOL_NAMES, RuntimeService
from runtime.store import RuntimeStore

COOP = cooperative.AGENT_ID
MISLEADING = misleading.AGENT_ID

MOCK_BASES = {
    COOP: "http://127.0.0.1:18291",
    MISLEADING: "http://127.0.0.1:18292",
}
MOCK_ALLOWLIST = {"127.0.0.1:18291", "127.0.0.1:18292"}

PRODUCT = "starlamp"
CAP = 200


def _harness(tmp_path, permissions: list[str]):
    db_path = tmp_path / "store" / "runtime.sqlite3"
    store = RuntimeStore(db_path=db_path)
    entries = {base: MERCHANT_PROFILES[agent] for agent, base in MOCK_BASES.items()}
    service = RuntimeService(
        store=store,
        gateway_transport=make_routing_transport(entries),
        gateway_fixture_allowlist=set(MOCK_ALLOWLIST),
    )
    run = store.create_run(
        {
            "goal_id": "goal-demo-shop",
            "goal_title": "Buy a fictional starlamp",
            "goal_description": (
                "Fictional shopping goal: compare two fixture merchants and buy "
                "a starlamp within 200 fictional credits. No real purchase exists."
            ),
            "task": "Negotiate a fictional starlamp within cap.",
            "source_url": "", "step_budget": 16, "token_budget": 16000,
        },
        "meta", "fake-model", TOOL_NAMES, permissions,
    )
    run = store.get_run(run["id"])
    service._pause_gates.setdefault(run["id"], asyncio.Event()).set()
    return service, store, run, db_path


async def _execute_approved(service, run, name, args, call_id):
    """Run a tool through dispatch, approving when AIP asks."""
    task = asyncio.create_task(service._execute_tool(run, None, name, args, call_id))
    for _ in range(200):
        if (run["id"], call_id) in service._pending_approvals:
            await service.control(run["id"], "approve", call_id)
            break
        if task.done():
            break
        await asyncio.sleep(0.02)
    return await task


def _bearer_headers() -> dict[str, str]:
    import os

    return {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}


def test_phase6_demo_shopping_end_to_end(tmp_path):
    async def _body():
        service, store, run, db_path = _harness(
            tmp_path, ["gateway.read", "gateway.send", "gateway.verify"])
        run_id = run["id"]

        # -- 1. shopping goal exists with a fictional cap -------------------
        assert run["goal_id"] == "goal-demo-shop"
        assert trusted_catalog.get_trusted_product(PRODUCT) is not None

        # -- 2. discover exactly the 2 configured merchants -----------------
        discovered = await service._execute_tool(
            run, None, "gateway.discover",
            {"bases": [MOCK_BASES[COOP], MOCK_BASES[MISLEADING]]}, "call-discover")
        assert discovered["ok"] is True, discovered
        found = {item["agent_id"] for item in discovered["agents"]}
        assert found == {COOP, MISLEADING}
        assert all(item["fixture"] is True for item in discovered["agents"])
        assert all(item["surface_label"] == "fake" for item in discovered["agents"])
        assert discovered["content_is_untrusted"] is True

        # -- 3a. legit branch: cooperative quote -> verify -> order ---------
        opened = await _execute_approved(service, run, "gateway.open_session", {
            "external_agent_id": COOP, "label": "replay", "max_messages": 12,
        }, "call-open-coop")
        assert opened["ok"] is True, opened
        assert opened["label"] == "replay"
        coop_session = opened["session_id"]

        quote = await _execute_approved(service, run, "gateway.send", {
            "session_id": coop_session,
            "payload": {"action": "quote", "product_id": PRODUCT},
        }, "call-quote-coop")
        assert quote["ok"] is True, quote
        assert quote["payload"]["status"] == "quoted"
        coop_price = quote["payload"]["price_credits"]
        assert coop_price == catalog_fixture.get_product(PRODUCT)["price_credits"]

        checked = await _execute_approved(service, run, "gateway.verify", {
            "session_id": coop_session, "product_id": PRODUCT,
            "asserted_price_credits": coop_price}, "call-verify-coop")
        assert checked["ok"] is True, checked
        assert checked["verdicts"][0]["verdict"] == "independently-observed"

        order = await _execute_approved(service, run, "gateway.send", {
            "session_id": coop_session,
            "payload": {"action": "order", "product_id": PRODUCT,
                        "quantity": 1, "max_price_credits": CAP},
        }, "call-order-coop")
        assert order["ok"] is True, order
        assert order["payload"]["status"] == "accepted"

        # -- 3b. unsupported branch: misleading quote is stopped ------------
        opened_bad = await _execute_approved(service, run, "gateway.open_session", {
            "external_agent_id": MISLEADING, "label": "replay", "max_messages": 12,
        }, "call-open-mis")
        assert opened_bad["ok"] is True, opened_bad
        bad_session = opened_bad["session_id"]

        bad_quote = await _execute_approved(service, run, "gateway.send", {
            "session_id": bad_session,
            "payload": {"action": "quote", "product_id": PRODUCT},
        }, "call-quote-mis")
        assert bad_quote["ok"] is True, bad_quote
        bad_price = bad_quote["payload"]["price_credits"]
        assert bad_price != catalog_fixture.get_product(PRODUCT)["price_credits"]

        bad_checked = await _execute_approved(service, run, "gateway.verify", {
            "session_id": bad_session, "product_id": PRODUCT,
            "asserted_price_credits": bad_price,
            "claim_text": misleading.FALSE_CLAIM}, "call-verify-mis")
        assert bad_checked["ok"] is True, bad_checked
        bad_verdicts = {item["verdict"] for item in bad_checked["verdicts"]}
        assert "unverified" in bad_verdicts
        # Unsupported claims stop this branch: no order is ever sent.
        tools = service._gateway_tools()
        assert tools.gateway._sessions.get_session(bad_session)["messages_sent"] == 1

        await service._execute_tool(
            run, None, "gateway.close_session",
            {"session_id": coop_session}, "call-close-coop")
        await service._execute_tool(
            run, None, "gateway.close_session",
            {"session_id": bad_session}, "call-close-mis")

        # -- 4. unauthorized branch: AIP denies, Guard opens a case ---------
        denied_run = store.create_run(
            {
                "goal_id": "goal-demo-shop", "goal_title": "Buy a fictional starlamp",
                "goal_description": "Ungranted attempt for the AIP-deny branch.",
                "task": "Attempt gateway.send without the grant.",
                "source_url": "", "step_budget": 4, "token_budget": 4000,
            },
            "meta", "fake-model", TOOL_NAMES, ["gateway.read"],
        )
        denied_run = store.get_run(denied_run["id"])
        service._pause_gates.setdefault(denied_run["id"], asyncio.Event()).set()
        denied = await service._execute_tool(
            denied_run, None, "gateway.send",
            {"session_id": bad_session, "payload": {"action": "quote"}},
            "call-denied")
        assert denied["ok"] is False
        assert "gateway.send" in denied["error"]
        deny_events = [event for event in store.list_run_events(denied_run["id"])
                       if event["type"] in {"aip.decision", "service.access_denied"}]
        assert any(event["type"] == "aip.decision"
                   and event["payload"].get("outcome") == "deny" for event in deny_events)
        assert any(event["type"] == "service.access_denied" for event in deny_events)
        deny_case = store.get_active_guard_case_for_run(denied_run["id"])
        assert deny_case is not None
        assert deny_case["signal"] == "service_access_denied"

        # Data minimization still refuses secret-bearing outbound loudly.
        refused = await _execute_approved(service, run, "gateway.open_session", {
            "external_agent_id": COOP, "label": "replay"}, "call-open-refuse")
        leak = await _execute_approved(service, run, "gateway.send", {
            "session_id": refused["session_id"],
            "payload": {"action": "order", "product_id": PRODUCT,
                        "account_token": "must-never-send"},
        }, "call-leak")
        assert leak["ok"] is False
        assert "account_token" in leak["error"]
        assert "must-never-send" not in leak["error"]
        await service._execute_tool(
            run, None, "gateway.close_session",
            {"session_id": refused["session_id"]}, "call-close-refuse")

        # -- 5. Guard investigates the unsupported exchange -----------------
        sessions = tools.gateway._sessions
        bad_transcript = sessions.list_transcript(bad_session)
        assert len(bad_transcript) >= 2
        assert all(row["label"] == "replay" for row in bad_transcript)
        assert any(row["untrusted"] for row in bad_transcript
                   if row["direction"] == "inbound")
        report = investigate_external_exchange(
            session=sessions.get_session(bad_session),
            transcript=bad_transcript,
            ledger_events=store.list_run_events(run_id),
        )
        assert report["classification"]["classification"] in {
            "misleading", "unsupported-claim"}
        kinds = {item["kind"] for item in report["contradictions"]}
        assert "catalog-price-conflict" in kinds
        assert "catalog-claim-unsupported" in kinds
        assert report["internal_reasoning"].startswith("unknown")
        for follow_up in report["follow_up_questions"]:
            assert follow_up["issue_via"] == "gateway.send"
        record_investigation(store, run, report,
                             guard_observer=service.guard_observer)
        run_events = store.list_run_events(run_id)
        assert any(event["type"] == "gateway.external_investigation"
                   for event in run_events)
        assert any(event["type"] == "gateway.external_contradiction"
                   for event in run_events)
        contra_case = store.get_active_guard_case_for_run(run_id)
        assert contra_case is not None
        assert contra_case["signal"] == "external_contradiction"

        # The legit exchange classifies clean through the same pipeline.
        coop_report = investigate_external_exchange(
            session=sessions.get_session(coop_session),
            transcript=sessions.list_transcript(coop_session),
            ledger_events=store.list_run_events(run_id),
        )
        assert coop_report["classification"]["classification"] == "legitimate-negotiation"
        assert coop_report["contradictions"] == []

        # -- 6. catalog comparison + findings --------------------------------
        assert trusted_catalog.check_price(PRODUCT, coop_price)["outcome"] == "match"
        assert trusted_catalog.check_price(PRODUCT, bad_price)["outcome"] == "conflict"
        assert trusted_catalog.supports_claim(misleading.FALSE_CLAIM) is False
        assert trusted_catalog.supports_claim("ships within the fictional bazaar") is True
        store.add_evidence(
            run,
            finding=(f"Legit offer: {COOP} quoted {coop_price} fictional credits for "
                     f"{PRODUCT} (catalog match); fictional order accepted."),
            quote=f"status=accepted product={PRODUCT} total={coop_price} (replay fixture)",
            source_url=f"fixture:gateway-session:{coop_session}",
        )
        store.add_evidence(
            run,
            finding=(f"Flagged offer: {MISLEADING} asserted {bad_price} fictional credits "
                     f"plus an unsupported warranty claim; branch stopped, no order sent."),
            quote=f"verdict=unverified contradictions={sorted(kinds)} (replay fixture)",
            source_url=f"fixture:gateway-session:{bad_session}",
        )
        assert len(store.list_evidence("goal-demo-shop")) == 2

        return {
            "run_id": run_id, "db_path": str(db_path),
            "coop_session": coop_session, "bad_session": bad_session,
        }

    ids = asyncio.run(_body())

    # -- 7. restart: everything stays inspectable from storage ---------------
    reopened = RuntimeStore(db_path=ids["db_path"])
    assert reopened.get_run(ids["run_id"]) is not None
    gateway_store = GatewaySessionStore(ids["db_path"])
    coop = gateway_store.get_session(ids["coop_session"])
    bad = gateway_store.get_session(ids["bad_session"])
    assert coop is not None and bad is not None
    assert coop["status"] == "closed" and bad["status"] == "closed"
    assert len(gateway_store.list_transcript(ids["coop_session"])) >= 4
    assert len(gateway_store.list_transcript(ids["bad_session"])) >= 2
    types = {event["type"] for event in reopened.list_run_events(ids["run_id"])}
    for expected in ("gateway.session.opened", "gateway.session.closed",
                     "gateway.verify.completed",
                     "gateway.external_investigation",
                     "gateway.external_contradiction"):
        assert expected in types, types
    assert len(reopened.list_evidence("goal-demo-shop")) == 2
    assert reopened.get_active_guard_case_for_run(ids["run_id"]) is not None
    assert ExternalAgentRegistry().get(COOP) is not None
    assert ExternalAgentRegistry().get(MISLEADING) is not None

    # The read-only verification route serves the reloaded transcript.
    service = RuntimeService(store=reopened)
    with TestClient(create_app(service)) as client:
        response = client.get(
            f"/gateway/sessions/{ids['bad_session']}/verification",
            headers=_bearer_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["classification"]["classification"] in {
            "misleading", "unsupported-claim"}
        assert any(item["verdict"] == "unverified" for item in body["verdicts"])
