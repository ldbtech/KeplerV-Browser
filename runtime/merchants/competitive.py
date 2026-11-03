"""Merchant B — competitive fixture (Phase 6 M3).

Aggressive but in-authority: accurate base prices with a deterministic
discount ladder and a fictional upsell. Never requests credentials, never
points at external URLs, never exceeds the quote/order authority. All data
fictional.
"""

from __future__ import annotations

from typing import Any

from runtime.merchants import catalog_fixture

AGENT_ID = "merchant-competitive"
DISPLAY_NAME = "Fictional Bargain Blitz (demo merchant B)"
IDENTITY_STATUS = "self_declared"
CAPABILITIES = ["catalog.quote", "offer.discount", "offer.upsell"]

#: Deterministic discount ladder by call index (percent off, capped).
DISCOUNT_LADDER = (0, 2, 5)

HISTORY = [
    {
        "with": "fictional-local-agent",
        "summary": "Opened with a fictional list-price quote, then a 2% counter-offer.",
        "outcome": "negotiating",
    },
    {
        "with": "fictional-local-agent",
        "summary": "Offered a fictional 5% close-out discount with an upsell bundle.",
        "outcome": "completed",
    },
    {
        "with": "fictional-local-agent",
        "summary": "Accepted a fictional haggle that stayed inside the ladder cap.",
        "outcome": "completed",
    },
]


def handle_action(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    if action == "quote":
        return _quote(payload, ctx)
    if action == "order":
        return _order(payload, ctx)
    return {
        "status": "unsupported-action",
        "supported": ["quote", "order"],
        "message": "Fictional bargain fixture: unknown action.",
    }


def _ladder_pct(call_index: int) -> int:
    return DISCOUNT_LADDER[min(max(call_index, 0), len(DISCOUNT_LADDER) - 1)]


def _upsell_for(product_id: str) -> dict[str, Any]:
    ids = catalog_fixture.product_ids()
    nxt = ids[(ids.index(product_id) + 1) % len(ids)] if product_id in ids else ids[0]
    product = catalog_fixture.get_product(nxt) or {}
    return {"product_id": nxt, "name": product.get("name"), "price_credits": product.get("price_credits")}


def _quote(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id", ""))
    product = catalog_fixture.get_product(product_id)
    if product is None:
        return {
            "status": "unknown-product",
            "product_id": product_id,
            "available": catalog_fixture.product_ids(),
            "message": "Fictional bargain fixture: no such product; nothing invented.",
        }
    pct = _ladder_pct(int(ctx.get("call_index", 0)))
    base = int(product["price_credits"])
    return {
        "status": "quoted",
        "product_id": product_id,
        "name": product["name"],
        "list_price_credits": base,
        "discount_pct": pct,
        "price_credits": base * (100 - pct) // 100,
        "currency": catalog_fixture.CURRENCY,
        "upsell": _upsell_for(product_id),
        "pressure": "Fictional limited-time offer (demo): price valid while this scripted round lasts.",
        "marketplace": catalog_fixture.MARKETPLACE_NAME,
        "message": "Fictional competitive quote; haggling stays inside the posted ladder.",
    }


def _order(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id", ""))
    product = catalog_fixture.get_product(product_id)
    if product is None:
        return {
            "status": "unknown-product",
            "product_id": product_id,
            "available": catalog_fixture.product_ids(),
            "message": "Fictional bargain fixture: no such product; nothing invented.",
        }
    pct = _ladder_pct(int(ctx.get("call_index", 0)))
    base = int(product["price_credits"])
    total = base * (100 - pct) // 100
    return {
        "status": "accepted",
        "product_id": product_id,
        "quantity": 1,
        "discount_pct": pct,
        "total_credits": total,
        "currency": catalog_fixture.CURRENCY,
        "note": "Fictional order accepted at the ladder price; no real purchase exists.",
    }
