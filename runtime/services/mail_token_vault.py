"""Rust keychain -> sidecar token bridge (in-memory only).

Tokens live in the Rust keychain under ``oauth:{provider}:{account}``. They
reach Python over the trusted authed ``127.0.0.1`` sidecar channel in exactly
two ways, and both keep tokens out of JS state, logs, and disk:

1. Per-request injection (preferred for direct reads): Rust resolves the
   requested account from its own metadata, reads the keychain entry, and
   forwards only the access token in the sidecar-only
   ``X-Keplerv-Mail-Token`` header plus non-secret binding headers. The API
   layer binds that token to the single resolved account for the duration of
   one request via :func:`use_request_token`; it is never stored.

2. Short-lived in-memory vault (for agent background tasks): the scheduler's
   approved mail tools run outside any Rust-proxied request, so Rust pushes
   fresh tokens to ``POST /accounts/tokens`` after connect/refresh and
   evicts them on disconnect/revoke. :class:`MailTokenVault` holds them in
   process memory with provider-expiry eviction; it never persists, never
   logs values, and :meth:`MailTokenVault.describe` exposes metadata only.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

#: Sidecar-only request headers. Rust sets them; the frontend never sees
#: them (Tauri commands accept only provider/email/query, never tokens).
MAIL_TOKEN_HEADER = "x-keplerv-mail-token"
MAIL_TOKEN_PROVIDER_HEADER = "x-keplerv-mail-provider"
MAIL_TOKEN_ACCOUNT_HEADER = "x-keplerv-mail-account"

MAX_TOKEN_LENGTH = 8192


@dataclass
class _Entry:
    token: str
    expires_at: int


class MailTokenVault:
    """In-memory bridge vault. No persistence, no logging of token values."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}

    @staticmethod
    def _key(provider: str, email: str) -> tuple[str, str]:
        return (provider.strip().lower(), email.strip().lower())

    def put(self, provider: str, email: str, token: str, expires_at: int) -> None:
        """Store one pushed token until its provider expiry."""
        key = self._key(provider, email)
        if not key[0] or not key[1] or "@" not in key[1]:
            raise ValueError("Enter a valid provider and account email.")
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise ValueError("The pushed mail token was empty or too long.")
        try:
            expiry = int(expires_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("The pushed mail token needs an expiry time.") from exc
        if expiry <= int(time.time()):
            raise ValueError("The pushed mail token is already expired.")
        with self._lock:
            self._entries[key] = _Entry(token=token, expires_at=expiry)

    def get(self, provider: str, email: str, now: int | None = None) -> str | None:
        """Return the live token for one account, evicting expired entries."""
        key = self._key(provider, email)
        moment = int(time.time()) if now is None else int(now)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= moment:
                self._entries.pop(key, None)
                return None
            return entry.token

    def evict(self, provider: str, email: str) -> bool:
        """Drop one account's token. Returns True when one was held."""
        with self._lock:
            return self._entries.pop(self._key(provider, email), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def describe(self) -> list[dict[str, object]]:
        """Metadata about held tokens. Never includes token values."""
        with self._lock:
            return [
                {"provider": provider, "email": email, "expires_at": entry.expires_at}
                for (provider, email), entry in sorted(self._entries.items())
            ]


_request_token: ContextVar[tuple[str, str, str] | None] = ContextVar(
    "keplerv_request_mail_token", default=None
)


@contextmanager
def use_request_token(provider: str, email: str, token: str | None) -> Iterator[None]:
    """Bind one request-scoped token for the duration of a ``with`` block."""
    if not token:
        yield
        return
    binding = (provider.strip().lower(), email.strip().lower(), token)
    handle = _request_token.set(binding)
    try:
        yield
    finally:
        _request_token.reset(handle)


def request_token_for(provider: str, email: str) -> str | None:
    """Return the request-scoped token only when it is bound to this account."""
    binding = _request_token.get()
    if binding is None:
        return None
    bound_provider, bound_email, token = binding
    if bound_provider == provider.strip().lower() and bound_email == email.strip().lower():
        return token
    return None
