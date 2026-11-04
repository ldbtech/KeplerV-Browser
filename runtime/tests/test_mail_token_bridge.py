"""Rust keychain -> sidecar token bridge: vault, sync, and live send.

Covers the in-memory bridge vault, the authed ``/accounts/sync`` and
``/accounts/tokens`` routes, the sidecar-only per-request token header, and
the scheduler's live Gmail/Outlook send after grant + approval. No live
calls: provider HTTP is ``httpx.MockTransport`` with fictional fixtures.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.connectors.base import LIVE_SEND_LABEL, ResolvedAccount
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.connectors.gmail import GMAIL_SEND_SCOPE
from runtime.connectors.outlook import OUTLOOK_SEND_SCOPE
from runtime.scheduler import TOOL_NAMES, RuntimeService
from runtime.services.fake_services import FakeServiceAdapter
from runtime.services.mail_token_vault import (
    MailTokenVault,
    request_token_for,
    use_request_token,
)
from runtime.services.registry import (
    PROVIDER_DESCRIPTORS,
    STATUS_CONNECTED,
    STATUS_REVOKED,
    ServiceRegistry,
)
from runtime.store import RuntimeStore

GMAIL_ACCOUNT = "ada@example.test"
EXPIRY = int(time.time()) + 3600


def _headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": "Bearer " + os.environ["KEPLERV_RUNTIME_TOKEN"]}
    if extra:
        headers.update(extra)
    return headers


def _service(tmp_path, **kwargs) -> RuntimeService:
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    return RuntimeService(
        store=RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3"),
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter(), **kwargs.pop("mail_connectors", {})},
        **kwargs,
    )


def _bridge_headers(token: str, provider: str, email: str) -> dict:
    return {
        "X-Keplerv-Mail-Token": token,
        "X-Keplerv-Mail-Provider": provider,
        "X-Keplerv-Mail-Account": email,
    }


# -- vault ------------------------------------------------------------------


def test_vault_holds_tokens_in_memory_only():
    vault = MailTokenVault()
    vault.put("gmail", GMAIL_ACCOUNT, "bridge-token", EXPIRY)
    assert vault.get("gmail", GMAIL_ACCOUNT) == "bridge-token"
    assert vault.get("gmail", "other@example.test") is None
    described = vault.describe()
    assert described == [{"provider": "gmail", "email": GMAIL_ACCOUNT, "expires_at": EXPIRY}]
    assert "bridge-token" not in json.dumps(described)
    assert vault.evict("gmail", GMAIL_ACCOUNT) is True
    assert vault.get("gmail", GMAIL_ACCOUNT) is None
    assert vault.evict("gmail", GMAIL_ACCOUNT) is False


def test_vault_evicts_expired_and_rejects_bad_pushes():
    vault = MailTokenVault()
    vault.put("gmail", GMAIL_ACCOUNT, "short", int(time.time()) + 100)
    assert vault.get("gmail", GMAIL_ACCOUNT, now=int(time.time()) + 101) is None
    assert vault.get("gmail", GMAIL_ACCOUNT) is None
    with pytest.raises(ValueError, match="already expired"):
        vault.put("gmail", GMAIL_ACCOUNT, "old", int(time.time()) - 5)
    with pytest.raises(ValueError, match="empty or too long"):
        vault.put("gmail", GMAIL_ACCOUNT, "", EXPIRY)
    with pytest.raises(ValueError, match="valid provider"):
        vault.put("gmail", "not-an-email", "token", EXPIRY)


def test_request_token_binds_to_one_account_only():
    account = ResolvedAccount(provider="gmail", email=GMAIL_ACCOUNT)
    with use_request_token("gmail", GMAIL_ACCOUNT, "request-token"):
        assert request_token_for("gmail", GMAIL_ACCOUNT) == "request-token"
        assert request_token_for("gmail", "other@example.test") is None
        assert request_token_for("outlook", GMAIL_ACCOUNT) is None
    assert request_token_for("gmail", GMAIL_ACCOUNT) is None
    with use_request_token("gmail", GMAIL_ACCOUNT, None):
        assert request_token_for(account.provider, account.email) is None


# -- sync + token routes -----------------------------------------------------


def test_sync_applies_metadata_and_rejects_tokens(tmp_path):
    service = _service(tmp_path)
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/accounts/sync",
            json={
                "accounts": [
                    {
                        "provider": "gmail",
                        "email": GMAIL_ACCOUNT,
                        "scopes": ["openid", "email"],
                        "expires_at": EXPIRY,
                        "status": STATUS_CONNECTED,
                    }
                ],
                "removed": [],
            },
            headers=_headers(),
        )
        assert response.status_code == 200
        assert response.json() == {"upserted": 1, "removed": 0}
        assert service._services().get("gmail", GMAIL_ACCOUNT) is not None

        smuggled = client.post(
            "/accounts/sync",
            json={
                "accounts": [
                    {
                        "provider": "gmail",
                        "email": "mallory@example.test",
                        "scopes": [],
                        "expires_at": EXPIRY,
                        "status": STATUS_CONNECTED,
                        "access_token": "must-not-store",
                    }
                ],
                "removed": [],
            },
            headers=_headers(),
        )
        assert smuggled.status_code == 422
        assert service._services().get("gmail", "mallory@example.test") is None

        unknown = client.post(
            "/accounts/sync",
            json={"accounts": [], "removed": [{"provider": "gmail", "email": "nobody@example.test"}]},
            headers=_headers(),
        )
        assert unknown.json() == {"upserted": 0, "removed": 0}

        bad_provider = client.post(
            "/accounts/sync",
            json={
                "accounts": [
                    {"provider": "example-mail", "email": "a@example.test", "scopes": [],
                     "expires_at": EXPIRY, "status": STATUS_CONNECTED}
                ],
                "removed": [],
            },
            headers=_headers(),
        )
        assert bad_provider.status_code == 400


def test_sync_removal_and_revocation_evict_bridge_tokens(tmp_path):
    service = _service(tmp_path)
    service.mail_tokens.put("gmail", GMAIL_ACCOUNT, "token-a", EXPIRY)
    service.mail_tokens.put("outlook", GMAIL_ACCOUNT, "token-b", EXPIRY)
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/accounts/sync",
            json={
                "accounts": [
                    {"provider": "outlook", "email": GMAIL_ACCOUNT, "scopes": [],
                     "expires_at": EXPIRY, "status": STATUS_REVOKED}
                ],
                "removed": [{"provider": "gmail", "email": GMAIL_ACCOUNT}],
            },
            headers=_headers(),
        )
    assert response.status_code == 200
    assert service.mail_tokens.get("gmail", GMAIL_ACCOUNT) is None
    assert service.mail_tokens.get("outlook", GMAIL_ACCOUNT) is None


def test_token_push_and_evict_round_trip_without_echo(tmp_path):
    service = _service(tmp_path)
    with TestClient(create_app(service)) as client:
        pushed = client.post(
            "/accounts/tokens",
            json={"provider": "gmail", "email": GMAIL_ACCOUNT,
                  "access_token": "bridge-secret", "expires_at": EXPIRY},
            headers=_headers(),
        )
        assert pushed.status_code == 200
        assert "bridge-secret" not in pushed.text
        assert service.mail_tokens.get("gmail", GMAIL_ACCOUNT) == "bridge-secret"

        accounts = client.get("/accounts", headers=_headers())
        assert "bridge-secret" not in accounts.text
        assert "token" not in accounts.text.lower()

        evicted = client.delete(
            "/accounts/tokens",
            params={"provider": "gmail", "email": GMAIL_ACCOUNT},
            headers=_headers(),
        )
        assert evicted.json() == {"evicted": True}
        assert service.mail_tokens.get("gmail", GMAIL_ACCOUNT) is None


def test_bridge_routes_require_app_token(tmp_path):
    with TestClient(create_app(_service(tmp_path))) as client:
        assert client.post("/accounts/sync", json={}).status_code == 401
        assert client.post("/accounts/tokens", json={}).status_code == 401
        assert client.delete("/accounts/tokens").status_code == 401


# -- per-request header path -------------------------------------------------


def _gmail_list_handler(seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"messages": [{"id": "msg-1"}]})

    return handler


def test_direct_read_uses_per_request_token_without_storing(tmp_path):
    seen: dict = {}
    service = _service(tmp_path)
    service._services().connect("gmail", GMAIL_ACCOUNT)
    service._mail_http = httpx.AsyncClient(transport=httpx.MockTransport(_gmail_list_handler(seen)))
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/mail/messages",
            params={"email": GMAIL_ACCOUNT},
            headers=_headers(_bridge_headers("request-token", "gmail", GMAIL_ACCOUNT)),
        )
    assert response.status_code == 200
    assert response.json()["messages"][0]["id"] == "msg-1"
    assert seen["auth"] == "Bearer request-token"
    assert service.mail_tokens.describe() == [], "per-request tokens must not be stored"
    assert request_token_for("gmail", GMAIL_ACCOUNT) is None


def test_direct_read_with_mismatched_binding_fails_closed(tmp_path):
    service = _service(tmp_path)
    service._services().connect("gmail", GMAIL_ACCOUNT)
    service._mail_http = httpx.AsyncClient(
        transport=httpx.MockTransport(_gmail_list_handler({}))
    )
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/mail/messages",
            params={"email": GMAIL_ACCOUNT},
            headers=_headers(_bridge_headers("request-token", "gmail", "other@example.test")),
        )
    assert response.status_code == 404
    assert "connect the account first" in response.json()["detail"].lower()


def test_direct_read_prefers_request_token_over_vault(tmp_path):
    seen: dict = {}
    service = _service(tmp_path)
    service._services().connect("gmail", GMAIL_ACCOUNT)
    service.mail_tokens.put("gmail", GMAIL_ACCOUNT, "vault-token", EXPIRY)
    service._mail_http = httpx.AsyncClient(transport=httpx.MockTransport(_gmail_list_handler(seen)))
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/mail/messages",
            params={"email": GMAIL_ACCOUNT},
            headers=_headers(_bridge_headers("request-token", "gmail", GMAIL_ACCOUNT)),
        )
    assert response.status_code == 200
    assert seen["auth"] == "Bearer request-token"


# -- scheduler live send -----------------------------------------------------


def _make_run(service, permissions):
    run = service.store.create_run(
        {"goal_id": "goal-bridge", "goal_title": "Fictional bridge test",
         "goal_description": "A made-up goal for bridge tests.",
         "task": "Exercise live send against mocks.", "source_url": "",
         "step_budget": 4, "token_budget": 4000},
        "meta", "fake-model", TOOL_NAMES, permissions)
    service._pause_gates.setdefault(run["id"], asyncio.Event()).set()
    return service.store.get_run(run["id"])


def _harness(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    store = RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3")
    adapter = FakeServiceAdapter()
    service = RuntimeService(
        store=store, service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()}, service_adapter=adapter)
    return service, registry, adapter


def test_live_gmail_send_after_grant_and_approval(tmp_path):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"id": "sent-1"})

    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("gmail", GMAIL_ACCOUNT)
        service.mail_tokens.put("gmail", GMAIL_ACCOUNT, "vault-token", EXPIRY)
        service._mail_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"provider": "gmail", "email": GMAIL_ACCOUNT, "to": "friend@example.test",
                "subject": "Fictional hello", "body": "Made-up body."}
        task = asyncio.create_task(service._execute_tool(run, None, "gmail.send", args, "call-live-send"))
        for _ in range(100):
            if (run["id"], "call-live-send") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        assert (run["id"], "call-live-send") in service._pending_approvals
        await service.control(run["id"], "approve", "call-live-send")
        result = await task
        assert result["ok"] is True
        assert result["message_id"] == "sent-1"
        assert result["fake"] is False and result["live"] is True
        assert result["label"] == LIVE_SEND_LABEL
        assert seen["path"] == "/gmail/v1/users/me/messages/send"
        assert seen["auth"] == "Bearer vault-token"
        assert adapter.sent == [], "live send must not touch the fake outbox"
        await service.aclose()

    asyncio.run(_body())


def test_live_outlook_send_after_grant_and_approval(tmp_path):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(202)

    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("outlook", GMAIL_ACCOUNT)
        service.mail_tokens.put("outlook", GMAIL_ACCOUNT, "vault-token", EXPIRY)
        service._mail_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"provider": "outlook", "email": GMAIL_ACCOUNT, "to": "friend@example.test",
                "subject": "Fictional hello", "body": "Made-up body."}
        task = asyncio.create_task(service._execute_tool(run, None, "gmail.send", args, "call-outlook-send"))
        for _ in range(100):
            if (run["id"], "call-outlook-send") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        await service.control(run["id"], "approve", "call-outlook-send")
        result = await task
        assert result["ok"] is True
        assert seen["path"] == "/v1.0/me/sendMail"
        assert result["live"] is True and result["label"] == LIVE_SEND_LABEL
        assert adapter.sent == []
        await service.aclose()

    asyncio.run(_body())


def test_live_send_without_send_scope_prompts_reconnect(tmp_path):
    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("gmail", GMAIL_ACCOUNT, scopes=["openid", "email"])
        service.mail_tokens.put("gmail", GMAIL_ACCOUNT, "vault-token", EXPIRY)
        run = _make_run(service, ["browser.read"])
        result = await service.execute_service_send(run, {
            "provider": "gmail", "email": GMAIL_ACCOUNT,
            "to": "friend@example.test", "subject": "Fictional", "body": "Made-up."})
        assert result["ok"] is False
        assert "Reconnect" in result["error"]
        assert "Nothing was sent" in result["error"]
        assert adapter.sent == []

    asyncio.run(_body())


def test_denied_live_send_transmits_nothing(tmp_path):
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "sent-1"})

    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("gmail", GMAIL_ACCOUNT)
        service.mail_tokens.put("gmail", GMAIL_ACCOUNT, "vault-token", EXPIRY)
        service._mail_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"provider": "gmail", "email": GMAIL_ACCOUNT, "to": "friend@example.test",
                "subject": "Fictional", "body": "Made-up."}
        task = asyncio.create_task(service._execute_tool(run, None, "gmail.send", args, "call-deny-live"))
        for _ in range(100):
            if (run["id"], "call-deny-live") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        await service.control(run["id"], "deny", "call-deny-live")
        result = await task
        assert result["ok"] is False
        assert calls == [], "denied approval must never reach the provider API"
        await service.aclose()

    asyncio.run(_body())


# -- descriptor drift guard ---------------------------------------------------


def test_provider_descriptors_match_connectors():
    gmail = PROVIDER_DESCRIPTORS["gmail"]
    assert GMAIL_SEND_SCOPE in gmail["scopes"]
    assert "mail.send" in gmail["capabilities"]
    outlook = PROVIDER_DESCRIPTORS["outlook"]
    assert OUTLOOK_SEND_SCOPE in outlook["scopes"]
    assert "mail.send" in outlook["capabilities"]
    fake = PROVIDER_DESCRIPTORS["fake-mail"]
    assert "mail.send" not in fake["capabilities"], "fake mail sends via its labeled outbox, not mail.send"
