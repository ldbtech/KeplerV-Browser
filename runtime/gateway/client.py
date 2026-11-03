"""Trust Gateway client (Phase 6 M2).

The single cross-boundary path to external agents — no agent bypass: every
outbound message is serialized, SSRF-checked, budgeted, correlated, and
recorded here. Inbound payloads are strictly validated and always tagged
``untrusted`` (data, never instructions).

Auth: supported mechanisms are ``none`` and ``bearer-header``. Bearer tokens
are passed explicitly per call by the caller; the gateway never reads the
keychain and never stores credentials.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from pydantic import ValidationError

from runtime.gateway import events as gateway_events
from runtime.gateway.a2a import A2AError, send_message as a2a_send_message
from runtime.gateway.registry import ExternalAgentRegistry
from runtime.gateway.schemas import (
    GatewayMessage,
    parse_message,
    serialize_message,
    tag_untrusted,
)
from runtime.gateway.sessions import (
    SESSION_CANCELLED,
    SESSION_CLOSED,
    GatewaySessionStore,
)
from runtime.gateway.ssrf import SSRFBlocked, validate_endpoint, validate_redirect

DEFAULT_TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
DEFAULT_RATE_PER_MINUTE = 30
DEFAULT_MIN_INTERVAL_S = 0.0


class GatewayError(Exception):
    pass


class GatewaySSRFBlocked(GatewayError):
    pass


class GatewayValidationError(GatewayError):
    pass


class GatewayAuthError(GatewayError):
    pass


class GatewayTimeout(GatewayError):
    pass


class GatewayCancelled(GatewayError):
    pass


class GatewayRateLimited(GatewayError):
    pass


class BudgetExceeded(GatewayError):
    pass


def estimate_tokens(payload: dict[str, Any]) -> int:
    return max(1, len(json.dumps(payload or {}, ensure_ascii=False)) // 4)


class TrustGateway:
    """Exclusive cross-boundary messenger for external-agent sessions."""

    def __init__(
        self,
        store,
        sessions: GatewaySessionStore,
        registry: ExternalAgentRegistry,
        *,
        guard_observer=None,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_per_minute: int = DEFAULT_RATE_PER_MINUTE,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._registry = registry
        self._guard_observer = guard_observer
        self._transport = transport
        self._rate_per_minute = rate_per_minute
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._send_timestamps: dict[str, list[float]] = {}
        self._cancelled: set[str] = set()

    # -- helpers -----------------------------------------------------------

    def _event(self, run: dict[str, Any], kind: str, status: str, payload: dict[str, Any]) -> dict[str, Any]:
        return gateway_events.record_gateway_event(
            self._store, run, kind, status, payload, guard_observer=self._guard_observer
        )

    def _run_for(self, session: dict[str, Any]) -> dict[str, Any] | None:
        return self._store.get_run(session["run_id"])

    def _check_rate_limit(self, session_id: str) -> None:
        now = self._clock()
        stamps = [stamp for stamp in self._send_timestamps.get(session_id, []) if now - stamp < 60.0]
        self._send_timestamps[session_id] = stamps
        if len(stamps) >= self._rate_per_minute:
            raise GatewayRateLimited(f"Session exceeded {self._rate_per_minute} messages/minute.")
        if self._min_interval_s > 0 and stamps and now - stamps[-1] < self._min_interval_s:
            raise GatewayRateLimited("Session is sending faster than the minimum interval.")

    def _stamp_send(self, session_id: str) -> None:
        self._send_timestamps.setdefault(session_id, []).append(self._clock())

    # -- lifecycle -----------------------------------------------------------

    async def open_session(
        self,
        run: dict[str, Any],
        external_agent_id: str,
        *,
        label: str = "live",
        max_messages: int = 50,
        max_tokens: int = 20000,
        max_cost_micros: int = 1_000_000,
        fixture_allowlist: set[str] | None = None,
    ) -> dict[str, Any]:
        record = self._registry.get(external_agent_id)
        if record is None:
            raise GatewayValidationError(f"Unknown external agent '{external_agent_id}'.")
        try:
            validate_endpoint(record["endpoint"], allowlist=fixture_allowlist or set())
        except SSRFBlocked as exc:
            self._event(run, gateway_events.SSRF_BLOCKED, "blocked", {
                "external_agent_id": external_agent_id, "endpoint": record["endpoint"],
                "summary": f"SSRF guard blocked endpoint: {exc}",
            })
            raise GatewaySSRFBlocked(str(exc)) from exc
        session = self._sessions.open_session(
            run_id=run["id"], agent_id=run["agent_id"],
            external_agent_id=external_agent_id, endpoint=record["endpoint"],
            provenance={"agent_provenance": record["provenance"], "identity_status": record["identity_status"]},
            label=label, max_messages=max_messages, max_tokens=max_tokens,
            max_cost_micros=max_cost_micros,
        )
        self._registry.record_reliability(external_agent_id, "sessions")
        self._event(run, gateway_events.SESSION_OPENED, "success", {
            "session_id": session["id"], "external_agent_id": external_agent_id,
            "identity_status": record["identity_status"], "label": label,
            "summary": f"Gateway session opened to {external_agent_id} ({record['identity_status']}).",
        })
        if record["identity_status"] in {"unknown", "suspected"}:
            self._event(run, "gateway.provenance_unknown_session", "success", {
                "session_id": session["id"], "external_agent_id": external_agent_id,
                "identity_status": record["identity_status"],
                "summary": f"Session opened to {record['identity_status']} agent {external_agent_id}.",
            })
        return session

    async def close_session(self, session_id: str, status: str = SESSION_CLOSED) -> dict[str, Any] | None:
        session = self._sessions.close_session(session_id, status)
        if session:
            if run := self._run_for(session):
                self._event(run, gateway_events.SESSION_CLOSED, "success", {
                    "session_id": session_id, "status": status,
                    "messages_sent": session["messages_sent"],
                    "messages_received": session["messages_received"],
                    "tokens_used": session["tokens_used"],
                    "summary": f"Gateway session {status}.",
                })
        return session

    def cancel(self, session_id: str) -> None:
        """Flag a session cancelled; in-flight and future sends abort."""
        self._cancelled.add(session_id)

    # -- messaging -----------------------------------------------------------

    async def send(
        self,
        session_id: str,
        *,
        sender: str,
        payload: dict[str, Any],
        kind: str = "request",
        auth_token: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cost_micros: int = 0,
        fixture_allowlist: set[str] | None = None,
    ) -> GatewayMessage:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise GatewayValidationError(f"Unknown gateway session '{session_id}'.")
        if session["status"] != "open" or session_id in self._cancelled:
            raise GatewayCancelled(f"Gateway session '{session_id}' is not open.")
        run = self._run_for(session)
        record = self._registry.get(session["external_agent_id"])

        tokens = estimate_tokens(payload)
        total_messages = session["messages_sent"] + session["messages_received"] + 1
        if total_messages > session["max_messages"]:
            raise self._budget(run, session, "messages", total_messages, session["max_messages"])
        if session["tokens_used"] + tokens > session["max_tokens"]:
            raise self._budget(run, session, "tokens", session["tokens_used"] + tokens, session["max_tokens"])
        if session["cost_micros"] + cost_micros > session["max_cost_micros"]:
            raise self._budget(run, session, "cost_micros", session["cost_micros"] + cost_micros, session["max_cost_micros"])
        self._check_rate_limit(session_id)
        if session_id in self._cancelled:
            raise GatewayCancelled(f"Gateway session '{session_id}' was cancelled.")

        try:
            outbound = GatewayMessage(
                session_id=session_id, message_id=f"msg-{uuid4()}",
                sender=sender, recipient=session["external_agent_id"],
                kind=kind, label=session["label"], payload=payload or {},
            )
        except ValidationError as exc:
            raise GatewayValidationError(f"Outbound message failed validation: {exc}") from exc

        if record and record.get("protocol") == "a2a":
            text = payload.get("text") or payload.get("message")
            if not isinstance(text, str):
                text = json.dumps(payload, ensure_ascii=False)
            try:
                reply_text, raw_result = await a2a_send_message(
                    session["endpoint"], text,
                    context_id=str(uuid5(NAMESPACE_URL, f"keplerv:a2a:{session_id}:{session['external_agent_id']}")),
                    protocol_version=(record or {}).get("protocol_version") or "1.0",
                    tenant=(record or {}).get("tenant") or "", timeout_s=timeout_s,
                    transport=self._transport)
            except A2AError as exc:
                if run:
                    if any(mark in str(exc).casefold() for mark in ("ssrf", "network safety", "endpoint blocked")):
                        event_kind, event_status = gateway_events.SSRF_BLOCKED, "blocked"
                        summary = "The Trust Gateway blocked an A2A endpoint at the network safety boundary."
                    elif "timed out" in str(exc).casefold():
                        event_kind, event_status = gateway_events.TIMEOUT, "timeout"
                        summary = "The external A2A agent did not respond within the gateway timeout."
                    else:
                        event_kind, event_status = gateway_events.VALIDATION_FAILED, "failed"
                        summary = "The external A2A response or request failed protocol validation."
                    self._event(run, event_kind, event_status, {
                        "session_id": session_id,
                        "external_agent_id": session["external_agent_id"],
                        "summary": summary,
                    })
                raise GatewayValidationError(str(exc)) from exc
            inbound = tag_untrusted(GatewayMessage(
                session_id=session_id, message_id=f"a2a-{uuid4()}",
                reply_to=outbound.message_id, sender=session["external_agent_id"],
                recipient=sender, kind="response", label=session["label"],
                payload={"text": reply_text, "a2a_result": raw_result},
            ))
            response_tokens = estimate_tokens(inbound.payload)
            self._stamp_send(session_id)
            self._sessions.append_message(
                session_id, message_id=outbound.message_id, direction="outbound",
                sender=sender, recipient=session["external_agent_id"], kind=kind,
                label=session["label"], untrusted=False, payload={"text": text}, tokens=tokens,
            )
            self._sessions.append_message(
                session_id, message_id=inbound.message_id, direction="inbound",
                sender=inbound.sender, recipient=sender, kind="response",
                label=session["label"], untrusted=True, payload=inbound.payload,
                reply_to=outbound.message_id, tokens=response_tokens,
            )
            self._sessions.add_usage(session_id, sent=1, received=1, tokens=tokens + response_tokens)
            self._registry.record_reliability(record["agent_id"], "messages_sent")
            self._registry.record_reliability(record["agent_id"], "messages_received")
            if run:
                self._event(run, gateway_events.MESSAGE_SENT, "success", {
                    "session_id": session_id, "message_id": outbound.message_id,
                    "summary": "A2A message sent through the Trust Gateway.",
                })
                self._event(run, gateway_events.MESSAGE_RECEIVED, "success", {
                    "session_id": session_id, "message_id": inbound.message_id,
                    "untrusted": True, "summary": "A2A response received as untrusted data.",
                })
                from runtime.guard.behavior_classification import classify_exchange
                screening = classify_exchange([{
                    "id": inbound.message_id, "message_id": inbound.message_id,
                    "direction": "inbound", "untrusted": True,
                    "payload": inbound.payload,
                }])
                if screening["classification"] in {"injection", "unauthorized-request", "policy-violation"}:
                    self._event(run, "gateway.external_behavior_flagged", "flagged", {
                        "session_id": session_id,
                        "external_agent_id": session["external_agent_id"],
                        "classification": screening["classification"],
                        "evidence_refs": screening["evidence_refs"],
                        "observed_markers": screening["observed_markers"],
                        "classifier_label": screening["classifier_label"],
                        "summary": "Guard flagged observable risk markers in an external A2A reply.",
                    })
            return inbound

        headers = self._auth_headers(record, auth_token)
        try:
            raw_response = await self._post(
                session["endpoint"], serialize_message(outbound), headers,
                timeout_s=timeout_s, fixture_allowlist=fixture_allowlist or set(),
            )
        except GatewayCancelled:
            raise
        except GatewayTimeout:
            if record:
                self._registry.record_reliability(record["agent_id"], "timeouts")
            if run:
                self._event(run, gateway_events.TIMEOUT, "timeout", {
                    "session_id": session_id, "message_id": outbound.message_id,
                    "summary": "Gateway request timed out.",
                })
            raise
        except GatewaySSRFBlocked:
            raise

        try:
            inbound = tag_untrusted(parse_message(raw_response))
        except (ValueError, ValidationError) as exc:
            if record:
                self._registry.record_reliability(record["agent_id"], "validation_failures")
            if run:
                self._event(run, gateway_events.VALIDATION_FAILED, "failed", {
                    "session_id": session_id, "message_id": outbound.message_id,
                    "summary": f"Inbound message failed strict validation: {exc}",
                })
            raise GatewayValidationError(f"Inbound message failed strict validation: {exc}") from exc

        if inbound.session_id != session_id:
            raise GatewayValidationError("Inbound message session_id does not match the gateway session.")
        if inbound.kind == "response" and inbound.reply_to != outbound.message_id:
            raise GatewayValidationError("Inbound response is not correlated to the outbound request (reply_to mismatch).")

        response_tokens = estimate_tokens(inbound.payload)
        self._stamp_send(session_id)
        self._sessions.append_message(
            session_id, message_id=outbound.message_id, direction="outbound",
            sender=outbound.sender, recipient=outbound.recipient, kind=outbound.kind,
            label=outbound.label, untrusted=False, payload=outbound.payload,
            tokens=tokens, cost_micros=cost_micros,
        )
        self._sessions.append_message(
            session_id, message_id=inbound.message_id, direction="inbound",
            sender=inbound.sender, recipient=inbound.recipient, kind=inbound.kind,
            label=inbound.label, untrusted=True, payload=inbound.payload,
            reply_to=inbound.reply_to, tokens=response_tokens,
        )
        session = self._sessions.add_usage(
            session_id, sent=1, received=1,
            tokens=tokens + response_tokens, cost_micros=cost_micros,
        ) or session
        if record:
            self._registry.record_reliability(record["agent_id"], "messages_sent")
            self._registry.record_reliability(record["agent_id"], "messages_received")
            for capability in self._observed_capabilities(inbound.payload):
                self._registry.record_observed_capability(record["agent_id"], capability)
        if run:
            self._event(run, gateway_events.MESSAGE_SENT, "success", {
                "session_id": session_id, "message_id": outbound.message_id,
                "kind": outbound.kind, "tokens": tokens,
                "summary": "Gateway message sent.",
            })
            self._event(run, gateway_events.MESSAGE_RECEIVED, "success", {
                "session_id": session_id, "message_id": inbound.message_id,
                "reply_to": inbound.reply_to, "kind": inbound.kind,
                "untrusted": True, "tokens": response_tokens,
                "summary": "Gateway response received (untrusted data).",
            })
        return inbound

    def _budget(self, run, session, name: str, used: int, maximum: int) -> BudgetExceeded:
        if run:
            self._event(run, gateway_events.LIMIT_EXCEEDED, "limited", {
                "session_id": session["id"], "budget": name, "used": used, "max": maximum,
                "summary": f"Gateway session exceeded its {name} budget.",
            })
        return BudgetExceeded(f"Gateway session exceeded its {name} budget ({used}/{maximum}).")

    @staticmethod
    def _observed_capabilities(payload: dict[str, Any]) -> list[str]:
        declared = payload.get("capabilities")
        if isinstance(declared, list):
            return [str(item)[:128] for item in declared if isinstance(item, str) and item.strip()][:16]
        return []

    @staticmethod
    def _auth_headers(record: dict[str, Any] | None, auth_token: str | None) -> dict[str, str]:
        mechanism = (record or {}).get("auth", {}).get("type", "none")
        header_name = (record or {}).get("auth", {}).get("header", "Authorization")
        if mechanism == "none":
            return {}
        if not auth_token:
            raise GatewayAuthError("This external agent requires a bearer token, which must be supplied explicitly per call.")
        return {header_name: f"Bearer {auth_token}"}

    async def _post(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_s: float,
        fixture_allowlist: set[str],
    ) -> Any:
        """Exclusive network path for cross-boundary traffic."""
        try:
            validate_endpoint(url, allowlist=fixture_allowlist)
        except SSRFBlocked as exc:
            raise GatewaySSRFBlocked(str(exc)) from exc
        client_kwargs: dict[str, Any] = {"follow_redirects": False, "timeout": timeout_s}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                current_url = url
                for _ in range(MAX_REDIRECTS + 1):
                    if asyncio.current_task() and asyncio.current_task().cancelling():
                        raise GatewayCancelled("Gateway request was cancelled.")
                    response = await client.post(current_url, json=body, headers=headers)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        try:
                            current_url = validate_redirect(location, allowlist=fixture_allowlist)
                        except SSRFBlocked as exc:
                            raise GatewaySSRFBlocked(f"Redirect blocked: {exc}") from exc
                        continue
                    if response.status_code == 401:
                        raise GatewayAuthError("External agent rejected the request credentials.")
                    response.raise_for_status()
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise GatewayValidationError(f"Inbound response is not JSON: {exc}") from exc
                raise GatewaySSRFBlocked("Too many redirects; chain blocked.")
        except httpx.TimeoutException as exc:
            raise GatewayTimeout(f"Gateway request timed out after {timeout_s}s.") from exc
        except GatewayError:
            raise
        except httpx.HTTPStatusError as exc:
            raise GatewayError(f"Gateway request failed: HTTP {exc.response.status_code}.") from exc
        except asyncio.CancelledError as exc:
            raise GatewayCancelled("Gateway request was cancelled.") from exc
