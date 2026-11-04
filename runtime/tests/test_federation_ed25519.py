"""Federation Ed25519 signatures: round-trip, tamper, wrong-key, revoked, rotation.

Real Ed25519 via the ``cryptography`` package over canonical JSON — no
live network, no keychain access (the file keystore fallback is
exercised only under a tmp dir with ``KEPLERV_FEDERATION_KEYSTORE_DIR``
set). Fictional demo content throughout.
"""

from __future__ import annotations

import base64
import os

import pytest

from runtime.federation import (
    IdentityKeystore,
    OperatorRegistry,
    RevocationList,
    issue_delegation,
    issue_receipt,
    keychain_account,
    sign_statement,
    verify_receipt,
    verify_statement,
)
from runtime.federation.keys import (
    KEYCHAIN_SERVICE,
    KEYSTORE_DIR_ENV_VAR,
    b64encode_raw,
    private_key_from_seed,
    public_bytes_from_private,
)
from runtime.merchants import trusted_catalog

SEED_A = bytes(range(32))
SEED_B = bytes((i + 7) % 256 for i in range(32))
SEED_C = bytes((i * 3 + 1) % 256 for i in range(32))


def _registry(seed: bytes = SEED_A) -> OperatorRegistry:
    registry = OperatorRegistry()
    registry.register("op-a", seed=seed)
    return registry


# -- statements: round-trip / tamper / wrong-key / revoked / rotation ------


def test_ed25519_round_trip_operator_level():
    registry = _registry()
    envelope = sign_statement(
        operator_id="op-a",
        statement={"kind": "quote", "content": {"product_id": "starlamp"}},
        registry=registry,
    )
    assert envelope["signature"]["algorithm"] == "ed25519-v1"
    assert len(base64.b64decode(envelope["signature"]["sig"])) == 64
    result = verify_statement(envelope, registry, RevocationList())
    assert result["authentic"] is True
    assert result["proof_level"] == "operator"
    assert result["authenticity_only"] is True


def test_ed25519_tampered_payload_rejected():
    registry = _registry()
    envelope = sign_statement(
        operator_id="op-a",
        statement={"kind": "quote", "content": {"price": 120}},
        registry=registry,
    )
    envelope["statement"]["content"]["price"] = 5
    result = verify_statement(envelope, registry, RevocationList())
    assert result["authentic"] is False
    assert result["proof_level"] == "unverified-signature"


def test_ed25519_tampered_signature_rejected():
    registry = _registry()
    envelope = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry
    )
    raw = bytearray(base64.b64decode(envelope["signature"]["sig"]))
    raw[0] ^= 0xFF
    envelope["signature"]["sig"] = base64.b64encode(bytes(raw)).decode("ascii")
    result = verify_statement(envelope, registry, RevocationList())
    assert result["authentic"] is False
    assert result["proof_level"] == "unverified-signature"


def test_ed25519_wrong_key_rejected():
    registry_a, registry_b = OperatorRegistry(), OperatorRegistry()
    registry_a.register("op-a", seed=SEED_A)
    registry_b.register("op-a", seed=SEED_B)
    envelope = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry_a
    )
    result = verify_statement(envelope, registry_b, RevocationList())
    assert result["authentic"] is False
    assert result["proof_level"] == "unverified-signature"


def test_ed25519_swapped_public_key_rejected():
    registry = _registry()
    other = OperatorRegistry()
    receipt = other.register("op-a", seed=SEED_B)
    envelope = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry
    )
    envelope["signature"]["public_key"] = receipt["public_key"]
    result = verify_statement(envelope, registry, RevocationList())
    assert result["authentic"] is False
    assert "public key" in result["reason"].casefold()


def test_ed25519_revoked_key_rejected():
    registry = _registry()
    receipt = registry.register("op-b", seed=SEED_B)
    revocation = RevocationList()
    envelope = sign_statement(
        operator_id="op-b", statement={"kind": "quote"}, registry=registry
    )
    assert verify_statement(envelope, registry, revocation)["authentic"] is True
    revocation.revoke_key(receipt["key_id"], "test compromise")
    result = verify_statement(envelope, registry, revocation)
    assert result["authentic"] is False
    assert "revoked" in result["reason"].casefold()
    assert revocation.status(receipt["key_id"])["revoked"] is True


def test_ed25519_revoked_credential_breaks_delegation():
    registry = _registry()
    credential = issue_delegation(
        operator_id="op-a", delegate_id="merchant-x",
        scopes=["catalog.quote"], registry=registry,
    )
    revocation = RevocationList()
    revocation.revoke_credential(
        credential["statement"]["credential_id"], "test abuse"
    )
    envelope = sign_statement(
        operator_id="op-a",
        statement={"kind": "quote", "scopes_requested": ["catalog.quote"]},
        registry=registry,
    )
    envelope["delegation_credential"] = credential
    result = verify_statement(envelope, registry, revocation)
    assert result["authentic"] is True  # origin fine; grant revoked
    assert result["proof_level"] == "operator"
    assert result["delegation_ok"] is False


def test_ed25519_rotation_new_signs_old_still_verifies():
    registry = _registry()
    before = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry
    )
    old_key_id = before["signature"]["key_id"]
    receipt = registry.rotate("op-a")
    assert receipt["key_id"] != old_key_id
    assert receipt["previous_key_id"] == old_key_id
    after = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry
    )
    assert after["signature"]["key_id"] == receipt["key_id"]
    revocation = RevocationList()
    assert verify_statement(before, registry, revocation)["proof_level"] == "operator"
    assert verify_statement(after, registry, revocation)["proof_level"] == "operator"
    # Revoking the rotated-out key rejects only the pre-rotation envelope.
    revocation.revoke_key(old_key_id, "test post-rotation compromise")
    assert verify_statement(before, registry, revocation)["authentic"] is False
    assert verify_statement(after, registry, revocation)["authentic"] is True


def test_ed25519_legacy_hmac_envelope_rejected():
    registry = _registry()
    envelope = sign_statement(
        operator_id="op-a", statement={"kind": "quote"}, registry=registry
    )
    envelope["signature"]["algorithm"] = "hmac-sha256-fixture-v1"
    result = verify_statement(envelope, registry, RevocationList())
    assert result["authentic"] is False
    assert "algorithm" in result["reason"].casefold()


def test_ed25519_delegation_and_receipt_proof_levels():
    registry = OperatorRegistry()
    registry.register("op-a", seed=SEED_A)
    registry.register("op-b", seed=SEED_B)
    envelope = sign_statement(
        operator_id="op-a",
        statement={"kind": "quote", "scopes_requested": ["catalog.quote"]},
        registry=registry,
    )
    envelope["delegation_credential"] = issue_delegation(
        operator_id="op-a", delegate_id="merchant-x",
        scopes=["catalog.quote"], registry=registry,
    )
    envelope["receipt"] = issue_receipt(
        operator_id="op-b", statement_id=envelope["statement_id"],
        registry=registry,
    )
    revocation = RevocationList()
    assert verify_statement(envelope, registry, revocation)["proof_level"] == "receipt"
    assert verify_receipt(envelope["receipt"], registry, revocation)["authentic"] is True


# -- keychain namespace + test-only file fallback ---------------------------


def test_keychain_namespace_documented():
    account = keychain_account("op-a", "key-abc")
    assert account == "federation-identity:op-a:key-abc"
    assert KEYCHAIN_SERVICE == "com.keplerv.browser"
    receipt = OperatorRegistry().register("op-x")
    assert receipt["keychain_account"].startswith("federation-identity:op-x:")


def test_keystore_memory_only_by_default():
    store = IdentityKeystore()
    assert store.persistent is False
    private = private_key_from_seed(SEED_A)
    store.store("op-a", "key-1", private)
    assert store.load("op-a", "key-1") is not None
    assert store.load("op-a", "key-unknown") is None
    assert store.load("op-unknown", "key-1") is None


def test_keystore_file_fallback_round_trip_tmp_only(tmp_path, monkeypatch):
    monkeypatch.setenv(KEYSTORE_DIR_ENV_VAR, str(tmp_path / "keys"))
    store = IdentityKeystore()
    assert store.persistent is True
    store.store("op-a", "key-1", private_key_from_seed(SEED_A))
    assert (tmp_path / "keys" / "op-a.key-1.key").read_bytes() == SEED_A
    fresh = IdentityKeystore()  # new handle reads the fallback file
    loaded = fresh.load("op-a", "key-1")
    assert loaded is not None
    assert public_bytes_from_private(loaded) == public_bytes_from_private(
        private_key_from_seed(SEED_A)
    )
    # Env var unset -> fallback off, disk ignored.
    monkeypatch.delenv(KEYSTORE_DIR_ENV_VAR)
    assert IdentityKeystore().persistent is False
    assert IdentityKeystore().load("op-a", "key-1") is None


def test_registry_signing_needs_keystore_key():
    registry = OperatorRegistry()
    with pytest.raises(ValueError, match="[Uu]nknown operator"):
        sign_statement(
            operator_id="ghost", statement={"kind": "quote"}, registry=registry
        )


# -- catalog attestations: round-trip / tamper / wrong-key / rotation -------


def test_catalog_attestation_round_trip():
    canonical = trusted_catalog.canonical_price_claim("starlamp", 120)
    blob = trusted_catalog.attest_claim(canonical)
    assert blob["algorithm"] == "ed25519-v1"
    assert len(base64.b64decode(blob["signature"])) == 64
    assert trusted_catalog.verify_attestation(canonical, blob) is True


def test_catalog_attestation_wrong_claim_and_tamper_rejected():
    canonical = trusted_catalog.canonical_price_claim("starlamp", 120)
    blob = trusted_catalog.attest_claim(canonical)
    assert trusted_catalog.verify_attestation(
        trusted_catalog.canonical_price_claim("starlamp", 72), blob
    ) is False
    tampered = dict(blob, signature=b64encode_raw(b"\x00" * 64))
    assert trusted_catalog.verify_attestation(canonical, tampered) is False
    assert trusted_catalog.verify_attestation(canonical, {"algorithm": "nope"}) is False
    assert trusted_catalog.verify_attestation(canonical, "not-a-dict") is False


def test_catalog_attestation_wrong_key_rejected():
    canonical = trusted_catalog.canonical_price_claim("starlamp", 120)
    blob = trusted_catalog.attest_claim(canonical)
    stranger = public_bytes_from_private(private_key_from_seed(SEED_C))
    assert trusted_catalog.verify_attestation(canonical, blob, public_key=stranger) is False
    forged_key_id = dict(
        blob, key_id="catalog-never-issued",
        public_key=b64encode_raw(public_bytes_from_private(private_key_from_seed(SEED_C))),
    )
    assert trusted_catalog.verify_attestation(canonical, forged_key_id) is False


def test_catalog_attestation_explicit_seed_key_round_trip():
    canonical = trusted_catalog.canonical_text_claim("starlamp", "30-day fictional return window")
    blob = trusted_catalog.attest_claim(canonical, seed=SEED_C)
    trusted = public_bytes_from_private(private_key_from_seed(SEED_C))
    assert trusted_catalog.verify_attestation(canonical, blob, public_key=trusted) is True


def test_catalog_attestation_rotation_keeps_old_blobs_valid():
    canonical = trusted_catalog.canonical_price_claim("skycompass", 85)
    before = trusted_catalog.attest_claim(canonical)
    receipt = trusted_catalog.rotate_catalog_key()
    assert receipt["key_id"] != receipt["previous_key_id"]
    after = trusted_catalog.attest_claim(canonical)
    assert after["key_id"] == receipt["key_id"]
    assert trusted_catalog.verify_attestation(canonical, before) is True
    assert trusted_catalog.verify_attestation(canonical, after) is True


def test_catalog_attestation_legacy_hmac_blob_rejected():
    canonical = trusted_catalog.canonical_price_claim("starlamp", 120)
    blob = trusted_catalog.attest_claim(canonical)
    blob["algorithm"] = "hmac-sha256-fixture-v1"
    assert trusted_catalog.verify_attestation(canonical, blob) is False
