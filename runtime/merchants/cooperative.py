"""Merchant A — cooperative fixture (Phase 6 M3).

Accurate quotes from the fixture catalog; respects order conditions
(``max_price_credits``); never invents products. All data fictional.
"""

from __future__ import annotations

from typing import Any

from runtime.merchants import catalog_fixture

AGENT_ID = "merchant-cooperative"
DISPLAY_NAME = "Fictional Co-op Corner (demo merchant A)"
IDENTITY_STATUS = "self_declared"
CAPABILITIES = ["catalog.quote", "order.conditions", "order.place"]

HISTORY = [
    {
        "with": "fictional-local-agent",
        "summary": "Quoted the fictional Starlamp Lantern at the catalog price.",
        "outcome": "completed",
    },
    {
        "with": "fictional-local-agent",
        "summary": "Declined a fictional order whose condition exceeded the catalog price.",
        "outcome": "declined-condition",
    },
    {
        "with": "fictional-auditor",
        "summary": "Served the fictional catalog descriptor for discovery.",
        "outcome": "completed",
    },
]


def handle_action(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    if action == "quote":
        return _quote(payload)
    if action == "order":
        return _order(payload)
    return {
        "status": "unsupported-action",
        "supported": ["quote", "order"],
        "message": "Fictional co-op fixture: unknown action.",
    }


def _quote(payload: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id", ""))
    product = catalog_fixture.get_product(product_id)
    if product is None:
        return {
            "status": "unknown-product",
            "product_id": product_id,
            "available": catalog_fixture.product_ids(),
            "message": "Fictional co-op fixture: no such product; nothing invented.",
        }
    return {
        "status": "quoted",
        "product_id": product_id,
        "name": product["name"],
        "price_credits": product["price_credits"],
        "currency": catalog_fixture.CURRENCY,
        "stock": product["stock"],
        "marketplace": catalog_fixture.MARKETPLACE_NAME,
        "message": "Fictional quote at the catalog price; no real purchase.",
    }


def _order(payload: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id", ""))
    product = catalog_fixture.get_product(product_id)
    if product is None:
        return {
            "status": "unknown-product",
            "product_id": product_id,
            "available": catalog_fixture.product_ids(),
            "message": "Fictional co-op fixture: no such product; nothing invented.",
        }
    try:
        quantity = max(1, int(payload.get("quantity", 1)))
    except (TypeError, ValueError):
        quantity = 1
    total = int(product["price_credits"]) * quantity
    max_price = payload.get("max_price_credits")
    if max_price is not None:
        try:
            limit = int(max_price)
        except (TypeError, ValueError):
            limit = total
        if total > limit:
            return {
                "status": "declined",
                "reason": "exceeds-condition",
                "product_id": product_id,
                "total_credits": total,
                "max_price_credits": limit,
                "currency": catalog_fixture.CURRENCY,
                "message": "Fictional order declined: total exceeds your max-price condition.",
            }
    return {
        "status": "accepted",
        "product_id": product_id,
        "quantity": quantity,
        "total_credits": total,
        "currency": catalog_fixture.CURRENCY,
        "note": "Fictional order accepted; no real purchase or payment exists.",
    }
