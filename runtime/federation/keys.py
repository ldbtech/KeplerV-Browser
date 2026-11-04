"""Ed25519 identity keys for federated statements (Phase 6 M6+M7).

Real asymmetric signatures via the ``cryptography`` package (pinned in
``runtime/requirements.txt``) — no custom crypto. This module owns
key generation, raw sign/verify helpers, and the documented storage
contract for local identity keys:

Keychain namespace (production)
-------------------------------
Local identity private keys belong in the system keychain under the
existing service with a dedicated account namespace::

    service: ``com.keplerv.browser`` (same as OAuth entries)
    account: ``federation-identity:{operator_id}:{key_id}``

The value is the 32-byte Ed25519 seed (private key material), stored by
the Tauri side — exactly like the OAuth access tokens, which the Rust
layer reads from the keychain and injects per request over the trusted
``127.0.0.1`` bearer-authenticated channel. The Python sidecar never
reads the keychain directly and never sees ``MODEL_API_KEY``; it holds
only in-memory handles (this module) or caller-supplied seeds.

Test-only file fallback (CI)
----------------------------
:Class:`IdentityKeystore` persists seeds to disk **only** when
``KEPLERV_FEDERATION_KEYSTORE_DIR`` points at a directory (test/CI use).
Without that variable every key is memory-only. Files hold raw 32-byte
seeds with ``0o600`` permissions and must never carry real keys.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

#: Keychain service for local identity keys (shared with OAuth entries).
KEYCHAIN_SERVICE = "com.keplerv.browser"

#: Keychain account template for identity private keys (seed bytes).
#: Rendered as ``federation-identity:{operator_id}:{key_id}``.
KEYCHAIN_ACCOUNT_TEMPLATE = "federation-identity:{operator_id}:{key_id}"

#: Env var enabling the test-only file fallback for the keystore.
KEYSTORE_DIR_ENV_VAR = "KEPLERV_FEDERATION_KEYSTORE_DIR"

SEED_LENGTH = 32
SIGNATURE_LENGTH = 64
PUBLIC_KEY_LENGTH = 32


def keychain_account(operator_id: str, key_id: str) -> str:
    """Return the documented keychain account for an identity key."""
    return KEYCHAIN_ACCOUNT_TEMPLATE.format(
        operator_id=operator_id, key_id=key_id
    )


def generate_private_key() -> Ed25519PrivateKey:
    """Generate a fresh random Ed25519 private key."""
    return Ed25519PrivateKey.generate()


def private_key_from_seed(seed: bytes) -> Ed25519PrivateKey:
    """Build a private key from a 32-byte seed (deterministic tests)."""
    if not isinstance(seed, bytes) or len(seed) != SEED_LENGTH:
        raise ValueError("Ed25519 seeds must be exactly 32 bytes.")
    return Ed25519PrivateKey.from_private_bytes(seed)


def private_seed(private_key: Ed25519PrivateKey) -> bytes:
    """Return the raw 32-byte seed of a private key (handle with care)."""
    from cryptography.hazmat.primitives import serialization

    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_bytes_from_private(private_key: Ed25519PrivateKey) -> bytes:
    """Return the raw 32-byte public key for a private key."""
    from cryptography.hazmat.primitives import serialization

    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_from_bytes(raw: bytes) -> Ed25519PublicKey:
    """Build a public key from raw 32-byte public material."""
    if not isinstance(raw, bytes) or len(raw) != PUBLIC_KEY_LENGTH:
        raise ValueError("Ed25519 public keys must be exactly 32 bytes.")
    return Ed25519PublicKey.from_public_bytes(raw)


def sign_bytes(private_key: Ed25519PrivateKey, payload: bytes) -> bytes:
    """Sign payload bytes; returns the raw 64-byte signature."""
    return private_key.sign(bytes(payload))


def verify_bytes(public_raw: bytes, payload: bytes, signature: bytes) -> bool:
    """Verify a signature; fail-closed (any error means invalid)."""
    try:
        if (
            not isinstance(public_raw, bytes)
            or len(public_raw) != PUBLIC_KEY_LENGTH
        ):
            return False
        if not isinstance(signature, bytes) or len(signature) != SIGNATURE_LENGTH:
            return False
        public_key_from_bytes(public_raw).verify(bytes(signature), bytes(payload))
        return True
    except (InvalidSignature, ValueError):
        return False


def b64encode_raw(raw: bytes) -> str:
    return base64.b64encode(bytes(raw)).decode("ascii")


def b64decode_raw(text: Any) -> bytes | None:
    """Strict base64 decode; returns None instead of raising (fail-closed)."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None


class IdentityKeystore:
    """In-memory identity keys with a test-only file fallback.

    Production contract: private seeds live in the system keychain under
    :data:`KEYCHAIN_ACCOUNT_TEMPLATE` and are injected per request by the
    Tauri layer; this store then holds memory-only handles. Disk
    persistence happens only when ``KEPLERV_FEDERATION_KEYSTORE_DIR`` is
    set (CI/tests), writing raw seeds with ``0o600`` permissions.
    """

    def __init__(self, *, directory: str | Path | None = None) -> None:
        env_dir = os.environ.get(KEYSTORE_DIR_ENV_VAR, "").strip()
        chosen = directory if directory is not None else (env_dir or None)
        self._directory = Path(chosen) if chosen else None
        self._keys: dict[tuple[str, str], Ed25519PrivateKey] = {}

    @property
    def persistent(self) -> bool:
        """Whether the test-only file fallback is active."""
        return self._directory is not None

    def _path_for(self, operator_id: str, key_id: str) -> Path:
        assert self._directory is not None
        safe_operator = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(operator_id)
        )[:64]
        safe_key = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(key_id)
        )[:64]
        return self._directory / f"{safe_operator}.{safe_key}.key"

    def store(
        self, operator_id: str, key_id: str, private_key: Ed25519PrivateKey
    ) -> None:
        """Hold a private key in memory (plus disk when fallback is on)."""
        self._keys[(str(operator_id), str(key_id))] = private_key
        if self._directory is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(operator_id, key_id)
        path.write_bytes(private_seed(private_key))
        try:
            os.chmod(path, 0o600)
        except OSError:
            path.unlink(missing_ok=True)
            raise

    def load(
        self, operator_id: str, key_id: str
    ) -> Ed25519PrivateKey | None:
        """Return the private key for an operator/key id, if held."""
        hit = self._keys.get((str(operator_id), str(key_id)))
        if hit is not None:
            return hit
        if self._directory is None:
            return None
        path = self._path_for(operator_id, key_id)
        try:
            seed = path.read_bytes()
        except OSError:
            return None
        try:
            private_key = private_key_from_seed(seed)
        except ValueError:
            return None
        self._keys[(str(operator_id), str(key_id))] = private_key
        return private_key

    def forget(self, operator_id: str, key_id: str) -> None:
        """Drop a key from memory (disk copy, if any, is left alone)."""
        self._keys.pop((str(operator_id), str(key_id)), None)
