"""Gmail connector using the official Gmail API shapes.

Read endpoints (all HTTPS, all GET):
- ``GET /gmail/v1/users/{userId}/messages`` (list; ``q`` for search)
- ``GET /gmail/v1/users/{userId}/messages/{id}?format=full`` (read)
- ``GET /gmail/v1/users/{userId}/threads/{id}?format=full`` (thread)

Send endpoint (live transmission, approval-gated by the caller):
- ``POST /gmail/v1/users/{userId}/messages/send`` (base64url RFC 2822)

Read scope: ``https://www.googleapis.com/auth/gmail.readonly``.
Send scope: ``https://www.googleapis.com/auth/gmail.send``.

:class:`GmailConnector` stays read-only by design: transmission lives only
in :class:`GmailSender`, which the scheduler calls after an AIP grant plus
explicit human approval. Neither class fabricates messages. Live tokens stay
in the Rust keychain and are injected per request over the trusted authed
``127.0.0.1`` sidecar channel; without a token supplier every method raises
:class:`MailNotConnectedError` instead of returning anything.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Callable
from urllib.parse import urlsplit

from runtime.connectors.base import (
    LIVE_SEND_LABEL,
    MailNotConnectedError,
    ResolvedAccount,
    normalize_message,
)

GMAIL_API_BASE = "https://gmail.googleapis.com"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
MINIMAL_SCOPES = ["openid", "email", GMAIL_READONLY_SCOPE]
SEND_SCOPES = ["openid", "email", GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE]


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _header(payload: dict[str, Any], name: str) -> str:
    for header in payload.get("headers", []) or []:
        if str(header.get("name", "")).lower() == name:
            return str(header.get("value", ""))
    return ""


def _extract_text(payload: dict[str, Any]) -> str:
    """Prefer text/plain bodies; fall back to any decodable text part."""
    mime = str(payload.get("mimeType", ""))
    data = (payload.get("body", {}) or {}).get("data", "")
    if mime.startswith("text/plain") and data:
        return _decode_body(str(data))
    parts = payload.get("parts", []) or []
    for part in parts:
        if str(part.get("mimeType", "")).startswith("text/plain"):
            text = _extract_text(part)
            if text:
                return text
    for part in parts:
        text = _extract_text(part)
        if text:
            return text
    if mime.startswith("text/") and data:
        return _decode_body(str(data))
    return ""


def parse_gmail_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert one Gmail ``messages.get(format=full)`` payload to shared shape."""
    payload = raw.get("payload", {}) or {}
    to_header = _header(payload, "to")
    return normalize_message(
        {
            "id": raw.get("id", ""),
            "thread_id": raw.get("threadId", ""),
            "from": _header(payload, "from"),
            "to": [address.strip() for address in to_header.split(",") if address.strip()],
            "subject": _header(payload, "subject"),
            "date": _header(payload, "date"),
            "snippet": raw.get("snippet", ""),
            "body": _extract_text(payload),
        },
        provider="gmail",
    )


def _check_api_base(api_base: str) -> str:
    """Allow the official HTTPS endpoint plus loopback overrides for tests.

    The mock-OAuth-IdP end-to-end test points connectors at a loopback mock;
    anything else that is not HTTPS is rejected.
    """
    parts = urlsplit(api_base)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host and not parts.username and not parts.password:
        return api_base.rstrip("/")
    if parts.scheme == "http" and host in {"127.0.0.1", "localhost"} and not parts.username and not parts.password:
        return api_base.rstrip("/")
    raise ValueError("The Gmail API base must be the official HTTPS endpoint or a loopback test URL.")


class GmailConnector:
    """Read-only Gmail access. No send/delete methods exist on this class."""

    provider_id = "gmail"

    def __init__(
        self,
        client: Any | None = None,
        token_supplier: Callable[[ResolvedAccount], str | None] | None = None,
        api_base: str = GMAIL_API_BASE,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier
        self._api_base = _check_api_base(api_base)

    def _token(self, account: ResolvedAccount) -> str:
        token = self._token_supplier(account) if self._token_supplier else None
        if not token:
            raise MailNotConnectedError(
                f"No live Gmail session for {account.email}. Connect the account first — "
                "no messages are shown without a connection."
            )
        return token

    def _require_client(self) -> Any:
        if self._client is None:
            raise MailNotConnectedError("Gmail has no HTTP client in this context; connect first.")
        return self._client

    async def _get(self, account: ResolvedAccount, path: str, params: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        token = self._token(account)
        response = await client.get(
            f"{self._api_base}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise MailNotConnectedError("Gmail rejected the session. Reconnect the account.")
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail request failed with HTTP {response.status_code}.")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def list_messages(self, account: ResolvedAccount, limit: int = 10) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 25))
        payload = await self._get(account, "/gmail/v1/users/me/messages", {"maxResults": bounded})
        entries = payload.get("messages", []) or []
        messages = [parse_gmail_message({"id": entry.get("id", "")}) for entry in entries[:bounded]]
        return {"provider": "gmail", "account": account.email, "fake": False, "messages": messages}

    async def search(self, account: ResolvedAccount, query: str, limit: int = 10) -> dict[str, Any]:
        needle = query.strip()
        if not needle:
            raise ValueError("Enter a search query.")
        bounded = max(1, min(int(limit), 25))
        payload = await self._get(account, "/gmail/v1/users/me/messages", {"q": needle[:500], "maxResults": bounded})
        entries = payload.get("messages", []) or []
        messages = [parse_gmail_message({"id": entry.get("id", "")}) for entry in entries[:bounded]]
        return {"provider": "gmail", "account": account.email, "fake": False, "query": needle, "messages": messages}

    async def get_message(self, account: ResolvedAccount, message_id: str) -> dict[str, Any]:
        if not message_id.strip():
            raise ValueError("Enter a message id.")
        payload = await self._get(account, f"/gmail/v1/users/me/messages/{message_id.strip()}", {"format": "full"})
        if not payload.get("id"):
            raise KeyError(f"Unknown Gmail message '{message_id}'.")
        return parse_gmail_message(payload)

    async def get_thread(self, account: ResolvedAccount, thread_id: str) -> dict[str, Any]:
        if not thread_id.strip():
            raise ValueError("Enter a thread id.")
        payload = await self._get(account, f"/gmail/v1/users/me/threads/{thread_id.strip()}", {"format": "full"})
        parts = payload.get("messages", []) or []
        if not payload.get("id") and not parts:
            raise KeyError(f"Unknown Gmail thread '{thread_id}'.")
        return {
            "provider": "gmail",
            "account": account.email,
            "fake": False,
            "thread_id": payload.get("id", thread_id.strip()),
            "messages": [parse_gmail_message(part) for part in parts],
        }


def build_gmail_send_payload(sender: str, to: str, subject: str, body: str) -> dict[str, str]:
    """Build the ``messages.send`` request body (base64url RFC 2822).

    Pure helper so the wire format is unit-testable without HTTP.
    """
    sender = sender.strip()
    to = to.strip()
    subject = subject.strip()
    if "@" not in sender or "@" not in to:
        raise ValueError("Enter valid sender and recipient addresses.")
    if not subject or not body.strip():
        raise ValueError("Subject and body are required.")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject[:300]
    message.set_content(body[:2000])
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return {"raw": raw}


class GmailSender:
    """Live Gmail transmission over ``POST .../messages/send``.

    Deliberately separate from the read-only :class:`GmailConnector` so the
    read path can never transmit. Callers (the scheduler's
    ``execute_service_send``) must enforce the AIP grant plus explicit human
    approval before calling :meth:`send_message`.
    """

    provider_id = "gmail"

    def __init__(
        self,
        client: Any | None = None,
        token_supplier: Callable[[ResolvedAccount], str | None] | None = None,
        api_base: str = GMAIL_API_BASE,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier
        self._api_base = _check_api_base(api_base)

    def _token(self, account: ResolvedAccount) -> str:
        token = self._token_supplier(account) if self._token_supplier else None
        if not token:
            raise MailNotConnectedError(
                f"No live Gmail session for {account.email}. Connect the account first. "
                "Nothing was sent."
            )
        return token

    async def send_message(self, account: ResolvedAccount, to: str, subject: str, body: str) -> dict[str, Any]:
        """Transmit one message. Returns a clearly-labeled live receipt."""
        if self._client is None:
            raise MailNotConnectedError("Gmail has no HTTP client in this context; connect first.")
        payload = build_gmail_send_payload(account.email, to, subject, body)
        token = self._token(account)
        response = await self._client.post(
            f"{self._api_base}/gmail/v1/users/me/messages/send",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise MailNotConnectedError("Gmail rejected the session. Reconnect the account.")
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail send failed with HTTP {response.status_code}.")
        data = response.json()
        message_id = data.get("id", "") if isinstance(data, dict) else ""
        return {
            "provider": "gmail",
            "account": account.email,
            "to": to.strip(),
            "message_id": str(message_id),
            "fake": False,
            "live": True,
            "label": LIVE_SEND_LABEL,
        }
