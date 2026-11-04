"""Outlook connector using the official Microsoft Graph API shapes.

Read endpoints (all HTTPS, all GET):
- ``GET /me/messages`` (list; ``$top``/``$select``/``$orderby``)
- ``GET /me/messages?$search="..."`` (search; ``ConsistencyLevel: eventual``)
- ``GET /me/messages/{id}`` (read)
- ``GET /me/messages?$filter=conversationId eq '...'`` (thread)

Send endpoint (live transmission, approval-gated by the caller):
- ``POST /me/sendMail`` (delegated ``Mail.Send``)

Read scope: ``Mail.Read``. Send scope: ``Mail.Send``.

:class:`OutlookConnector` stays read-only by design: transmission lives only
in :class:`OutlookSender`, which the scheduler calls after an AIP grant plus
explicit human approval. Neither class fabricates messages. Live tokens stay
in the Rust keychain and are injected per request over the trusted authed
``127.0.0.1`` sidecar channel; without a token supplier every method raises
:class:`MailNotConnectedError` instead of returning anything.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from runtime.connectors.base import (
    LIVE_SEND_LABEL,
    MailNotConnectedError,
    ResolvedAccount,
    normalize_message,
)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
OUTLOOK_READ_SCOPE = "Mail.Read"
OUTLOOK_SEND_SCOPE = "Mail.Send"
MINIMAL_SCOPES = ["openid", "email", OUTLOOK_READ_SCOPE]
SEND_SCOPES = ["openid", "email", OUTLOOK_READ_SCOPE, OUTLOOK_SEND_SCOPE]

_MESSAGE_SELECT = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,bodyPreview,body"
)


def _check_api_base(api_base: str) -> str:
    """Allow the official HTTPS endpoint plus loopback overrides for tests."""
    parts = urlsplit(api_base)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host and not parts.username and not parts.password:
        return api_base.rstrip("/")
    if parts.scheme == "http" and host in {"127.0.0.1", "localhost"} and not parts.username and not parts.password:
        return api_base.rstrip("/")
    raise ValueError("The Graph API base must be the official HTTPS endpoint or a loopback test URL.")


def _recipients(raw: dict[str, Any]) -> list[str]:
    addresses: list[str] = []
    for entry in raw.get("toRecipients", []) or []:
        address = ((entry or {}).get("emailAddress", {}) or {}).get("address", "")
        if address:
            addresses.append(str(address))
    return addresses


def parse_outlook_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert one Graph ``message`` resource to the shared mail shape."""
    sender = ((raw.get("from", {}) or {}).get("emailAddress", {}) or {}).get("address", "")
    body = raw.get("body", {}) or {}
    return normalize_message(
        {
            "id": raw.get("id", ""),
            "thread_id": raw.get("conversationId", ""),
            "from": str(sender),
            "to": _recipients(raw),
            "subject": raw.get("subject", ""),
            "date": raw.get("receivedDateTime", ""),
            "snippet": raw.get("bodyPreview", ""),
            "body": str(body.get("content", "")),
        },
        provider="outlook",
    )


class OutlookConnector:
    """Read-only Outlook access. No send/delete methods exist on this class."""

    provider_id = "outlook"

    def __init__(
        self,
        client: Any | None = None,
        token_supplier: Callable[[ResolvedAccount], str | None] | None = None,
        api_base: str = GRAPH_API_BASE,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier
        self._api_base = _check_api_base(api_base)

    def _token(self, account: ResolvedAccount) -> str:
        token = self._token_supplier(account) if self._token_supplier else None
        if not token:
            raise MailNotConnectedError(
                f"No live Outlook session for {account.email}. Connect the account first — "
                "no messages are shown without a connection."
            )
        return token

    def _require_client(self) -> Any:
        if self._client is None:
            raise MailNotConnectedError("Outlook has no HTTP client in this context; connect first.")
        return self._client

    async def _get(
        self,
        account: ResolvedAccount,
        path: str,
        params: dict[str, Any],
        *,
        consistency_level: bool = False,
    ) -> dict[str, Any]:
        client = self._require_client()
        token = self._token(account)
        headers = {"Authorization": f"Bearer {token}"}
        if consistency_level:
            headers["ConsistencyLevel"] = "eventual"
        response = await client.get(f"{self._api_base}{path}", params=params, headers=headers)
        if response.status_code == 401:
            raise MailNotConnectedError("Outlook rejected the session. Reconnect the account.")
        if response.status_code >= 400:
            raise RuntimeError(f"Outlook request failed with HTTP {response.status_code}.")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def list_messages(self, account: ResolvedAccount, limit: int = 10) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 25))
        payload = await self._get(
            account,
            "/me/messages",
            {"$top": bounded, "$select": _MESSAGE_SELECT, "$orderby": "receivedDateTime desc"},
        )
        entries = payload.get("value", []) or []
        return {
            "provider": "outlook",
            "account": account.email,
            "fake": False,
            "messages": [parse_outlook_message(entry) for entry in entries[:bounded]],
        }

    async def search(self, account: ResolvedAccount, query: str, limit: int = 10) -> dict[str, Any]:
        needle = query.strip()
        if not needle:
            raise ValueError("Enter a search query.")
        bounded = max(1, min(int(limit), 25))
        payload = await self._get(
            account,
            "/me/messages",
            {"$search": f'"{needle[:500]}"', "$top": bounded, "$select": _MESSAGE_SELECT},
            consistency_level=True,
        )
        entries = payload.get("value", []) or []
        return {
            "provider": "outlook",
            "account": account.email,
            "fake": False,
            "query": needle,
            "messages": [parse_outlook_message(entry) for entry in entries[:bounded]],
        }

    async def get_message(self, account: ResolvedAccount, message_id: str) -> dict[str, Any]:
        if not message_id.strip():
            raise ValueError("Enter a message id.")
        payload = await self._get(
            account, f"/me/messages/{message_id.strip()}", {"$select": _MESSAGE_SELECT}
        )
        if not payload.get("id"):
            raise KeyError(f"Unknown Outlook message '{message_id}'.")
        return parse_outlook_message(payload)

    async def get_thread(self, account: ResolvedAccount, thread_id: str) -> dict[str, Any]:
        needle = thread_id.strip()
        if not needle:
            raise ValueError("Enter a thread id.")
        if "'" in needle or len(needle) > 256:
            raise ValueError("Enter a valid thread id.")
        payload = await self._get(
            account,
            "/me/messages",
            {
                "$filter": f"conversationId eq '{needle}'",
                "$top": 25,
                "$select": _MESSAGE_SELECT,
                "$orderby": "receivedDateTime desc",
            },
        )
        parts = payload.get("value", []) or []
        if not parts:
            raise KeyError(f"Unknown Outlook thread '{thread_id}'.")
        return {
            "provider": "outlook",
            "account": account.email,
            "fake": False,
            "thread_id": needle,
            "messages": [parse_outlook_message(part) for part in parts],
        }


def build_outlook_send_payload(to: str, subject: str, body: str) -> dict[str, Any]:
    """Build the ``/me/sendMail`` request body. Pure helper for unit tests."""
    to = to.strip()
    subject = subject.strip()
    if "@" not in to:
        raise ValueError("Enter a valid recipient address.")
    if not subject or not body.strip():
        raise ValueError("Subject and body are required.")
    return {
        "message": {
            "subject": subject[:300],
            "body": {"contentType": "Text", "content": body[:2000]},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }


class OutlookSender:
    """Live Outlook transmission over ``POST /me/sendMail``.

    Deliberately separate from the read-only :class:`OutlookConnector` so
    the read path can never transmit. Callers (the scheduler's
    ``execute_service_send``) must enforce the AIP grant plus explicit human
    approval before calling :meth:`send_message`.
    """

    provider_id = "outlook"

    def __init__(
        self,
        client: Any | None = None,
        token_supplier: Callable[[ResolvedAccount], str | None] | None = None,
        api_base: str = GRAPH_API_BASE,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier
        self._api_base = _check_api_base(api_base)

    def _token(self, account: ResolvedAccount) -> str:
        token = self._token_supplier(account) if self._token_supplier else None
        if not token:
            raise MailNotConnectedError(
                f"No live Outlook session for {account.email}. Connect the account first. "
                "Nothing was sent."
            )
        return token

    async def send_message(self, account: ResolvedAccount, to: str, subject: str, body: str) -> dict[str, Any]:
        """Transmit one message. Returns a clearly-labeled live receipt."""
        if self._client is None:
            raise MailNotConnectedError("Outlook has no HTTP client in this context; connect first.")
        payload = build_outlook_send_payload(to, subject, body)
        token = self._token(account)
        response = await self._client.post(
            f"{self._api_base}/me/sendMail",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise MailNotConnectedError("Outlook rejected the session. Reconnect the account.")
        if response.status_code >= 400:
            raise RuntimeError(f"Outlook send failed with HTTP {response.status_code}.")
        # Graph answers 202 with no body: there is no provider message id to
        # echo, so the receipt carries the accepted status instead of one.
        return {
            "provider": "outlook",
            "account": account.email,
            "to": to.strip(),
            "status": f"accepted (HTTP {response.status_code})",
            "fake": False,
            "live": True,
            "label": LIVE_SEND_LABEL,
        }
