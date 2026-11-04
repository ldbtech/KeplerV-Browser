"""Outlook connector: real Microsoft Graph shapes over MockTransport.

No live calls: every test drives :class:`OutlookConnector` /
:class:`OutlookSender` through ``httpx.MockTransport`` with fictional
fixtures. Fail-closed behavior (no token / 401) is asserted for every path.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from runtime.connectors.base import LIVE_SEND_LABEL, MailNotConnectedError, ResolvedAccount
from runtime.connectors.outlook import (
    GRAPH_API_BASE,
    OUTLOOK_READ_SCOPE,
    OUTLOOK_SEND_SCOPE,
    OutlookConnector,
    OutlookSender,
    build_outlook_send_payload,
    parse_outlook_message,
)

ACCOUNT = ResolvedAccount(provider="outlook", email="ada@example.test")


def _graph_message(message_id: str = "msg-1", conversation: str = "conv-1") -> dict:
    return {
        "id": message_id,
        "conversationId": conversation,
        "from": {"emailAddress": {"address": "registrar@example.test", "name": "Registrar"}},
        "toRecipients": [{"emailAddress": {"address": "ada@example.test"}}],
        "subject": "Fictional welcome",
        "receivedDateTime": "2026-09-01T09:00:00Z",
        "bodyPreview": "Hello from the fictional test.",
        "body": {"contentType": "Text", "content": "Hello from the fictional test."},
    }


def _connector(handler, *, token: str | None = "test-token") -> OutlookConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OutlookConnector(client=client, token_supplier=lambda account: token)


def _sender(handler, *, token: str | None = "test-token") -> OutlookSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OutlookSender(client=client, token_supplier=lambda account: token)


def test_outlook_scopes_and_base():
    assert GRAPH_API_BASE == "https://graph.microsoft.com/v1.0"
    assert OUTLOOK_READ_SCOPE == "Mail.Read"
    assert OUTLOOK_SEND_SCOPE == "Mail.Send"


def test_outlook_list_uses_graph_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"value": [_graph_message("msg-1"), _graph_message("msg-2")]})

    result = asyncio.run(_connector(handler).list_messages(ACCOUNT, limit=10))
    assert seen["path"] == "/v1.0/me/messages"
    assert seen["params"]["$top"] == "10"
    assert "$select" in seen["params"]
    assert seen["auth"] == "Bearer test-token"
    assert [message["id"] for message in result["messages"]] == ["msg-1", "msg-2"]
    assert result["fake"] is False
    first = result["messages"][0]
    assert first["subject"] == "Fictional welcome"
    assert first["from"] == "registrar@example.test"
    assert first["to"] == ["ada@example.test"]
    assert first["thread_id"] == "conv-1"


def test_outlook_search_uses_search_query():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["consistency"] = request.headers.get("ConsistencyLevel")
        return httpx.Response(200, json={"value": [_graph_message("msg-9")]})

    result = asyncio.run(_connector(handler).search(ACCOUNT, "enrollment"))
    assert seen["params"]["$search"] == '"enrollment"'
    assert seen["consistency"] == "eventual"
    assert result["query"] == "enrollment"
    assert result["messages"][0]["id"] == "msg-9"
    with pytest.raises(ValueError, match="search query"):
        asyncio.run(_connector(handler).search(ACCOUNT, "  "))


def test_outlook_get_message_and_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1.0/me/messages/msg-1"
        return httpx.Response(200, json=_graph_message("msg-1"))

    message = asyncio.run(_connector(handler).get_message(ACCOUNT, "msg-1"))
    assert message["subject"] == "Fictional welcome"
    assert "fictional test" in message["body"]
    assert message["fake"] is False

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(KeyError, match="Unknown Outlook message"):
        asyncio.run(_connector(empty).get_message(ACCOUNT, "missing"))


def test_outlook_get_thread_filters_conversation():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"value": [_graph_message("a"), _graph_message("b")]})

    thread = asyncio.run(_connector(handler).get_thread(ACCOUNT, "conv-1"))
    assert seen["params"]["$filter"] == "conversationId eq 'conv-1'"
    assert thread["thread_id"] == "conv-1"
    assert len(thread["messages"]) == 2

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    with pytest.raises(KeyError, match="Unknown Outlook thread"):
        asyncio.run(_connector(empty).get_thread(ACCOUNT, "conv-1"))
    with pytest.raises(ValueError, match="thread id"):
        asyncio.run(_connector(handler).get_thread(ACCOUNT, "conv-1' OR '1'='1"))


def test_outlook_without_token_never_returns_messages():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call may happen without a session")

    connector = _connector(handler, token=None)
    for call in (
        lambda: connector.list_messages(ACCOUNT),
        lambda: connector.search(ACCOUNT, "anything"),
        lambda: connector.get_message(ACCOUNT, "msg-1"),
        lambda: connector.get_thread(ACCOUNT, "conv-1"),
    ):
        with pytest.raises(MailNotConnectedError, match="Connect the account first"):
            asyncio.run(call())


def test_outlook_unauthorized_maps_to_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(MailNotConnectedError, match="Reconnect the account"):
        asyncio.run(_connector(handler).list_messages(ACCOUNT))


def test_outlook_connector_has_no_send_path():
    connector = OutlookConnector()
    for name in ("send", "send_message", "send_email", "delete", "trash"):
        assert not hasattr(connector, name), f"read-only connector must not grow {name}"


def test_outlook_api_base_rejects_non_loopback_http():
    with pytest.raises(ValueError, match="API base"):
        OutlookConnector(api_base="http://example.com/graph")


def test_parse_outlook_message_handles_missing_fields():
    message = parse_outlook_message({"id": "m"})
    assert message["provider"] == "outlook"
    assert message["to"] == []
    assert message["fake"] is False


def test_outlook_send_posts_sendmail_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        import json as json_module

        seen["body"] = json_module.loads(request.content.decode("utf-8"))
        return httpx.Response(202)

    receipt = asyncio.run(
        _sender(handler).send_message(ACCOUNT, "friend@example.test", "Fictional hello", "Made-up body.")
    )
    assert seen["path"] == "/v1.0/me/sendMail"
    assert seen["auth"] == "Bearer test-token"
    payload = seen["body"]
    assert payload["message"]["subject"] == "Fictional hello"
    assert payload["message"]["body"] == {"contentType": "Text", "content": "Made-up body."}
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "friend@example.test"}}]
    assert payload["saveToSentItems"] is True
    assert receipt["fake"] is False and receipt["live"] is True
    assert receipt["label"] == LIVE_SEND_LABEL
    assert receipt["account"] == "ada@example.test"


def test_outlook_send_without_session_sends_nothing():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call may happen without a session")

    with pytest.raises(MailNotConnectedError, match="Nothing was sent"):
        asyncio.run(_sender(handler, token=None).send_message(ACCOUNT, "a@example.test", "s", "b"))


def test_outlook_send_unauthorized_maps_to_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(MailNotConnectedError, match="Reconnect the account"):
        asyncio.run(_sender(handler).send_message(ACCOUNT, "a@example.test", "s", "b"))


def test_build_outlook_send_payload_validates():
    payload = build_outlook_send_payload("friend@example.test", "Hi", "Body")
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == "friend@example.test"
    with pytest.raises(ValueError, match="recipient"):
        build_outlook_send_payload("not-an-email", "Hi", "Body")
    with pytest.raises(ValueError, match="Subject and body"):
        build_outlook_send_payload("a@example.test", "  ", "Body")
