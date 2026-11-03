"""Phase 5 M5+M7: accounts + mail HTTP routes behind the app token."""

from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.scheduler import RuntimeService
from runtime.services.fake_provider import FAKE_LABEL
from runtime.services.registry import ServiceRegistry
from runtime.store import RuntimeStore

def _headers() -> dict:
    return {"Authorization": "Bearer " + os.environ["KEPLERV_RUNTIME_TOKEN"]}


def _service(tmp_path, **kwargs) -> RuntimeService:
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    return RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter(), **kwargs.pop("mail_connectors", {})},
        **kwargs,
    )


def test_routes_require_bearer_token(tmp_path):
    with TestClient(create_app(_service(tmp_path))) as client:
        assert client.get("/accounts").status_code == 401
        assert client.get("/mail/messages").status_code == 401
        assert client.get("/mail/messages/fake-msg-001").status_code == 401


def test_accounts_lists_providers_and_metadata_without_secrets(tmp_path):
    service = _service(tmp_path)
    service._services().connect("fake-mail", "demo.person@example.test")
    with TestClient(create_app(service)) as client:
        response = client.get("/accounts", headers=_headers())
    assert response.status_code == 200
    payload = response.json()
    assert {provider["id"] for provider in payload["providers"]} == {"gmail", "outlook", "fake-mail"}
    assert payload["accounts"][0]["email"] == "demo.person@example.test"
    assert payload["accounts"][0]["status"] == "connected"
    blob = response.text.lower()
    assert "token" not in blob and "secret" not in blob and "password" not in blob


def test_mail_list_and_search_over_fake_provider(tmp_path):
    service = _service(tmp_path)
    service._services().connect("fake-mail", "demo.person@example.test")
    with TestClient(create_app(service)) as client:
        listing = client.get("/mail/messages", headers=_headers())
        search = client.get("/mail/messages", params={"q": "enrollment"}, headers=_headers())
    assert listing.status_code == 200
    assert listing.json()["label"] == FAKE_LABEL
    assert len(listing.json()["messages"]) == 3
    assert search.json()["messages"], "fixture search must match the enrollment thread"
    assert all(message["fake"] for message in search.json()["messages"])


def test_mail_read_and_thread(tmp_path):
    service = _service(tmp_path)
    service._services().connect("fake-mail", "demo.person@example.test")
    with TestClient(create_app(service)) as client:
        one = client.get("/mail/messages/fake-msg-001", headers=_headers())
        missing = client.get("/mail/messages/nope", headers=_headers())
        thread = client.get("/mail/threads/fake-thread-enrollment", headers=_headers())
        missing_thread = client.get("/mail/threads/nope", headers=_headers())
    assert one.status_code == 200
    assert one.json()["id"] == "fake-msg-001"
    assert one.json()["fake"] is True
    assert missing.status_code == 404
    assert thread.status_code == 200
    assert thread.json()["thread_id"] == "fake-thread-enrollment"
    assert len(thread.json()["messages"]) == 2
    assert missing_thread.status_code == 404


def test_mail_without_connection_returns_nothing_fabricated(tmp_path):
    with TestClient(create_app(_service(tmp_path))) as client:
        response = client.get("/mail/messages", headers=_headers())
    assert response.status_code == 404
    assert "messages" not in response.json()["detail"]


def test_mail_ambiguous_accounts_return_candidates(tmp_path):
    service = _service(tmp_path)
    service._services().connect("fake-mail", "demo.person@example.test")
    service._services().connect("fake-mail", "second.person@example.test")
    with TestClient(create_app(service)) as client:
        ambiguous = client.get("/mail/messages", headers=_headers())
        explicit = client.get("/mail/messages", params={"email": "second.person@example.test"}, headers=_headers())
    assert ambiguous.status_code == 409
    assert len(ambiguous.json()["detail"]["candidates"]) == 2
    assert explicit.status_code == 200


def test_mail_revoked_and_expired_accounts_are_forbidden(tmp_path):
    service = _service(tmp_path)
    registry = service._services()
    registry.connect("fake-mail", "gone@example.test")
    registry.revoke("fake-mail", "gone@example.test")
    registry.connect("fake-mail", "old@example.test", expires_at=int(time.time()) - 5)
    with TestClient(create_app(service)) as client:
        revoked = client.get("/mail/messages", params={"email": "gone@example.test"}, headers=_headers())
        expired = client.get("/mail/messages", params={"email": "old@example.test"}, headers=_headers())
    assert revoked.status_code == 403
    assert "revoked" in revoked.json()["detail"]
    assert expired.status_code == 403
    assert "expired" in expired.json()["detail"]


def test_mail_outlook_skeleton_is_labeled_not_live(tmp_path):
    service = _service(tmp_path)
    service._services().connect("outlook", "ada@example.test")
    with TestClient(create_app(service)) as client:
        response = client.get("/mail/messages", params={"email": "ada@example.test"}, headers=_headers())
    assert response.status_code == 400
    assert "OUTLOOK SKELETON" in response.json()["detail"]


def test_mail_gmail_without_session_is_disconnected_not_fabricated(tmp_path):
    service = _service(tmp_path)
    service._services().connect("gmail", "ada@example.test")
    with TestClient(create_app(service)) as client:
        response = client.get("/mail/messages", params={"email": "ada@example.test"}, headers=_headers())
    assert response.status_code == 404
    assert "connect first" in response.json()["detail"].lower()
    assert "messages" not in response.json()["detail"]
