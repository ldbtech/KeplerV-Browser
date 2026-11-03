"""Service registry + ConnectedAccount non-secret metadata store.

This module holds provider descriptors (labels, capabilities, scopes) and
per-account *metadata* (status, scopes, expiry). OAuth tokens themselves stay
in the Rust keychain under ``oauth:{provider}:{account}`` entries; Python only
holds the account identity as an opaque handle and never sees token values.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


STATUS_CONNECTED = "connected"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"

#: Non-secret provider descriptors. Mirrors ``src-tauri/src/oauth.rs``.
PROVIDER_DESCRIPTORS: dict[str, dict] = {
    "gmail": {
        "id": "gmail",
        "label": "Gmail (read-only)",
        "auth": "oauth2-pkce",
        "scopes": ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly"],
        "capabilities": ["mail.read", "mail.search", "mail.get"],
    },
    "outlook": {
        "id": "outlook",
        "label": "Outlook (read-only)",
        "auth": "oauth2-pkce",
        "scopes": ["openid", "email", "Mail.Read"],
        "capabilities": ["mail.read", "mail.search", "mail.get"],
    },
    "fake-mail": {
        "id": "fake-mail",
        "label": "Fake Mail (clearly-labeled test fixture — no live mail)",
        "auth": "fake-local",
        "scopes": ["fake-mail.read"],
        "capabilities": ["mail.read", "mail.search", "mail.get"],
    },
}


@dataclass
class ConnectedAccount:
    """Non-secret connected-account metadata. No tokens here, ever."""

    provider: str
    email: str
    scopes: list[str]
    expires_at: int
    status: str = STATUS_CONNECTED


def _default_store_path() -> Path:
    data_dir = os.getenv("KEPLERV_DATA_DIR", "")
    base = Path(data_dir) if data_dir else Path.cwd() / ".keplerv-data"
    return base / "services" / "accounts.json"


class ServiceRegistry:
    """Provider descriptors + JSON-backed ConnectedAccount metadata store."""

    def __init__(self, store_path: Path | str | None = None) -> None:
        self._store_path = Path(store_path) if store_path else _default_store_path()
        self._accounts: dict[str, ConnectedAccount] = {}
        self._load()

    # -- descriptors -----------------------------------------------------

    def providers(self) -> list[dict]:
        return [dict(descriptor) for descriptor in PROVIDER_DESCRIPTORS.values()]

    def describe(self, provider: str) -> dict:
        try:
            return dict(PROVIDER_DESCRIPTORS[provider])
        except KeyError as exc:
            raise ValueError(f"Unknown service provider '{provider}'.") from exc

    # -- account metadata ------------------------------------------------

    @staticmethod
    def _key(provider: str, email: str) -> str:
        return f"{provider}:{email.strip().lower()}"

    def connect(self, provider: str, email: str, scopes: list[str] | None = None, expires_at: int | None = None) -> ConnectedAccount:
        """Record a newly connected account (metadata only)."""
        descriptor = self.describe(provider)
        normalized = email.strip().lower()
        if "@" not in normalized:
            raise ValueError("Enter a valid account email.")
        account = ConnectedAccount(
            provider=provider,
            email=normalized,
            scopes=list(scopes) if scopes is not None else list(descriptor["scopes"]),
            expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
            status=STATUS_CONNECTED,
        )
        self._accounts[self._key(provider, normalized)] = account
        self._save()
        return account

    def get(self, provider: str, email: str) -> ConnectedAccount | None:
        return self._accounts.get(self._key(provider, email))

    def list_accounts(self) -> list[ConnectedAccount]:
        return sorted(self._accounts.values(), key=lambda account: (account.provider, account.email))

    def effective_status(self, account: ConnectedAccount, now: int | None = None) -> str:
        """Derived status: stored ``revoked`` always wins, else expiry applies."""
        if account.status == STATUS_REVOKED:
            return STATUS_REVOKED
        moment = int(time.time()) if now is None else now
        if account.expires_at <= moment:
            return STATUS_EXPIRED
        return STATUS_CONNECTED

    def refresh(self, provider: str, email: str, expires_at: int) -> ConnectedAccount:
        account = self._require(provider, email)
        if account.status == STATUS_REVOKED:
            raise ValueError("This account was revoked. Reconnect it to use it again.")
        account.expires_at = expires_at
        account.status = STATUS_CONNECTED
        self._save()
        return account

    def revoke(self, provider: str, email: str) -> ConnectedAccount:
        """Mark revoked (remote revocation observed). Credential deletion itself
        happens in the Rust keychain via ``oauth_disconnect``."""
        account = self._require(provider, email)
        account.status = STATUS_REVOKED
        self._save()
        return account

    def disconnect(self, provider: str, email: str) -> bool:
        """Drop local metadata after keychain credential deletion."""
        removed = self._accounts.pop(self._key(provider, email), None) is not None
        if removed:
            self._save()
        return removed

    def _require(self, provider: str, email: str) -> ConnectedAccount:
        account = self.get(provider, email)
        if account is None:
            raise ValueError(f"No connected account '{email}' for provider '{provider}'.")
        return account

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key, item in raw.items():
            if not isinstance(item, dict):
                continue
            try:
                self._accounts[key] = ConnectedAccount(
                    provider=str(item["provider"]),
                    email=str(item["email"]),
                    scopes=[str(scope) for scope in item.get("scopes", [])],
                    expires_at=int(item.get("expires_at", 0)),
                    status=str(item.get("status", STATUS_CONNECTED)),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(account) for key, account in self._accounts.items()}
        temp = self._store_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(self._store_path)
