"""Trusted product catalog fixture (Phase 6 M4).

The canonical authority that claim verification checks merchant statements
against. Product facts come from :mod:`runtime.merchants.catalog_fixture`
(single source of truth — no duplicated prices); this module adds:

- the integrity manifest (SHA-256 over canonical JSON),
- fixture-only HMAC attestations (``hmac``/``hashlib`` from the standard
  library — established primitives, no custom crypto).

Fictional marketplace only: made-up products, fictional credits, no real
purchases. The attestation key below is a hard-coded *fixture* key for
tests; it never touches the keychain and must never protect real data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from runtime.merchants import catalog_fixture

MARKETPLACE_NAME = catalog_fixture.MARKETPLACE_NAME
CURRENCY = catalog_fixture.CURRENCY
FICTIONAL_DISCLAIMER = catalog_fixture.FICTIONAL_DISCLAIMER

ATTESTATION_ALGORITHM = "hmac-sha256-fixture-v1"

#: Fixture-only attestation key. Hard-coded on purpose: it authenticates
#: fictional demo claims in tests, never real data, and never the keychain.
FIXTURE_ATTESTATION_KEY = b"keplerv-phase6-fixture-attestation-key-test-only-01"


def trusted_entries() -> dict[str, dict[str, Any]]:
    """Canonical trusted entries derived from the fixture catalog."""
    entries: dict[str, dict[str, Any]] = {}
    for product_id in catalog_fixture.product_ids():
        product = catalog_fixture.get_product(product_id) or {}
        entries[product_id] = {
            "product_id": product_id,
            "name": product.get("name"),
            "price_credits": product.get("price_credits"),
            "stock": product.get("stock"),
            "description": product.get("description"),
            "currency": CURRENCY,
            "marketplace": MARKETPLACE_NAME,
            "claims_supported": sorted(catalog_fixture.SUPPORTED_CLAIMS),
            "fixture": True,
            "surface_label": "fake",
        }
    return entries


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_digest(entries: dict[str, dict[str, Any]] | None = None) -> str:
    """SHA-256 manifest digest over the canonical trusted entries."""
    data = entries if entries is not None else trusted_entries()
    return hashlib.sha256(_canonical_bytes(data)).hexdigest()


def verify_manifest(entries: dict[str, Any], expected_digest: str) -> bool:
    """Check catalog integrity: recompute the manifest and compare digests."""
    if not isinstance(entries, dict) or not expected_digest:
        return False
    try:
        actual = hashlib.sha256(_canonical_bytes(entries)).hexdigest()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected_digest)


def get_trusted_product(product_id: str, entries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    data = entries if entries is not None else trusted_entries()
    product = data.get(product_id)
    return dict(product) if isinstance(product, dict) else None


def check_price(product_id: str, asserted_price: Any, entries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare an asserted price against the trusted catalog (no invention)."""
    product = get_trusted_product(product_id, entries)
    if product is None:
        return {"outcome": "unknown-product", "trusted_price": None}
    trusted_price = product.get("price_credits")
    try:
        asserted = int(asserted_price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"outcome": "not-a-price", "trusted_price": trusted_price}
    if isinstance(asserted_price, bool):
        return {"outcome": "not-a-price", "trusted_price": trusted_price}
    if asserted == trusted_price:
        return {"outcome": "match", "trusted_price": trusted_price}
    return {"outcome": "conflict", "trusted_price": trusted_price}


def supports_claim(text: str, entries: dict[str, dict[str, Any]] | None = None) -> bool:
    """Whether the trusted catalog supports a merchant's text claim."""
    if not isinstance(text, str) or not text.strip():
        return False
    data = entries if entries is not None else trusted_entries()
    for product in data.values():
        supported = product.get("claims_supported") or []
        if text.strip() in supported:
            return True
    return False


def attest_claim(canonical_claim: str, *, key: bytes = FIXTURE_ATTESTATION_KEY) -> dict[str, str]:
    """Create a fixture attestation blob for one canonical claim string.

    Fixture-only: proves the *mechanism* for tests. Real deployments must
    use a managed key — never this constant.
    """
    claim_bytes = canonical_claim.encode("utf-8")
    return {
        "algorithm": ATTESTATION_ALGORITHM,
        "claim_sha256": hashlib.sha256(claim_bytes).hexdigest(),
        "mac": hmac.new(key, claim_bytes, hashlib.sha256).hexdigest(),
        "fixture": "true",
    }


def verify_attestation(canonical_claim: str, attestation: Any, *, key: bytes = FIXTURE_ATTESTATION_KEY) -> bool:
    """Verify a fixture attestation blob over the exact claim string."""
    if not isinstance(canonical_claim, str) or not isinstance(attestation, dict):
        return False
    if attestation.get("algorithm") != ATTESTATION_ALGORITHM:
        return False
    claim_bytes = canonical_claim.encode("utf-8")
    if attestation.get("claim_sha256") != hashlib.sha256(claim_bytes).hexdigest():
        return False
    mac = attestation.get("mac")
    if not isinstance(mac, str) or not mac:
        return False
    expected = hmac.new(key, claim_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def canonical_price_claim(product_id: str, price_credits: int) -> str:
    return f"price:{product_id}:{int(price_credits)}:{CURRENCY}"


def canonical_text_claim(product_id: str, text: str) -> str:
    return f"text:{product_id}:{text.strip()}"
