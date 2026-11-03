"""Phase 5 M5+M7: Gmail read-only connector, Outlook skeleton, fake adapter."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from runtime.connectors.base import MailNotConnectedError, ResolvedAccount, resolve_account, AccountRef, AmbiguousAccountError
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.connectors.gmail import GMAIL_READONLY_SCOPE, GmailConnector, parse_gmail_message
from runtime.connectors.outlook import OUTLOOK_SKELETON_LABEL, OutlookConnector, OutlookSkeletonError
from runtime.services.fake_provider import FAKE_LABEL

ACCOUNT = ResolvedAccount(provider="gmail", email="ada@example.test")


def _gmail_full_message() -> dict:
    body = base64.urlsafe_b64encode(b"Hello from the fictional test.").decode("ascii").rstrip("=")
    return {
        "id": "msg-1",
        "threadId": "thread-1",
        "snippet": "Hello from the fictional",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Registrar Office <registrar@example.test>"},
                {"name": "To", "value": "ada@example.test"},
                {"name": "Subject", "value": "Fictional welcome"},
                {"name": "Date", "value": "Mon, 01 Sep 2026 09:00:00 +0000"},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
        },
    }


def _gmail_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _connector(handler, *, token: str | None = "test-token") -> GmailConnector:
    return GmailConnector(client=_gmail_client(handler), token_supplier=lambda account: token)


def test_gmail_uses_minimal_readonly_scope():
    assert GMAIL_READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_gmail_list_uses_official_list_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"messages": [{"id": "msg-1"}, {"id": "msg-2"}]})

    token = "test-token"
    result = asyncio.run(_connector(handler, token=token).list_messages(ACCOUNT, limit=10))
    assert seen["path"] == "/gmail/v1/users/me/messages"
    assert seen["params"]["maxResults"] == "10"
    assert "q" not in seen["params"]
    assert seen["auth"] == "Bearer " + token
    assert [message["id"] for message in result["messages"]] == ["msg-1", "msg-2"]
    assert result["fake"] is False


def test_gmail_search_passes_query_through():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"messages": [{"id": "msg-9"}]})

    result = asyncio.run(_connector(handler).search(ACCOUNT, "enrollment"))
    assert seen["params"]["q"] == "enrollment"
    assert result["query"] == "enrollment"
    assert result["messages"][0]["id"] == "msg-9"
    with pytest.raises(ValueError, match="search query"):
        asyncio.run(_connector(handler).search(ACCOUNT, "  "))


def test_gmail_get_message_requests_full_format_and_parses_headers():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_gmail_full_message())

    message = asyncio.run(_connector(handler).get_message(ACCOUNT, "msg-1"))
    assert seen["path"] == "/gmail/v1/users/me/messages/msg-1"
    assert seen["params"] == {"format": "full"}
    assert message["subject"] == "Fictional welcome"
    assert message["from"] == "Registrar Office <registrar@example.test>"
    assert message["to"] == ["ada@example.test"]
    assert message["thread_id"] == "thread-1"
    assert "fictional test" in message["body"]
    assert message["fake"] is False


def test_gmail_get_thread_returns_thread_members():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gmail/v1/users/me/threads/thread-1"
        payload = dict(_gmail_full_message())
        return httpx.Response(200, json={"id": "thread-1", "messages": [payload, payload]})

    thread = asyncio.run(_connector(handler).get_thread(ACCOUNT, "thread-1"))
    assert thread["thread_id"] == "thread-1"
    assert len(thread["messages"]) == 2


def test_gmail_unknown_message_raises_without_fabrication():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(KeyError, match="Unknown Gmail message"):
        asyncio.run(_connector(handler).get_message(ACCOUNT, "missing"))


def test_gmail_without_token_never_returns_messages():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("no HTTP call may happen without a session")

    connector = _connector(handler, token=None)
    for call in (
        lambda: connector.list_messages(ACCOUNT),
        lambda: connector.search(ACCOUNT, "anything"),
        lambda: connector.get_message(ACCOUNT, "msg-1"),
        lambda: connector.get_thread(ACCOUNT, "thread-1"),
    ):
        with pytest.raises(MailNotConnectedError, match="Connect the account first"):
            asyncio.run(call())


def test_gmail_without_client_reports_disconnected():
    connector = GmailConnector(token_supplier=lambda account: "test-token")
    with pytest.raises(MailNotConnectedError, match="no HTTP client"):
        asyncio.run(connector.list_messages(ACCOUNT))


def test_gmail_unauthorized_maps_to_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid credentials"})

    with pytest.raises(MailNotConnectedError, match="Reconnect the account"):
        asyncio.run(_connector(handler).list_messages(ACCOUNT))


def test_gmail_has_no_send_path():
    connector = GmailConnector()
    for name in ("send", "send_message", "send_email", "delete", "trash"):
        assert not hasattr(connector, name), f"read-only connector must not grow {name}"


def test_parse_gmail_message_prefers_plain_text():
    html = base64.urlsafe_b64encode(b"<p>html</p>").decode("ascii")
    plain = base64.urlsafe_b64encode(b"plain words").decode("ascii")
    message = parse_gmail_message({
        "id": "m", "threadId": "t",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "Subject", "value": "s"}],
            "parts": [
                {"mimeType": "text/html", "body": {"data": html}},
                {"mimeType": "text/plain", "body": {"data": plain}},
            ],
        },
    })
    assert message["body"] == "plain words"


def test_outlook_skeleton_labels_every_operation():
    connector = OutlookConnector()
    assert connector.provider_id == "outlook"
    account = ResolvedAccount(provider="outlook", email="ada@example.test")
    for call in (
        lambda: connector.list_messages(account),
        lambda: connector.search(account, "anything"),
        lambda: connector.get_message(account, "msg-1"),
        lambda: connector.get_thread(account, "thread-1"),
    ):
        with pytest.raises(OutlookSkeletonError) as error:
            asyncio.run(call())
        assert OUTLOOK_SKELETON_LABEL in str(error.value)


def test_fake_adapter_carries_fake_label_on_everything():
    adapter = FakeMailAdapter()
    account = ResolvedAccount(provider="fake-mail", email="demo.person@example.test")
    listing = asyncio.run(adapter.list_messages(account))
    assert listing["label"] == FAKE_LABEL and listing["fake"] is True
    assert all(message["fake"] is True and message["label"] == FAKE_LABEL for message in listing["messages"])
    found = asyncio.run(adapter.search(account, "enrollment"))
    assert found["messages"], "fixture search must match the enrollment thread"
    one = asyncio.run(adapter.get_message(account, "fake-msg-001"))
    assert one["fake"] is True
    thread = asyncio.run(adapter.get_thread(account, "fake-thread-enrollment"))
    assert thread["fake"] is True and len(thread["messages"]) == 2
    with pytest.raises(KeyError, match="Unknown fake message"):
        asyncio.run(adapter.get_message(account, "nope"))
    with pytest.raises(KeyError, match="Unknown fake thread"):
        asyncio.run(adapter.get_thread(account, "nope"))


def test_resolve_account_default_explicit_and_ambiguous(tmp_path):
    from runtime.services.registry import ServiceRegistry

    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    registry.connect("fake-mail", "demo.person@example.test")
    only = resolve_account(AccountRef(), registry.list_accounts())
    assert (only.provider, only.email) == ("fake-mail", "demo.person@example.test")
    registry.connect("gmail", "ada@example.test")
    explicit = resolve_account(AccountRef(provider="gmail"), registry.list_accounts())
    assert explicit.email == "ada@example.test"
    with pytest.raises(AmbiguousAccountError) as error:
        resolve_account(AccountRef(), registry.list_accounts())
    assert len(error.value.candidates) == 2
    registry._accounts.clear()
    with pytest.raises(MailNotConnectedError, match="No service account is connected"):
        resolve_account(AccountRef(), registry.list_accounts())
