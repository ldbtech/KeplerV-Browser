"""Signed external-agent statements for the federated AIP prototype (Phase 6 M6+M7).

Prototype for operator identity, delegation credentials, scoped auth, and
receipts across the agent boundary. Signatures use HMAC-SHA256 from the
standard library (``hmac``/``hashlib`` — established primitives, no custom
crypto) over canonical JSON, with per-test fixture keys held only in
memory.

This is a *symmetric prototype*: HMAC proves the mechanism (verify /
reject, revocation, proof levels), not a production design. Real
deployments must use asymmetric signatures with keys in a KMS. Fixture
keys are generated per registry (or supplied explicitly by tests), never
read from the keychain, and must never protect real data.

Core rule — signed != true: a valid signature proves *origin* (which
operator key signed these bytes), never *truth*. Content must still be
checked against the trusted catalog and the event ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any
from uuid import uuid4

from runtime.models import utc_now

SIGNATURE_ALGORITHM = "hmac-sha256-fixture-v1"

#: Proof levels, weakest first. ``unverified-signature`` means no
#: authenticated origin (bad/missing signature, unknown or revoked key).
#: Higher levels require every lower level to verify first.
PROOF_LEVELS: tuple[str, ...] = (
    "unverified-signature",
    "operator",
    "delegation",
    "receipt",
)

_FIXTURE_NOTE = "fixture-only symmetric prototype; not a production signature scheme"


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON encoding that signatures cover."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _mac(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


class OperatorRegistry:
    """In-memory operator identities (fixture keys only, never the keychain)."""

    def __init__(self) -> None:
        self._operators: dict[str, dict[str, Any]] = {}

    def register(self, operator_id: str, *, key: bytes | None = None) -> dict[str, str]:
        """Create an operator identity with a fresh (or caller-supplied) key."""
        operator_id = str(operator_id or "").strip()
        if not operator_id or len(operator_id) > 128:
            raise ValueError("Operator id must be 1-128 characters.")
        if operator_id in self._operators:
            raise ValueError(f"Operator '{operator_id}' is already registered.")
        secret = bytes(key) if key is not None else secrets.token_bytes(32)
        if len(secret) < 16:
            raise ValueError("Fixture keys must be at least 16 bytes.")
        key_id = f"key-{uuid4().hex[:12]}"
        self._operators[operator_id] = {
            "operator_id": operator_id,
            "key_id": key_id,
            "secret": secret,
            "registered_at": utc_now(),
        }
        return {"operator_id": operator_id, "key_id": key_id}

    def key_id_for(self, operator_id: str) -> str | None:
        record = self._operators.get(str(operator_id))
        return record["key_id"] if record else None

    def secret_for(self, operator_id: str, key_id: str) -> bytes | None:
        record = self._operators.get(str(operator_id))
        if record is None or record["key_id"] != key_id:
            return None
        return record["secret"]

    def known_operator(self, operator_id: str) -> bool:
        return str(operator_id) in self._operators


class RevocationList:
    """Revocation status for operator keys and delegation credentials."""

    def __init__(self) -> None:
        self._revoked_keys: dict[str, dict[str, str]] = {}
        self._revoked_credentials: dict[str, dict[str, str]] = {}

    def revoke_key(self, key_id: str, reason: str = "") -> None:
        self._revoked_keys[str(key_id)] = {
            "revoked_at": utc_now(), "reason": str(reason)[:300]
        }

    def revoke_credential(self, credential_id: str, reason: str = "") -> None:
        self._revoked_credentials[str(credential_id)] = {
            "revoked_at": utc_now(), "reason": str(reason)[:300]
        }

    def is_key_revoked(self, key_id: str) -> bool:
        return str(key_id) in self._revoked_keys

    def is_credential_revoked(self, credential_id: str) -> bool:
        return str(credential_id) in self._revoked_credentials

    def status(self, entry_id: str) -> dict[str, Any]:
        """Revocation status lookup for a key or credential id."""
        entry_id = str(entry_id)
        if entry_id in self._revoked_keys:
            return {"id": entry_id, "kind": "key", "revoked": True,
                    **self._revoked_keys[entry_id]}
        if entry_id in self._revoked_credentials:
            return {"id": entry_id, "kind": "credential", "revoked": True,
                    **self._revoked_credentials[entry_id]}
        return {"id": entry_id, "kind": "unknown", "revoked": False}


def sign_statement(
    *,
    operator_id: str,
    statement: dict[str, Any],
    registry: OperatorRegistry,
) -> dict[str, Any]:
    """Sign a statement envelope with the operator's fixture key."""
    if not isinstance(statement, dict):
        raise ValueError("Statement must be a JSON object.")
    key_id = registry.key_id_for(operator_id)
    secret = registry.secret_for(operator_id, key_id or "")
    if key_id is None or secret is None:
        raise ValueError(f"Unknown operator '{operator_id}'.")
    body = json.loads(json.dumps(statement, ensure_ascii=False))
    body.setdefault("statement_id", f"stmt-{uuid4().hex[:12]}")
    body.setdefault("issued_at", utc_now())
    envelope = {
        "statement_id": body["statement_id"],
        "statement": body,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "operator_id": operator_id,
            "key_id": key_id,
            "sig": _mac(secret, canonical_bytes(body)),
            "fixture": True,
            "note": _FIXTURE_NOTE,
        },
    }
    return envelope


def _verify_signature(
    body: Any, signature: Any, registry: OperatorRegistry, revocation: RevocationList
) -> tuple[bool, str, str]:
    """Return (ok, key_id, reason) for one signature block."""
    if not isinstance(signature, dict):
        return False, "", "Missing signature block."
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return False, "", "Unknown signature algorithm."
    operator_id = str(signature.get("operator_id") or "")
    key_id = str(signature.get("key_id") or "")
    presented = signature.get("sig")
    if not operator_id or not key_id or not isinstance(presented, str) or not presented:
        return False, key_id, "Signature block is incomplete."
    if revocation.is_key_revoked(key_id):
        return False, key_id, f"Signing key '{key_id}' is revoked."
    secret = registry.secret_for(operator_id, key_id)
    if secret is None:
        return False, key_id, f"Operator '{operator_id}' / key '{key_id}' is unknown."
    try:
        expected = _mac(secret, canonical_bytes(body))
    except (TypeError, ValueError):
        return False, key_id, "Statement is not signable canonical JSON."
    if not hmac.compare_digest(presented, expected):
        return False, key_id, "Signature does not match the statement bytes."
    return True, key_id, "Signature verifies for a known, non-revoked operator key."


def issue_delegation(
    *,
    operator_id: str,
    delegate_id: str,
    scopes: list[str],
    registry: OperatorRegistry,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Issue a signed delegation credential: operator grants scopes to a delegate."""
    if not scopes:
        raise ValueError("Delegation credentials must grant at least one scope.")
    credential = {
        "credential_id": f"cred-{uuid4().hex[:12]}",
        "operator_id": operator_id,
        "delegate_id": str(delegate_id),
        "scopes": sorted({str(scope) for scope in scopes}),
        "issued_at": utc_now(),
    }
    if expires_at:
        credential["expires_at"] = str(expires_at)
    return sign_statement(
        operator_id=operator_id, statement=credential, registry=registry
    )


def _delegation_check(
    envelope: dict[str, Any],
    registry: OperatorRegistry,
    revocation: RevocationList,
) -> tuple[bool, str]:
    credential = envelope.get("delegation_credential")
    if credential is None:
        return False, "No delegation credential presented."
    if not isinstance(credential, dict):
        return False, "Delegation credential is malformed."
    body = credential.get("statement")
    ok, _, reason = _verify_signature(
        body, credential.get("signature"), registry, revocation
    )
    if not ok:
        return False, f"Delegation credential signature invalid: {reason}"
    if not isinstance(body, dict):
        return False, "Delegation credential body is malformed."
    credential_id = str(body.get("credential_id") or "")
    if revocation.is_credential_revoked(credential_id):
        return False, f"Delegation credential '{credential_id}' is revoked."
    expires_at = str(body.get("expires_at") or "")
    if expires_at and expires_at <= utc_now():
        return False, "Delegation credential has expired."
    statement = envelope.get("statement")
    requested = set()
    if isinstance(statement, dict):
        raw_scopes = statement.get("scopes_requested", [])
        requested = {str(scope) for scope in raw_scopes} if isinstance(raw_scopes, list) else set()
    granted = {str(scope) for scope in body.get("scopes", [])}
    if not requested.issubset(granted):
        missing = sorted(requested - granted)
        return False, f"Delegation credential does not cover scopes: {missing}."
    return True, "Delegation credential verifies and covers the requested scopes."


def issue_receipt(
    *,
    operator_id: str,
    statement_id: str,
    registry: OperatorRegistry,
    note: str = "",
) -> dict[str, Any]:
    """Issue a signed receipt acknowledging delivery of a statement.

    A receipt proves the signer *received* the referenced statement — it is
    not an endorsement of its content (signed != true).
    """
    receipt = {
        "receipt_id": f"rcpt-{uuid4().hex[:12]}",
        "statement_id": str(statement_id),
        "received_at": utc_now(),
        "note": str(note)[:300],
    }
    return sign_statement(
        operator_id=operator_id, statement=receipt, registry=registry
    )


def _receipt_check(
    envelope: dict[str, Any],
    registry: OperatorRegistry,
    revocation: RevocationList,
) -> tuple[bool, str]:
    receipt = envelope.get("receipt")
    if receipt is None:
        return False, "No receipt presented."
    if not isinstance(receipt, dict):
        return False, "Receipt is malformed."
    body = receipt.get("statement")
    ok, _, reason = _verify_signature(
        body, receipt.get("signature"), registry, revocation
    )
    if not ok:
        return False, f"Receipt signature invalid: {reason}"
    if not isinstance(body, dict) or body.get("statement_id") != envelope.get("statement_id"):
        return False, "Receipt does not reference this statement."
    return True, "Counterparty receipt verifies for this statement."


def verify_statement(
    envelope: Any,
    registry: OperatorRegistry,
    revocation: RevocationList,
) -> dict[str, Any]:
    """Verify a signed statement envelope; return the achieved proof level.

    Levels: ``unverified-signature`` (no authenticated origin) <
    ``operator`` (known, non-revoked operator key) < ``delegation`` (valid
    delegation credential covering the requested scopes) < ``receipt``
    (counterparty receipt verifies). Every result carries
    ``authenticity_only``: origin is proven, truth is not.
    """
    base = {
        "authentic": False,
        "proof_level": "unverified-signature",
        "authenticity_only": True,
        "truth_note": (
            "A valid signature proves origin only (signed != true); content "
            "must still be checked against the trusted catalog and event ledger."
        ),
    }
    if not isinstance(envelope, dict):
        return {**base, "reason": "Envelope is not a JSON object."}
    ok, key_id, reason = _verify_signature(
        envelope.get("statement"), envelope.get("signature"), registry, revocation
    )
    if not ok:
        return {**base, "key_id": key_id, "reason": reason}
    result: dict[str, Any] = {
        **base, "authentic": True, "proof_level": "operator",
        "key_id": key_id, "reason": reason,
    }
    delegation_ok, delegation_reason = _delegation_check(envelope, registry, revocation)
    result["delegation_ok"] = delegation_ok
    result["delegation_reason"] = delegation_reason
    if delegation_ok:
        result["proof_level"] = "delegation"
        result["reason"] = delegation_reason
    receipt_ok, receipt_reason = _receipt_check(envelope, registry, revocation)
    result["receipt_ok"] = receipt_ok
    result["receipt_reason"] = receipt_reason
    if receipt_ok and delegation_ok:
        result["proof_level"] = "receipt"
        result["reason"] = receipt_reason
    return result


def verify_receipt(
    receipt: Any,
    registry: OperatorRegistry,
    revocation: RevocationList,
) -> dict[str, Any]:
    """Verify a standalone receipt envelope (signature + shape only)."""
    if not isinstance(receipt, dict):
        return {"authentic": False, "reason": "Receipt is not a JSON object."}
    ok, key_id, reason = _verify_signature(
        receipt.get("statement"), receipt.get("signature"), registry, revocation
    )
    body = receipt.get("statement")
    if ok and (not isinstance(body, dict) or not body.get("statement_id")):
        return {"authentic": False, "key_id": key_id,
                "reason": "Receipt does not reference a statement."}
    return {"authentic": ok, "key_id": key_id, "reason": reason}
