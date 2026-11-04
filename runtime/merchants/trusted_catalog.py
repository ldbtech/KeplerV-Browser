"""Trusted product catalog fixture (Phase 6 M4).

The canonical authority that claim verification checks merchant statements
against. Product facts come from :mod:`runtime.merchants.catalog_fixture`
(single source of truth — no duplicated prices); this module adds:

- the integrity manifest (SHA-256 over canonical JSON),
- Ed25519 claim attestations (``cryptography`` package — established
  primitives, no custom crypto).

Fictional marketplace only: made-up products, fictional credits, no real
purchases. The catalog signing key is generated in memory per process
(``rotate_catalog_key`` for rotation); production deployments must hold
it in the keychain ``federation-identity`` namespace instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

from runtime.federation import keys as identity_keys
from runtime.merchants import catalog_fixture

MARKETPLACE_NAME = catalog_fixture.MARKETPLACE_NAME
CURRENCY = catalog_fixture.CURRENCY
FICTIONAL_DISCLAIMER = catalog_fixture.FICTIONAL_DISCLAIMER

ATTESTATION_ALGORITHM = "ed25519-v1"

#: In-memory catalog signing key (current key id + private key) with
#: retained previous public keys so pre-rotation blobs still verify.
_CATALOG_KEY_ID: str | None = None
_CATALOG_PRIVATE_KEY = None
_CATALOG_PREVIOUS_KEYS: dict[str, bytes] = {}


def _current_catalog_key():
    """Return the (key_id, private_key), generating the process key lazily."""
    global _CATALOG_KEY_ID, _CATALOG_PRIVATE_KEY
    if _CATALOG_KEY_ID is None or _CATALOG_PRIVATE_KEY is None:
        _CATALOG_KEY_ID = f"catalog-{uuid4().hex[:12]}"
        _CATALOG_PRIVATE_KEY = identity_keys.generate_private_key()
    return _CATALOG_KEY_ID, _CATALOG_PRIVATE_KEY


def catalog_public_key(*, key_id: str | None = None) -> bytes | None:
    """Return the catalog verification key (current or retained previous)."""
    current_id, current_private = _current_catalog_key()
    if key_id is None or key_id == current_id:
        return identity_keys.public_bytes_from_private(current_private)
    previous = _CATALOG_PREVIOUS_KEYS.get(str(key_id))
    return bytes(previous) if previous is not None else None


def rotate_catalog_key() -> dict[str, str]:
    """Rotate the in-memory catalog signing key; previous blobs still verify."""
    global _CATALOG_KEY_ID, _CATALOG_PRIVATE_KEY
    current_id, current_private = _current_catalog_key()
    _CATALOG_PREVIOUS_KEYS[current_id] = identity_keys.public_bytes_from_private(
        current_private
    )
    _CATALOG_KEY_ID = f"catalog-{uuid4().hex[:12]}"
    _CATALOG_PRIVATE_KEY = identity_keys.generate_private_key()
    return {"key_id": _CATALOG_KEY_ID, "previous_key_id": current_id}


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


def attest_claim(
    canonical_claim: str,
    *,
    seed: bytes | None = None,
    key_id: str | None = None,
) -> dict[str, str]:
    """Create an Ed25519 attestation blob for one canonical claim string.

    Signs with the in-memory catalog key by default; ``seed`` (a 32-byte
    Ed25519 seed) selects an explicit test key instead. Fail-closed on
    unusable input.
    """
    if not isinstance(canonical_claim, str) or not canonical_claim:
        raise ValueError("Canonical claim must be a non-empty string.")
    claim_bytes = canonical_claim.encode("utf-8")
    if seed is not None:
        private_key = identity_keys.private_key_from_seed(seed)
        signing_key_id = str(key_id or "catalog-test-key")
    else:
        signing_key_id, private_key = _current_catalog_key()
    signature = identity_keys.sign_bytes(private_key, claim_bytes)
    public_raw = identity_keys.public_bytes_from_private(private_key)
    return {
        "algorithm": ATTESTATION_ALGORITHM,
        "claim_sha256": hashlib.sha256(claim_bytes).hexdigest(),
        "key_id": signing_key_id,
        "public_key": identity_keys.b64encode_raw(public_raw),
        "signature": identity_keys.b64encode_raw(signature),
    }


def verify_attestation(
    canonical_claim: str,
    attestation: Any,
    *,
    public_key: bytes | None = None,
) -> bool:
    """Verify an Ed25519 attestation blob over the exact claim string.

    Verifies against the catalog's known keys (current + retained
    previous) by default, or against an explicit ``public_key`` for
    caller-managed test keys. Anything unexpected fails closed.
    """
    if not isinstance(canonical_claim, str) or not isinstance(attestation, dict):
        return False
    if attestation.get("algorithm") != ATTESTATION_ALGORITHM:
        return False
    claim_bytes = canonical_claim.encode("utf-8")
    if attestation.get("claim_sha256") != hashlib.sha256(claim_bytes).hexdigest():
        return False
    signature = identity_keys.b64decode_raw(attestation.get("signature"))
    if signature is None:
        return False
    if public_key is not None:
        trusted = bytes(public_key)
    else:
        key_id = attestation.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            return False
        known = catalog_public_key(key_id=key_id)
        if known is None:
            return False
        trusted = known
        claimed = identity_keys.b64decode_raw(attestation.get("public_key"))
        if claimed is None or claimed != trusted:
            return False
    return identity_keys.verify_bytes(trusted, claim_bytes, signature)


def canonical_price_claim(product_id: str, price_credits: int) -> str:
    return f"price:{product_id}:{int(price_credits)}:{CURRENCY}"


def canonical_text_claim(product_id: str, text: str) -> str:
    return f"text:{product_id}:{text.strip()}"
