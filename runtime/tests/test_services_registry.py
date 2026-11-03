"""Phase 5 M3+M4: service registry, account metadata, fake provider."""

from __future__ import annotations

import json
import time

import pytest

from runtime.services.fake_provider import FAKE_LABEL, FakeMailProvider
from runtime.services.registry import STATUS_CONNECTED, STATUS_EXPIRED, STATUS_REVOKED, ServiceRegistry


def make_registry(tmp_path):
    return ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")


def test_providers_expose_capability_declarations(tmp_path):
    registry = make_registry(tmp_path)
    providers = {descriptor["id"]: descriptor for descriptor in registry.providers()}
    assert set(providers) == {"gmail", "outlook", "fake-mail"}
    for descriptor in providers.values():
        assert descriptor["capabilities"], "capabilities must be declared"
        assert "mail.read" in descriptor["capabilities"]
        assert descriptor["scopes"], "scopes must be declared"
    assert "gmail.readonly" in providers["gmail"]["scopes"][2]
    assert providers["fake-mail"]["auth"] == "fake-local"


def test_describe_unknown_provider_raises(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(ValueError, match="Unknown service provider"):
        registry.describe("example-mail")


def test_connect_records_non_secret_metadata(tmp_path):
    registry = make_registry(tmp_path)
    account = registry.connect("gmail", "Ada@Example.Test")
    assert account.provider == "gmail"
    assert account.email == "ada@example.test"
    assert account.status == STATUS_CONNECTED
    assert "gmail.readonly" in account.scopes[2]
    stored = json.loads((tmp_path / "services" / "accounts.json").read_text(encoding="utf-8"))
    blob = json.dumps(stored).lower()
    assert "token" not in blob and "secret" not in blob and "password" not in blob


def test_connect_rejects_bad_email_and_unknown_provider(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(ValueError, match="valid account email"):
        registry.connect("gmail", "not-an-email")
    with pytest.raises(ValueError, match="Unknown service provider"):
        registry.connect("example-mail", "ada@example.test")


def test_multi_account_support_same_provider(tmp_path):
    registry = make_registry(tmp_path)
    registry.connect("gmail", "ada@example.test")
    registry.connect("gmail", "grace@example.test")
    emails = [account.email for account in registry.list_accounts()]
    assert emails == ["ada@example.test", "grace@example.test"]


def test_expired_status_derived_from_expiry(tmp_path):
    registry = make_registry(tmp_path)
    account = registry.connect("outlook", "ada@example.test", expires_at=int(time.time()) - 10)
    assert registry.effective_status(account) == STATUS_EXPIRED
    refreshed = registry.refresh("outlook", "ada@example.test", int(time.time()) + 3600)
    assert registry.effective_status(refreshed) == STATUS_CONNECTED


def test_revoked_status_wins_over_valid_expiry(tmp_path):
    registry = make_registry(tmp_path)
    registry.connect("gmail", "ada@example.test", expires_at=int(time.time()) + 3600)
    revoked = registry.revoke("gmail", "ada@example.test")
    assert revoked.status == STATUS_REVOKED
    assert registry.effective_status(revoked) == STATUS_REVOKED
    with pytest.raises(ValueError, match="revoked"):
        registry.refresh("gmail", "ada@example.test", int(time.time()) + 7200)


def test_disconnect_removes_metadata(tmp_path):
    registry = make_registry(tmp_path)
    registry.connect("gmail", "ada@example.test")
    assert registry.disconnect("gmail", "ada@example.test") is True
    assert registry.get("gmail", "ada@example.test") is None
    assert registry.disconnect("gmail", "ada@example.test") is False


def test_registry_round_trips_through_store(tmp_path):
    registry = make_registry(tmp_path)
    registry.connect("fake-mail", "demo.person@example.test")
    reloaded = make_registry(tmp_path)
    account = reloaded.get("fake-mail", "demo.person@example.test")
    assert account is not None and account.status == STATUS_CONNECTED


def test_fake_provider_labels_every_payload():
    provider = FakeMailProvider()
    assert provider.provider_id == "fake-mail"
    listing = provider.list_messages("demo.person@example.test")
    assert listing["label"] == FAKE_LABEL and listing["fake"] is True
    assert listing["messages"], "fake mailbox must ship fixtures"
    for message in listing["messages"]:
        assert message["fake"] is True
        assert message["label"] == FAKE_LABEL
        assert message["subject"].startswith(f"[{FAKE_LABEL}]")
        assert "example.test" in message["from"]


def test_fake_provider_get_and_search(tmp_path):
    provider = FakeMailProvider()
    message = provider.get_message("demo.person@example.test", "fake-msg-001")
    assert message["label"] == FAKE_LABEL
    with pytest.raises(KeyError):
        provider.get_message("demo.person@example.test", "nope")
    results = provider.search("demo.person@example.test", "world models")
    assert results["label"] == FAKE_LABEL
    assert [match["id"] for match in results["messages"]] == ["fake-msg-002"]
    empty = provider.search("demo.person@example.test", "zzz-no-such-content")
    assert empty["messages"] == [] and empty["label"] == FAKE_LABEL
    with pytest.raises(ValueError, match="search query"):
        provider.search("demo.person@example.test", "   ")


def test_fake_provider_rejects_bad_account():
    provider = FakeMailProvider()
    with pytest.raises(ValueError, match="valid fake account email"):
        provider.list_messages("not-an-email")
