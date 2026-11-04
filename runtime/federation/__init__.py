"""Federated AIP: Ed25519-signed external-agent statements (Phase 6 M6+M7)."""

from __future__ import annotations

from runtime.federation.keys import (
    KEYCHAIN_ACCOUNT_TEMPLATE,
    KEYCHAIN_SERVICE,
    KEYSTORE_DIR_ENV_VAR,
    IdentityKeystore,
    keychain_account,
)
from runtime.federation.statements import (
    PROOF_LEVELS,
    SIGNATURE_ALGORITHM,
    OperatorRegistry,
    RevocationList,
    canonical_bytes,
    issue_delegation,
    issue_receipt,
    sign_statement,
    verify_receipt,
    verify_statement,
)

__all__ = [
    "KEYCHAIN_ACCOUNT_TEMPLATE",
    "KEYCHAIN_SERVICE",
    "KEYSTORE_DIR_ENV_VAR",
    "PROOF_LEVELS",
    "SIGNATURE_ALGORITHM",
    "IdentityKeystore",
    "OperatorRegistry",
    "RevocationList",
    "canonical_bytes",
    "issue_delegation",
    "issue_receipt",
    "keychain_account",
    "sign_statement",
    "verify_receipt",
    "verify_statement",
]
