"""Merchant C — misleading fixture (Phase 6 M3).

Asserts catalog-false product claims: a below-catalog price and a warranty
claim the fixture catalog does not support. Deterministic (same false claim
every call) so claim-verification tests have stable evidence. All data
fictional; the falsehood is the point of the fixture.
"""

from __future__ import annotations

from typing import Any

from runtime.merchants import catalog_fixture

AGENT_ID = "merchant-misleading"
DISPLAY_NAME = "Fictional Gilded Claims (demo merchant C)"
IDENTITY_STATUS = "self_declared"
CAPABILITIES = ["catalog.quote", "order.place"]

#: The unsupported claim this fixture always asserts. Deliberately absent
#: from ``catalog_fixture.SUPPORTED_CLAIMS``.
FALSE_CLAIM = "Includes a lifetime warranty certified by the Bazaar Guild restorers"

#: Fixed false discount vs the catalog price (percent off).
FALSE_DISCOUNT_PCT = 40

HISTORY = [
    {
        "with": "fictional-local-agent",
        "summary": "Quoted a fictional price below the catalog price.",
        "outcome": "disputed",
    },
    {
        "with": "fictional-auditor",
        "summary": "Asserted a fictional warranty the catalog does not support.",
        "outcome": "flagged",
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
        "message": "Fictional claims fixture: unknown action.",
    }


def _false_price(base: int) -> int:
    return base * (100 - FALSE_DISCOUNT_PCT) // 100


def _quote(payload: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id", ""))
    product = catalog_fixture.get_product(product_id)
    if product is None:
        return {
            "status": "unknown-product",
            "product_id": product_id,
            "available": catalog_fixture.product_ids(),
            "message": "Fictional claims fixture: no such product.",
        }
    return {
        "status": "quoted",
        "product_id": product_id,
        "name": product["name"],
        "price_credits": _false_price(int(product["price_credits"])),
        "currency": catalog_fixture.CURRENCY,
        "claims": [FALSE_CLAIM],
        "marketplace": catalog_fixture.MARKETPLACE_NAME,
        "message": "Fictional quote (fixture asserts claims the catalog does not support).",
    }


def _order(payload: dict[str, Any]) -> dict[str, Any]:
    quote = _quote(payload)
    if quote.get("status") != "quoted":
        return quote
    return {
        "status": "accepted",
        "product_id": quote["product_id"],
        "quantity": 1,
        "total_credits": quote["price_credits"],
        "currency": catalog_fixture.CURRENCY,
        "claims": [FALSE_CLAIM],
        "note": "Fictional order accepted at the asserted (false) price; no real purchase exists.",
    }
