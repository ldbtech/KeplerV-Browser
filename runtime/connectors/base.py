"""Shared mail-connector interface and account-resolution helpers.

The interface is read-only by design: there is no send/delete surface here,
so a send attempt cannot silently succeed — it fails loudly at the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class MailNotConnectedError(RuntimeError):
    """Raised when mail is requested without a connected account.

    Connectors must raise this instead of returning fabricated messages.
    """


class MailAccountStatusError(RuntimeError):
    """Raised when the account exists but is revoked or expired."""


class AmbiguousAccountError(RuntimeError):
    """Raised when an account reference matches several accounts."""

    def __init__(self, message: str, candidates: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class AccountRef:
    """Caller-supplied account reference. Both fields are optional."""

    provider: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class ResolvedAccount:
    provider: str
    email: str


class MailConnector(Protocol):
    """Read-only mail interface. All providers implement this shape."""

    provider_id: str

    async def list_messages(self, account: ResolvedAccount, limit: int = 10) -> dict[str, Any]:
        ...

    async def search(self, account: ResolvedAccount, query: str, limit: int = 10) -> dict[str, Any]:
        ...

    async def get_message(self, account: ResolvedAccount, message_id: str) -> dict[str, Any]:
        ...

    async def get_thread(self, account: ResolvedAccount, thread_id: str) -> dict[str, Any]:
        ...


def normalize_message(raw: dict[str, Any], *, provider: str, fake: bool = False, label: str | None = None) -> dict[str, Any]:
    """Normalize one provider message into the shared mail shape."""
    message = {
        "id": str(raw.get("id", "")),
        "thread_id": str(raw.get("thread_id", raw.get("threadId", "") or "")),
        "provider": provider,
        "from": raw.get("from", ""),
        "to": list(raw.get("to", [])),
        "subject": str(raw.get("subject", "")),
        "date": str(raw.get("date", "")),
        "snippet": str(raw.get("snippet", "")),
        "body": str(raw.get("body", "")),
        "fake": fake,
    }
    if label:
        message["label"] = label
    return message


def resolve_account(
    ref: AccountRef,
    accounts: list[Any],
    *,
    effective_status: Any | None = None,
) -> ResolvedAccount:
    """Resolve an account reference against connected-account metadata.

    Rules: exact email match wins; otherwise filter by provider/email-substring;
    a single remaining candidate wins; zero matches raise MailNotConnectedError;
    several matches raise AmbiguousAccountError (never silently auto-pick).
    """
    normalized_email = (ref.email or "").strip().lower()
    normalized_provider = (ref.provider or "").strip().lower()

    def _provider(account: Any) -> str:
        return str(account.provider if hasattr(account, "provider") else account.get("provider")).lower()

    def _email(account: Any) -> str:
        return str(account.email if hasattr(account, "email") else account.get("email")).lower()

    if normalized_email:
        for account in accounts:
            if _email(account) == normalized_email and (not normalized_provider or _provider(account) == normalized_provider):
                return ResolvedAccount(provider=_provider(account), email=_email(account))
    candidates = [
        account for account in accounts
        if (not normalized_provider or _provider(account) == normalized_provider)
        and (not normalized_email or normalized_email in _email(account))
    ]
    if not candidates:
        if not accounts:
            raise MailNotConnectedError("No service account is connected. Connect one first — no mail is shown without a connection.")
        raise MailNotConnectedError("No connected account matches that reference.")
    if len(candidates) > 1:
        raise AmbiguousAccountError(
            "That matches several connected accounts. Pick one explicitly.",
            [{"provider": _provider(account), "email": _email(account)} for account in candidates],
        )
    only = candidates[0]
    return ResolvedAccount(provider=_provider(only), email=_email(only))
