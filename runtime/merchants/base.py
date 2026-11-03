"""Fixture HTTP harness for synthetic merchants (Phase 6 M3).

Each merchant is an independent loopback server speaking the gateway
envelope schemas (:mod:`runtime.gateway.schemas`):

- ``GET /capabilities`` — served capability descriptor (discovery target).
- ``GET /history`` — served fictional exchange history.
- ``POST /agent`` — gateway envelope request in, correlated envelope out.

All binds are ``127.0.0.1`` with an app-provided fixture token (never the
keychain). Behavior is deterministic: response message ids derive from a
per-server call counter, and profiles are pure functions of
``(payload, seed, call_index)``.

Socket-free twins (:func:`mock_transport_for`, :func:`make_routing_transport`)
run the same handlers behind ``httpx.MockTransport`` so classification and
determinism tests pass even where loopback binds are blocked.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Any
from urllib.parse import urlsplit

import httpx

from runtime.gateway.schemas import GatewayMessage, parse_message, serialize_message
from runtime.merchants import catalog_fixture

AGENT_PATH = "/agent"
CAPABILITIES_PATH = "/capabilities"
HISTORY_PATH = "/history"


def build_capability_descriptor(profile: Any, endpoint_base: str) -> dict[str, Any]:
    """Capability descriptor served for discovery (explicit endpoints only)."""
    base = endpoint_base.rstrip("/")
    return {
        "agent_id": profile.AGENT_ID,
        "display_name": profile.DISPLAY_NAME,
        "endpoint": f"{base}{AGENT_PATH}",
        "capabilities_url": f"{base}{CAPABILITIES_PATH}",
        "history_url": f"{base}{HISTORY_PATH}",
        "identity_claim": profile.IDENTITY_STATUS,
        "capabilities_declared": list(profile.CAPABILITIES),
        "marketplace": catalog_fixture.MARKETPLACE_NAME,
        "currency": catalog_fixture.CURRENCY,
        "fixture": True,
        "surface_label": "fake",
        "fictional_disclaimer": catalog_fixture.FICTIONAL_DISCLAIMER,
        "history": [dict(entry) for entry in profile.HISTORY],
    }


def history_document(profile: Any) -> dict[str, Any]:
    return {
        "agent_id": profile.AGENT_ID,
        "fixture": True,
        "surface_label": "fake",
        "history": [dict(entry) for entry in profile.HISTORY],
    }


def handle_envelope(profile: Any, raw: Any, *, seed: int = 0, call_index: int = 0) -> dict[str, Any]:
    """Pure envelope handler: strict-parse, dispatch, correlate.

    Raises ``ValueError``/``pydantic.ValidationError`` for malformed input.
    """
    request = parse_message(raw)
    if request.kind != "request" or not isinstance(request.payload, dict):
        response = GatewayMessage(
            session_id=request.session_id,
            message_id=f"{profile.AGENT_ID}-resp-{call_index + 1:04d}",
            reply_to=request.message_id,
            sender=profile.AGENT_ID,
            recipient=request.sender,
            kind="error",
            label=request.label,
            payload={
                "fixture": "synthetic-merchant",
                "surface_label": "fake",
                "status": "unsupported-kind",
                "message": "Fixture merchants answer 'request' envelopes only.",
            },
        )
        return serialize_message(response)
    ctx = {"seed": seed, "call_index": call_index, "agent_id": profile.AGENT_ID}
    result = profile.handle_action(dict(request.payload), ctx)
    payload = {
        "fixture": "synthetic-merchant",
        "surface_label": "fake",
        "capabilities": list(profile.CAPABILITIES),
        **result,
    }
    response = GatewayMessage(
        session_id=request.session_id,
        message_id=f"{profile.AGENT_ID}-resp-{call_index + 1:04d}",
        reply_to=request.message_id,
        sender=profile.AGENT_ID,
        recipient=request.sender,
        kind="response",
        label=request.label,
        payload=payload,
    )
    return serialize_message(response)


def _fixture_response(
    profile: Any,
    *,
    method: str,
    path: str,
    body: bytes,
    authorization: str,
    endpoint_base: str,
    seed: int,
    call_index: int,
    expected_token: str | None,
) -> tuple[int, dict[str, Any], bool]:
    """Shared logic for live + mock transports. Returns (status, json, counted)."""
    if expected_token and authorization != f"Bearer {expected_token}":
        return 401, {"error": "Fixture token required."}, False
    if method == "GET" and path == CAPABILITIES_PATH:
        return 200, build_capability_descriptor(profile, endpoint_base), False
    if method == "GET" and path == HISTORY_PATH:
        return 200, history_document(profile), False
    if method == "POST" and path == AGENT_PATH:
        try:
            raw = json.loads(body.decode("utf-8") if body else "null")
        except (ValueError, UnicodeDecodeError):
            return 422, {"error": "Request body is not JSON."}, False
        try:
            return 200, handle_envelope(profile, raw, seed=seed, call_index=call_index), True
        except Exception as exc:  # strict-schema rejection, deterministic
            return 422, {"error": f"Gateway envelope rejected: {exc}"}, False
    return 404, {"error": "Unknown fixture route."}, False


def mock_transport_for(
    profile: Any,
    *,
    seed: int = 0,
    expected_token: str | None = None,
) -> httpx.MockTransport:
    """Socket-free twin of one merchant server (same handler, same auth)."""
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        base = str(request.url).split(request.url.path)[0]
        status, payload, counted = _fixture_response(
            profile,
            method=request.method,
            path=request.url.path,
            body=request.content,
            authorization=request.headers.get("authorization", ""),
            endpoint_base=base,
            seed=seed,
            call_index=state["calls"],
            expected_token=expected_token,
        )
        if counted:
            state["calls"] += 1
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def make_routing_transport(
    entries: dict[str, Any],
    *,
    seed: int = 0,
    expected_tokens: dict[str, str | None] | None = None,
) -> httpx.MockTransport:
    """Socket-free twin of the whole synthetic internet, routed by base URL."""

    counters = {base: 0 for base in entries}
    tokens = expected_tokens or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for base, profile in entries.items():
            if url == base or url.startswith(base.rstrip("/") + "/"):
                status, payload, counted = _fixture_response(
                    profile,
                    method=request.method,
                    path=request.url.path,
                    body=request.content,
                    authorization=request.headers.get("authorization", ""),
                    endpoint_base=base,
                    seed=seed,
                    call_index=counters[base],
                    expected_token=tokens.get(base),
                )
                if counted:
                    counters[base] += 1
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"error": "Unknown fixture merchant."})

    return httpx.MockTransport(handler)


def make_handler(
    profile: Any, *, seed: int, expected_token: str | None, counter: dict[str, int], lock: Lock
) -> type[BaseHTTPRequestHandler]:
    class MerchantHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route(self, method: str, body: bytes = b"") -> None:
            path = urlsplit(self.path).path
            host = self.headers.get("host", "127.0.0.1")
            with lock:
                call_index = counter["calls"]
            status, payload, counted = _fixture_response(
                profile,
                method=method,
                path=path,
                body=body,
                authorization=self.headers.get("authorization", ""),
                endpoint_base=f"http://{host}",
                seed=seed,
                call_index=call_index,
                expected_token=expected_token,
            )
            if counted:
                with lock:
                    counter["calls"] += 1
            self._send_json(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0") or "0")
            self._route("POST", self.rfile.read(length) if length else b"")

        def log_message(self, *args: Any) -> None:
            return

    return MerchantHandler


class MerchantServer:
    """Loopback fixture server for one synthetic merchant.

    Binds ``127.0.0.1`` on an ephemeral port. ``expected_token`` is an
    app-provided fixture token (never the keychain); ``None`` leaves the
    in-process harness open and must only be used inside tests.
    """

    def __init__(self, profile: Any, *, seed: int = 0, expected_token: str | None = None) -> None:
        self.profile = profile
        self.seed = seed
        self.expected_token = expected_token
        self._counter = {"calls": 0}
        self._lock = Lock()
        handler = make_handler(
            profile, seed=seed, expected_token=expected_token,
            counter=self._counter, lock=self._lock,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def agent_endpoint(self) -> str:
        return f"{self.base_url}{AGENT_PATH}"

    @property
    def allowlist_entry(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    @property
    def bound_host(self) -> str:
        return self.server.server_address[0]

    def __enter__(self) -> "MerchantServer":
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
