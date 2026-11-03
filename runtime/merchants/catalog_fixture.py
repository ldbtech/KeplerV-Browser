"""Fictional marketplace fixture data (Phase 6 M3).

Made-up marketplace, made-up products, fictional currency. The canonical
trusted catalog lives with M4; this module holds the small fixture the M3
merchant scripts quote from (or, for the misleading profile, lie about).
"""

from __future__ import annotations

MARKETPLACE_NAME = "Fictional KeplerV Bazaar (demo)"

CURRENCY = "fictional KeplerV credits (KVC — demo only, no monetary value)"

FICTIONAL_DISCLAIMER = (
    "Fictional demo data only: no real products, funds, or purchases exist "
    "behind this marketplace."
)

PRODUCTS: dict[str, dict[str, object]] = {
    "starlamp": {
        "name": "Starlamp Lantern",
        "price_credits": 120,
        "stock": 14,
        "description": "A fictional hand-lantern with a painted star shade.",
    },
    "skycompass": {
        "name": "Sky Compass",
        "price_credits": 85,
        "stock": 30,
        "description": "A fictional brass compass that points at storybook north.",
    },
    "fieldkit": {
        "name": "Field Survey Kit",
        "price_credits": 210,
        "stock": 6,
        "description": "A fictional canvas roll with toy survey tools.",
    },
}

#: Claims the fixture catalog actually supports. Anything else a merchant
#: asserts (e.g. the misleading profile's warranty claim) is unsupported.
SUPPORTED_CLAIMS = frozenset({
    "ships within the fictional bazaar",
    "30-day fictional return window",
})


def product_ids() -> list[str]:
    return sorted(PRODUCTS)


def get_product(product_id: str) -> dict[str, object] | None:
    product = PRODUCTS.get(product_id)
    return dict(product) if product is not None else None
