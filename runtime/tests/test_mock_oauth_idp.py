"""Mock-OAuth-IdP end-to-end test: connect -> token -> read -> send -> revoke.

A loopback IdP speaking real OAuth 2.0 Authorization Code + PKCE (S256 via
stdlib ``hashlib`` — no custom crypto) plus mock Gmail/Graph resource
endpoints. The test drives the full mechanics with the REAL connector and
sender classes pointed at the mock, proving:

1. authorize with an S256 code challenge captures a code on loopback,
2. code exchange verifies the PKCE verifier (wrong verifier rejected),
3. refresh grant rotates the access token,
4. read/send work with the issued token through real API shapes,
5. revoke fails everything closed (read AND send raise, nothing returned).

The provider is labeled ``mock`` everywhere and all data is fictional. This
proves the protocol mechanics headlessly; the Rust browser sign-in uses the
same PKCE + loopback-redirect flow (see ``src-tauri/src/oauth.rs``).
Swapping in real Google/Microsoft providers is documented in
``docs/mail-setup.md``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

from runtime.connectors.base import LIVE_SEND_LABEL, MailNotConnectedError, ResolvedAccount
from runtime.connectors.gmail import GmailConnector, GmailSender
from runtime.connectors.outlook import OutlookConnector, OutlookSender

MOCK_IDP_LABEL = "MOCK IdP — loopback OAuth fixture, not a real provider"
MOCK_EMAIL = "mock.person@example.test"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _gmail_full_fixture() -> dict:
    body = base64.urlsafe_b64encode(b"Hello from the fictional mock.").decode("ascii").rstrip("=")
    return {
        "id": "mock-msg-1",
        "threadId": "mock-thread-1",
        "snippet": "Hello from the fictional mock",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Mock Registrar <registrar@example.test>"},
                {"name": "To", "value": MOCK_EMAIL},
                {"name": "Subject", "value": "Fictional mock welcome"},
                {"name": "Date", "value": "Mon, 01 Sep 2026 09:00:00 +0000"},
            ],
            "body": {"data": body},
        },
    }


def _graph_fixture(message_id: str = "mock-msg-1", conversation: str = "mock-conv-1") -> dict:
    return {
        "id": message_id,
        "conversationId": conversation,
        "from": {"emailAddress": {"address": "registrar@example.test"}},
        "toRecipients": [{"emailAddress": {"address": MOCK_EMAIL}}],
        "subject": "Fictional mock welcome",
        "receivedDateTime": "2026-09-01T09:00:00Z",
        "bodyPreview": "Hello from the fictional mock.",
        "body": {"contentType": "Text", "content": "Hello from the fictional mock."},
    }


class _MockIdPState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.codes: dict[str, dict] = {}  # code -> {challenge, redirect_uri}
        self.access: dict[str, bool] = {}  # access_token -> live
        self.refresh: dict[str, bool] = {}  # refresh_token -> live
        self.gmail_sent: list[dict] = []
        self.graph_sent: list[dict] = []


class _MockIdPHandler(BaseHTTPRequestHandler):
    state: _MockIdPState

    def log_message(self, *args):  # keep test output clean
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bearer_live(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        with self.state.lock:
            return self.state.access.get(header[len("Bearer "):], False)

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return dict(parse_qsl(self.rfile.read(length).decode("utf-8"), keep_blank_values=True))

    # -- OAuth endpoints -------------------------------------------------

    def do_GET(self):  # noqa: N802 (stdlib handler naming)
        parsed = urlparse(self.path)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if parsed.path == "/authorize":
            self._handle_authorize(params)
        elif parsed.path == "/userinfo":
            if not self._bearer_live():
                self._send_json(401, {"error": "invalid_token"})
            else:
                self._send_json(200, {"email": MOCK_EMAIL})
        elif parsed.path == "/gmail/v1/users/me/messages":
            self._handle_gmail_list()
        elif parsed.path.startswith("/gmail/v1/users/me/messages/"):
            self._handle_gmail_get(parsed.path.rsplit("/", 1)[-1])
        elif parsed.path == "/me/messages":
            self._handle_graph_list(params)
        elif parsed.path.startswith("/me/messages/"):
            self._handle_graph_get(parsed.path.rsplit("/", 1)[-1])
        else:
            self._send_json(404, {"error": "unknown_endpoint"})

    def _handle_authorize(self, params: dict) -> None:
        required = ("client_id", "redirect_uri", "code_challenge", "state")
        if params.get("response_type") != "code" or not all(params.get(key) for key in required):
            self._send_json(400, {"error": "invalid_request"})
            return
        if params.get("code_challenge_method", "S256") != "S256":
            self._send_json(400, {"error": "pkce_required"})
            return
        redirect = urlparse(params["redirect_uri"])
        if redirect.scheme != "http" or (redirect.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
            self._send_json(400, {"error": "loopback_redirect_required"})
            return
        code = f"mock-code-{secrets.token_hex(8)}"
        with self.state.lock:
            self.state.codes[code] = {
                "challenge": params["code_challenge"],
                "redirect_uri": params["redirect_uri"],
            }
        location = f"{params['redirect_uri']}?code={code}&state={params['state']}"
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802 (stdlib handler naming)
        parsed = urlparse(self.path)
        if parsed.path == "/token":
            self._handle_token(self._form())
        elif parsed.path == "/revoke":
            self._handle_revoke(self._form())
        elif parsed.path == "/gmail/v1/users/me/messages/send":
            self._handle_gmail_send()
        elif parsed.path == "/me/sendMail":
            self._handle_graph_send()
        else:
            self._send_json(404, {"error": "unknown_endpoint"})

    def _handle_token(self, form: dict) -> None:
        grant = form.get("grant_type")
        if grant == "authorization_code":
            with self.state.lock:
                record = self.state.codes.pop(form.get("code", ""), None)
            if record is None or record["redirect_uri"] != form.get("redirect_uri"):
                self._send_json(400, {"error": "invalid_grant"})
                return
            if _s256(form.get("code_verifier", "")) != record["challenge"]:
                self._send_json(400, {"error": "pkce_mismatch"})
                return
        elif grant == "refresh_token":
            with self.state.lock:
                live = self.state.refresh.get(form.get("refresh_token", ""), False)
            if not live:
                self._send_json(400, {"error": "invalid_grant"})
                return
        else:
            self._send_json(400, {"error": "unsupported_grant_type"})
            return
        access = f"mock-access-{secrets.token_hex(12)}"
        refresh = f"mock-refresh-{secrets.token_hex(12)}"
        with self.state.lock:
            self.state.access[access] = True
            self.state.refresh[refresh] = True
        self._send_json(200, {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "scope": "openid email mock.read mock.send",
            "token_type": "Bearer",
        })

    def _handle_revoke(self, form: dict) -> None:
        token = form.get("token", "")
        with self.state.lock:
            hit = self.state.access.pop(token, None) is not None
            hit = self.state.refresh.pop(token, None) is not None or hit
            if hit:  # revoking one grant token retires the whole mock grant
                self.state.access.clear()
                self.state.refresh.clear()
        self._send_json(200, {"revoked": hit})

    # -- mock resource endpoints ------------------------------------------

    def _handle_gmail_list(self) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": "invalid credentials"})
            return
        self._send_json(200, {"messages": [{"id": "mock-msg-1"}], "resultSizeEstimate": 1})

    def _handle_gmail_get(self, message_id: str) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": "invalid credentials"})
            return
        if message_id != "mock-msg-1":
            self._send_json(200, {})
            return
        self._send_json(200, _gmail_full_fixture())

    def _handle_gmail_send(self) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": "invalid credentials"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        with self.state.lock:
            self.state.gmail_sent.append(payload)
            message_id = f"mock-sent-{len(self.state.gmail_sent)}"
        self._send_json(200, {"id": message_id, "labelIds": ["SENT"]})

    def _handle_graph_list(self, params: dict) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": {"code": "InvalidAuthenticationToken"}})
            return
        messages = [_graph_fixture()]
        search = params.get("$search", "").strip('"').lower()
        if search:
            messages = [item for item in messages if search in item["subject"].lower()]
        filt = params.get("$filter", "")
        if filt.startswith("conversationId eq "):
            needle = filt[len("conversationId eq "):].strip().strip("'")
            messages = [item for item in messages if item["conversationId"] == needle]
        self._send_json(200, {"value": messages})

    def _handle_graph_get(self, message_id: str) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": {"code": "InvalidAuthenticationToken"}})
            return
        if message_id != "mock-msg-1":
            self._send_json(200, {})
            return
        self._send_json(200, _graph_fixture())

    def _handle_graph_send(self) -> None:
        if not self._bearer_live():
            self._send_json(401, {"error": {"code": "InvalidAuthenticationToken"}})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        with self.state.lock:
            self.state.graph_sent.append(payload)
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture()
def mock_idp():
    """Loopback mock IdP + mock resource server. Yields (base_url, state)."""
    state = _MockIdPState()
    handler = type("_BoundHandler", (_MockIdPHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    return verifier, _s256(verifier)


def test_mock_oauth_connect_token_read_send_revoke_e2e(mock_idp):
    base_url, state = mock_idp
    assert MOCK_IDP_LABEL.startswith("MOCK")

    async def _body():
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as http:
            # -- connect: authorize with S256 PKCE on loopback ---------------
            verifier, challenge = _pkce_pair()
            redirect_uri = "http://127.0.0.1:9/oauth/callback"
            authorize = await http.get(
                "/authorize",
                params={
                    "client_id": "mock-client-id",
                    "response_type": "code",
                    "redirect_uri": redirect_uri,
                    "scope": "openid email mock.read mock.send",
                    "state": "mock-state-1",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
            assert authorize.status_code == 302
            callback = urlparse(authorize.headers["location"])
            callback_params = dict(parse_qsl(callback.query))
            assert callback_params["state"] == "mock-state-1"
            code = callback_params["code"]

            # -- token: wrong verifier is rejected, correct one mints --------
            bad = await http.post("/token", data={
                "grant_type": "authorization_code", "client_id": "mock-client-id",
                "code": code, "code_verifier": "wrong-verifier", "redirect_uri": redirect_uri,
            })
            assert bad.status_code == 400  # code is single-use: consumed below instead
            # NOTE: the wrong-verifier attempt consumed the single-use code by
            # design; re-authorize for the real exchange.
            authorize = await http.get(
                "/authorize",
                params={
                    "client_id": "mock-client-id", "response_type": "code",
                    "redirect_uri": redirect_uri, "scope": "openid email",
                    "state": "mock-state-2", "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
            code = dict(parse_qsl(urlparse(authorize.headers["location"]).query))["code"]
            exchanged = await http.post("/token", data={
                "grant_type": "authorization_code", "client_id": "mock-client-id",
                "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
            })
            assert exchanged.status_code == 200
            tokens = exchanged.json()
            access, refresh = tokens["access_token"], tokens["refresh_token"]

            info = await http.get("/userinfo", headers={"Authorization": f"Bearer {access}"})
            assert info.json()["email"] == MOCK_EMAIL

            # -- refresh grant rotates the access token ----------------------
            rotated = await http.post("/token", data={
                "grant_type": "refresh_token", "client_id": "mock-client-id",
                "refresh_token": refresh,
            })
            assert rotated.status_code == 200
            access = rotated.json()["access_token"]
            refresh = rotated.json()["refresh_token"]

            supplier = lambda account, token=access: token  # noqa: E731

            # -- read via the real Gmail connector ---------------------------
            gmail_account = ResolvedAccount(provider="gmail", email=MOCK_EMAIL)
            gmail = GmailConnector(client=http, token_supplier=supplier, api_base=base_url)
            listing = await gmail.list_messages(gmail_account)
            assert [item["id"] for item in listing["messages"]] == ["mock-msg-1"]
            message = await gmail.get_message(gmail_account, "mock-msg-1")
            assert message["subject"] == "Fictional mock welcome"
            assert "fictional mock" in message["body"]

            # -- send via the real Gmail sender ------------------------------
            gmail_sender = GmailSender(client=http, token_supplier=supplier, api_base=base_url)
            receipt = await gmail_sender.send_message(
                gmail_account, "friend@example.test", "Fictional mock hello", "Made-up body.")
            assert receipt["message_id"] == "mock-sent-1"
            assert receipt["label"] == LIVE_SEND_LABEL
            recorded = state.gmail_sent[0]["raw"]
            decoded = base64.urlsafe_b64decode(recorded).decode("utf-8")
            assert "To: friend@example.test" in decoded

            # -- read via the real Outlook connector -------------------------
            outlook_account = ResolvedAccount(provider="outlook", email=MOCK_EMAIL)
            outlook = OutlookConnector(client=http, token_supplier=supplier, api_base=base_url)
            outlook_listing = await outlook.list_messages(outlook_account)
            assert outlook_listing["messages"][0]["subject"] == "Fictional mock welcome"
            found = await outlook.search(outlook_account, "mock welcome")
            assert len(found["messages"]) == 1
            thread = await outlook.get_thread(outlook_account, "mock-conv-1")
            assert thread["thread_id"] == "mock-conv-1"

            # -- send via the real Outlook sender -----------------------------
            outlook_sender = OutlookSender(client=http, token_supplier=supplier, api_base=base_url)
            outlook_receipt = await outlook_sender.send_message(
                outlook_account, "friend@example.test", "Fictional mock hello", "Made-up body.")
            assert outlook_receipt["live"] is True
            assert state.graph_sent[0]["message"]["subject"] == "Fictional mock hello"

            # -- revoke fails read AND send closed -----------------------------
            revoked = await http.post("/revoke", data={"token": refresh})
            assert revoked.json()["revoked"] is True
            for call in (
                lambda: gmail.list_messages(gmail_account),
                lambda: gmail_sender.send_message(gmail_account, "a@example.test", "s", "b"),
                lambda: outlook.list_messages(outlook_account),
                lambda: outlook_sender.send_message(outlook_account, "a@example.test", "s", "b"),
            ):
                with pytest.raises(MailNotConnectedError, match="Reconnect the account|Nothing was sent"):
                    await call()

    asyncio.run(_body())


def test_mock_idp_rejects_non_pkce_and_non_loopback(mock_idp):
    base_url, _ = mock_idp

    async def _body():
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as http:
            plain = await http.get("/authorize", params={
                "client_id": "mock-client-id", "response_type": "code",
                "redirect_uri": "http://127.0.0.1:9/oauth/callback",
                "state": "s", "code_challenge": "x", "code_challenge_method": "plain",
            })
            assert plain.status_code == 400
            remote = await http.get("/authorize", params={
                "client_id": "mock-client-id", "response_type": "code",
                "redirect_uri": "https://example.com/callback",
                "state": "s", "code_challenge": "x", "code_challenge_method": "S256",
            })
            assert remote.status_code == 400

    asyncio.run(_body())
