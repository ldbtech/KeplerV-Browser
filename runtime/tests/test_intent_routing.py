"""Phase 5 M5+M7: new intent types, validation, and account routing."""

from __future__ import annotations

import asyncio
import json

from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.models import ModelDecision
from runtime.scheduler import RuntimeService
from runtime.services.registry import ServiceRegistry
from runtime.store import RuntimeStore
from runtime.voice.intent import INTENT_TYPES, VoiceContext, interpret


class ScriptedModel:
    """Fake adapter returning one scripted intent per test."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def generate(self, model, messages, tools):
        return ModelDecision(message=json.dumps(self._payload), usage={})


class ScriptedProviders:
    active_provider = "meta"
    active_model = "scripted-test-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def get(self, provider_id):
        return ScriptedModel(self._payload)


def _run(payload: dict, context: VoiceContext):
    return asyncio.run(interpret("do it", context, ScriptedProviders(payload)))


def _context(**kwargs) -> VoiceContext:
    return VoiceContext(**kwargs)


def test_new_intent_types_are_registered():
    for intent_type in (
        "CONNECT_ACCOUNT", "DISCONNECT_ACCOUNT", "LIST_CONNECTED_ACCOUNTS",
        "OPEN_SERVICE", "SEARCH_EMAIL", "READ_EMAIL", "PREPARE_EMAIL_DRAFT",
        "REQUEST_SEND_EMAIL", "OPEN_MEDIA", "SHOW_SERVICE_PERMISSIONS",
        "REVOKE_AGENT_SERVICE_ACCESS",
    ):
        assert intent_type in INTENT_TYPES


def test_search_email_without_query_asks_for_clarification():
    intent = _run(
        {"type": "SEARCH_EMAIL", "target_id": None, "args": {}, "summary_for_speech": "x"},
        _context(),
    )
    assert intent.needs_clarification is True
    assert "search" in (intent.clarification_question or "").lower()


def test_connect_account_without_provider_asks_for_clarification():
    intent = _run(
        {"type": "CONNECT_ACCOUNT", "target_id": None, "args": {}, "summary_for_speech": "x"},
        _context(),
    )
    assert intent.needs_clarification is True


def test_prepare_draft_without_recipient_asks_for_clarification():
    intent = _run(
        {"type": "PREPARE_EMAIL_DRAFT", "target_id": None, "args": {"subject": "hi"}, "summary_for_speech": "x"},
        _context(),
    )
    assert intent.needs_clarification is True
    assert "addressed" in (intent.clarification_question or "").lower()


def test_read_email_with_known_message_id_passes():
    intent = _run(
        {"type": "READ_EMAIL", "target_id": "fake-msg-001", "args": {},
         "summary_for_speech": "Opening it."},
        _context(messages=[{"id": "fake-msg-001", "subject": "hello"}]),
    )
    assert intent.needs_clarification is False
    assert intent.target_id == "fake-msg-001"


def test_read_email_with_unknown_message_id_asks_for_clarification():
    intent = _run(
        {"type": "READ_EMAIL", "target_id": "msg-invented", "args": {},
         "summary_for_speech": "Opening it."},
        _context(messages=[{"id": "fake-msg-001", "subject": "hello"}]),
    )
    assert intent.needs_clarification is True
    assert intent.target_id is None


def test_account_email_is_a_known_target_id():
    intent = _run(
        {"type": "DISCONNECT_ACCOUNT", "target_id": "ada@example.test", "args": {},
         "summary_for_speech": "Disconnecting."},
        _context(accounts=[{"id": "gmail:ada@example.test", "provider": "gmail",
                             "email": "ada@example.test", "status": "connected"}]),
    )
    assert intent.needs_clarification is False


def test_scheduler_routes_default_single_account(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    registry.connect("fake-mail", "demo.person@example.test")
    service = RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()},
    )
    result = asyncio.run(service.execute_mail_tool({}, "mail.read", {"limit": 10}))
    assert result["ok"] is True
    assert len(result["messages"]) == 3


def test_scheduler_routes_explicit_account(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    registry.connect("fake-mail", "demo.person@example.test")
    registry.connect("fake-mail", "second.person@example.test")
    service = RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()},
    )
    result = asyncio.run(service.execute_mail_tool(
        {}, "mail.search", {"email": "second.person@example.test", "query": "atlas", "limit": 10}))
    assert result["ok"] is True
    assert result["account"] == "second.person@example.test"


def test_scheduler_never_auto_picks_between_accounts(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    registry.connect("fake-mail", "demo.person@example.test")
    registry.connect("fake-mail", "second.person@example.test")
    service = RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()},
    )
    result = asyncio.run(service.execute_mail_tool({}, "mail.read", {"limit": 10}))
    assert result["ok"] is False
    assert result["needs_disambiguation"] is True
    assert len(result["candidates"]) == 2


def test_scheduler_denies_revoked_account_without_mail(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    registry.connect("fake-mail", "gone@example.test")
    registry.revoke("fake-mail", "gone@example.test")
    service = RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()},
    )
    result = asyncio.run(service.execute_mail_tool(
        {}, "mail.read", {"email": "gone@example.test", "limit": 10}))
    assert result["ok"] is False
    assert "revoked" in result["error"]
    assert "messages" not in result


def test_aip_mail_policy(tmp_path):
    from runtime.aip.policy import authorize_tool

    allowed = authorize_tool(
        agent_id="a", registered_agent_id="a", goal_id="g", registered_goal_id="g",
        permissions={"mail.read"}, tool_name="mail.read", arguments={},
        authorized_origin=None)
    assert allowed.outcome == "allow"
    denied = authorize_tool(
        agent_id="a", registered_agent_id="a", goal_id="g", registered_goal_id="g",
        permissions={"browser.read"}, tool_name="mail.search", arguments={},
        authorized_origin=None)
    assert denied.outcome == "deny"
    no_send = authorize_tool(
        agent_id="a", registered_agent_id="a", goal_id="g", registered_goal_id="g",
        permissions={"mail.read"}, tool_name="mail.send", arguments={},
        authorized_origin=None)
    assert no_send.outcome == "deny"


def test_browser_sessions_are_never_shared_between_runs(tmp_path):
    from runtime.browser.controller import BrowserController

    first = BrowserController("run-one", None, tmp_path)
    second = BrowserController("run-two", None, tmp_path)
    assert first is not second
    assert first.authorized_origins is not second.authorized_origins
    first.authorize_origin(("https", "example.test", 443))
    assert ("https", "example.test", 443) not in second.authorized_origins
    # Ephemeral contexts: start() opens a fresh context with no persisted
    # storage_state or profile, so cookies cannot leak across runs.
    import inspect

    source = inspect.getsource(BrowserController.start)
    assert "new_context" in source
    assert "storage_state" not in source
