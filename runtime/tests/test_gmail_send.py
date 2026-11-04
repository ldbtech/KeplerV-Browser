"""Gmail send path: wire format and fail-closed behavior.

No live calls: :class:`GmailSender` is driven through
``httpx.MockTransport`` with fictional fixtures.
"""

from __future__ import annotations

import asyncio
import base64
import json as json_module

import httpx
import pytest

from runtime.connectors.base import LIVE_SEND_LABEL, MailNotConnectedError, ResolvedAccount
from runtime.connectors.gmail import (
    GMAIL_SEND_SCOPE,
    GmailSender,
    build_gmail_send_payload,
)

ACCOUNT = ResolvedAccount(provider="gmail", email="ada@example.test")


def _sender(handler, *, token: str | None = "test-token") -> GmailSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GmailSender(client=client, token_supplier=lambda account: token)


def test_gmail_send_scope():
    assert GMAIL_SEND_SCOPE == "https://www.googleapis.com/auth/gmail.send"


def test_build_gmail_send_payload_is_base64url_rfc2822():
    payload = build_gmail_send_payload("ada@example.test", "friend@example.test", "Fictional hello", "Made-up body.")
    decoded = base64.urlsafe_b64decode(payload["raw"]).decode("utf-8")
    assert "To: friend@example.test" in decoded
    assert "Fictional hello" in decoded
    assert "Made-up body." in decoded
    with pytest.raises(ValueError, match="valid sender and recipient"):
        build_gmail_send_payload("ada@example.test", "not-an-email", "s", "b")
    with pytest.raises(ValueError, match="Subject and body"):
        build_gmail_send_payload("ada@example.test", "a@example.test", "  ", "b")


def test_gmail_send_posts_official_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json_module.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"id": "sent-1", "labelIds": ["SENT"]})

    receipt = asyncio.run(
        _sender(handler).send_message(ACCOUNT, "friend@example.test", "Fictional hello", "Made-up body.")
    )
    assert seen["path"] == "/gmail/v1/users/me/messages/send"
    assert seen["auth"] == "Bearer test-token"
    decoded = base64.urlsafe_b64decode(seen["body"]["raw"]).decode("utf-8")
    assert "To: friend@example.test" in decoded
    assert receipt["message_id"] == "sent-1"
    assert receipt["fake"] is False and receipt["live"] is True
    assert receipt["label"] == LIVE_SEND_LABEL
    assert receipt["account"] == "ada@example.test"


def test_gmail_send_without_session_sends_nothing():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call may happen without a session")

    with pytest.raises(MailNotConnectedError, match="Nothing was sent"):
        asyncio.run(_sender(handler, token=None).send_message(ACCOUNT, "a@example.test", "s", "b"))


def test_gmail_send_unauthorized_maps_to_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid credentials"})

    with pytest.raises(MailNotConnectedError, match="Reconnect the account"):
        asyncio.run(_sender(handler).send_message(ACCOUNT, "a@example.test", "s", "b"))


def test_gmail_send_server_error_is_loud():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "backend error"})

    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(_sender(handler).send_message(ACCOUNT, "a@example.test", "s", "b"))
