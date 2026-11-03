"""Phase 5 M9 — Scenario B: fake-provider OAuth connect + read + revoke.

Runnable end-to-end through the token-authenticated HTTP API: the registry
records the connected account (the metadata half of an OAuth connect; tokens
stay in the Rust keychain, covered by ``cargo test`` oauth tests), mail reads
flow through the fake adapter with clear FAKE labels, and revoke/disconnect
shut reads off without leaking messages. Uses fictional ``example.test``
fixtures only — no live mail, no keychain access.
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.scheduler import RuntimeService
from runtime.services.fake_provider import FAKE_LABEL
from runtime.services.registry import ServiceRegistry
from runtime.store import RuntimeStore

ACCOUNT = "demo.person@example.test"


def _headers() -> dict:
    return {"Authorization": "Bearer " + os.environ["KEPLERV_RUNTIME_TOKEN"]}


def _service(tmp_path) -> RuntimeService:
    return RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=ServiceRegistry(store_path=tmp_path / "services" / "accounts.json"),
        mail_connectors={"fake-mail": FakeMailAdapter()},
    )


def test_scenario_b_connect_lists_no_secrets(tmp_path):
    """OAuth-connect metadata half: provider advertised, account connected, no secrets."""
    service = _service(tmp_path)
    descriptor = service._services().describe("fake-mail")
    assert descriptor["auth"] == "fake-local"
    assert "mail.read" in descriptor["capabilities"]

    account = service._services().connect("fake-mail", ACCOUNT)
    assert account.status == "connected"

    with TestClient(create_app(service)) as client:
        denied = client.get("/accounts")
        response = client.get("/accounts", headers=_headers())
    assert denied.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert "fake-mail" in {provider["id"] for provider in payload["providers"]}
    assert payload["accounts"][0]["email"] == ACCOUNT
    assert payload["accounts"][0]["status"] == "connected"
    blob = response.text.lower()
    assert "token" not in blob and "secret" not in blob and "password" not in blob


def test_scenario_b_read_search_and_thread_are_fake_labeled(tmp_path):
    """Reads flow through the fake adapter; every payload is labeled, none live."""
    service = _service(tmp_path)
    service._services().connect("fake-mail", ACCOUNT)
    with TestClient(create_app(service)) as client:
        listing = client.get("/mail/messages", headers=_headers())
        search = client.get("/mail/messages", params={"q": "enrollment"}, headers=_headers())
        empty_search = client.get("/mail/messages", params={"q": "no-such-topic-xyz"}, headers=_headers())
        one = client.get("/mail/messages/fake-msg-001", headers=_headers())
        missing = client.get("/mail/messages/does-not-exist", headers=_headers())
        thread = client.get("/mail/threads/fake-thread-enrollment", headers=_headers())
    assert listing.status_code == 200
    assert listing.json()["label"] == FAKE_LABEL
    assert listing.json()["fake"] is True
    messages = listing.json()["messages"]
    assert len(messages) == 3
    assert all(message["fake"] is True and message["label"] == FAKE_LABEL for message in messages)
    assert all(FAKE_LABEL in message["subject"] for message in messages)

    assert search.status_code == 200
    assert search.json()["messages"], "enrollment fixture must match"
    assert empty_search.json()["messages"] == []

    assert one.status_code == 200
    assert one.json()["id"] == "fake-msg-001"
    assert one.json()["fake"] is True
    assert one.json()["account"] == ACCOUNT
    assert missing.status_code == 404

    assert thread.status_code == 200
    assert thread.json()["thread_id"] == "fake-thread-enrollment"
    assert len(thread.json()["messages"]) == 2
    assert thread.json()["fake"] is True


def test_scenario_b_revoke_blocks_reads_without_leaking(tmp_path):
    """Revoke wins immediately: reads become 403 and carry no message content."""
    service = _service(tmp_path)
    service._services().connect("fake-mail", ACCOUNT)
    with TestClient(create_app(service)) as client:
        before = client.get("/mail/messages", headers=_headers())
    assert before.status_code == 200

    service._services().revoke("fake-mail", ACCOUNT)
    with TestClient(create_app(service)) as client:
        after = client.get("/mail/messages", headers=_headers())
        single = client.get("/mail/messages/fake-msg-001", headers=_headers())
        accounts = client.get("/accounts", headers=_headers())
    assert after.status_code == 403
    assert "revoked" in after.json()["detail"]
    assert "messages" not in after.json()["detail"]
    assert single.status_code == 403
    assert accounts.json()["accounts"][0]["status"] == "revoked"


def test_scenario_b_disconnect_returns_to_no_connection(tmp_path):
    """Disconnect drops metadata; reads go back to 404 with nothing fabricated."""
    service = _service(tmp_path)
    service._services().connect("fake-mail", ACCOUNT)
    assert service._services().disconnect("fake-mail", ACCOUNT) is True
    with TestClient(create_app(service)) as client:
        response = client.get("/mail/messages", headers=_headers())
        accounts = client.get("/accounts", headers=_headers())
    assert response.status_code == 404
    assert "messages" not in response.json()["detail"]
    assert accounts.json()["accounts"] == []
