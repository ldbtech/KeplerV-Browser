"""Deterministic multi-round scenario scripts (Phase 6 M3).

Scripts are pure data derived from a seed (seeded product order, fixed
steps across all four merchants). :func:`run_script` drives a script
through the Trust Gateway with ``replay``-labeled sessions, and
:func:`canonical_bytes` reduces the collected records to stable bytes
(excluding ids/timestamps) for determinism comparisons.
"""

from __future__ import annotations

import json
import random
from typing import Any

from runtime.merchants import adversarial, competitive, cooperative, misleading
from runtime.merchants import catalog_fixture

MERCHANT_ORDER = (
    cooperative.AGENT_ID,
    competitive.AGENT_ID,
    misleading.AGENT_ID,
)


def scenario_script(seed: int) -> list[dict[str, Any]]:
    """Seeded deterministic script: quotes everywhere, conditions, probe."""
    rng = random.Random(seed)
    products = catalog_fixture.product_ids()
    rng.shuffle(products)
    steps: list[dict[str, Any]] = []
    for product_id in products:
        for agent_id in MERCHANT_ORDER:
            steps.append({"merchant": agent_id, "payload": {"action": "quote", "product_id": product_id}})
    first = products[0]
    fair_price = int(catalog_fixture.PRODUCTS[first]["price_credits"])
    steps.append({
        "merchant": cooperative.AGENT_ID,
        "payload": {"action": "order", "product_id": first, "max_price_credits": fair_price},
    })
    steps.append({
        "merchant": cooperative.AGENT_ID,
        "payload": {"action": "order", "product_id": first, "max_price_credits": 1},
    })
    steps.append({
        "merchant": adversarial.AGENT_ID,
        "payload": {"action": "quote", "product_id": first},
    })
    return steps


def canonical_bytes(records: list[dict[str, Any]]) -> bytes:
    """Stable bytes for determinism comparison (no ids, no timestamps)."""
    slim = [
        {
            "merchant": record["merchant"],
            "request": record["request"],
            "response": record["response"],
            "label": record["label"],
        }
        for record in records
    ]
    return json.dumps(slim, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def run_script(
    gateway: Any,
    run: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    sender: str = "local-agent",
    fixture_allowlist: set[str] | None = None,
    auth_tokens: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run steps through the gateway; one ``replay`` session per merchant."""
    allowlist = fixture_allowlist or set()
    tokens = auth_tokens or {}
    sessions: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    try:
        for step in steps:
            merchant = step["merchant"]
            if merchant not in sessions:
                session = await gateway.open_session(run, merchant, label="replay", fixture_allowlist=allowlist)
                sessions[merchant] = session["id"]
            inbound = await gateway.send(
                sessions[merchant],
                sender=sender,
                payload=step["payload"],
                auth_token=tokens.get(merchant),
                fixture_allowlist=allowlist,
            )
            records.append({
                "merchant": merchant,
                "request": dict(step["payload"]),
                "response": dict(inbound.payload),
                "label": inbound.label,
            })
    finally:
        for session_id in sessions.values():
            try:
                await gateway.close_session(session_id)
            except Exception:
                pass
    return records
