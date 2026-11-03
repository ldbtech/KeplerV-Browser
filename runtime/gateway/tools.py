"""Local-agent gateway tools (Phase 6 M4).

The agent-facing surface for cross-boundary work: discover agents,
open/close a session, send a message, request evidence. Every method runs
through the Trust Gateway — there is no direct-contact path — and the
scheduler authorizes each call through AIP before dispatching here.

Data minimization: outbound payloads are scanned for secret-bearing and
session-bearing fields (account tokens, browser sessions, credentials).
Such payloads are *refused* with the offending field names — never
silently stripped, never sent. Inbound content is returned tagged
``content_is_untrusted``; it is data, never instructions.

The tools never read the keychain. The fixture Bearer [REDACTED] loopback
merchant tests is supplied explicitly by the caller (scheduler config),
exactly like any other per-call gateway auth token.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from runtime.gateway.client import TrustGateway
from runtime.gateway.a2a import A2AError, fetch_card
from runtime.gateway.registry import IDENTITY_STATUSES, SECRET_FIELD_NAMES
from runtime.merchants.discovery import DiscoveryError, discover_agents
from runtime.verification import claims as claim_service

GATEWAY_TOOL_NAMES = (
    "gateway.discover",
    "gateway.open_session",
    "gateway.close_session",
    "gateway.send",
    "gateway.verify",
)

#: Outbound fields that are never allowed to cross the boundary, on top of
#: the registry secret names: account handles and browser session state.
EXTRA_FORBIDDEN_OUTBOUND_KEYS = frozenset({
    "account", "account_id", "account_ref", "email", "mailbox",
    "browser_session", "browser_session_id", "session_token", "sessiontoken",
    "cookie", "cookies", "set_cookie", "keychain", "model_api_key",
    "refresh_token",
})

FORBIDDEN_OUTBOUND_KEYS = SECRET_FIELD_NAMES | EXTRA_FORBIDDEN_OUTBOUND_KEYS

#: Single key parts that forbid a field even inside a compound name, so
#: renamed variants (``account_token``, ``browser_session_id``) still match.
#: Merchant action payloads (action/product_id/quantity/max_price_credits)
#: contain none of these parts.
FORBIDDEN_KEY_PARTS = frozenset({
    "token", "tokens", "secret", "secrets", "password", "passwd",
    "credential", "credentials", "cookie", "cookies", "account", "accounts",
    "email", "session", "sessions", "keychain",
})

MAX_OUTBOUND_BYTES = 8192
MAX_DISCOVERY_BASES = 8


class GatewayDataMinimizationError(Exception):
    """Raised when an outbound payload carries forbidden fields."""


class GatewayDiscoverArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bases: list[str] = Field(min_length=1, max_length=MAX_DISCOVERY_BASES)


class GatewayOpenSessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_agent_id: str = Field(min_length=1, max_length=128)
    label: str = Field(default="live", max_length=16)
    max_messages: int = Field(default=50, ge=1, le=200)


class GatewayCloseSessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)


class GatewaySendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class GatewayVerifyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    product_id: str = Field(min_length=1, max_length=128)
    asserted_price_credits: int | None = Field(default=None, ge=0, le=10_000_000)
    claim_text: str | None = Field(default=None, max_length=2000)
    request_merchant_evidence: bool = False


def _key_is_forbidden(key: str) -> bool:
    lowered = str(key).casefold()
    if lowered in FORBIDDEN_OUTBOUND_KEYS:
        return True
    parts = [part for part in "".join(
        char if char.isalnum() else " " for char in lowered).split() if part]
    return any(part in FORBIDDEN_KEY_PARTS for part in parts)


def find_forbidden_keys(value: Any, trail: str = "") -> list[str]:
    """Collect dotted paths of forbidden outbound field names (names only)."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{trail}{key}"
            if _key_is_forbidden(key):
                found.append(path)
            found.extend(find_forbidden_keys(item, f"{path}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_forbidden_keys(item, f"{trail}{index}."))
    return found


def scrub_outbound_payload(payload: Any) -> dict[str, Any]:
    """Validate an outbound payload; refuse forbidden fields loudly.

    Returns a plain-dict copy. Raises :class:`GatewayDataMinimizationError`
    naming the offending fields (never their values).
    """
    if not isinstance(payload, dict):
        raise GatewayDataMinimizationError("Outbound payload must be a JSON object.")
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise GatewayDataMinimizationError(f"Outbound payload is not JSON-serializable: {exc}") from exc
    if len(raw.encode("utf-8")) > MAX_OUTBOUND_BYTES:
        raise GatewayDataMinimizationError(
            f"Outbound payload exceeds the {MAX_OUTBOUND_BYTES}-byte minimization cap."
        )
    forbidden = find_forbidden_keys(payload)
    if forbidden:
        raise GatewayDataMinimizationError(
            "Outbound payload carries forbidden fields that must never cross the "
            f"agent boundary (account tokens / sessions / credentials): {sorted(set(forbidden))}."
        )
    return json.loads(raw)


class GatewayTools:
    """Agent-facing gateway operations bound to one runtime."""

    def __init__(
        self,
        store,
        sessions,
        registry,
        *,
        guard_observer=None,
        transport: httpx.AsyncBaseTransport | None = None,
        fixture_allowlist: set[str] | None = None,
        fixture_token: str | None = None,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._registry = registry
        self._guard_observer = guard_observer
        self._transport = transport
        self._fixture_allowlist = set(fixture_allowlist or set())
        self._fixture_token = fixture_token
        self._gateway = TrustGateway(
            store, sessions, registry,
            guard_observer=guard_observer, transport=transport,
        )

    @property
    def gateway(self) -> TrustGateway:
        return self._gateway

    # -- discover ------------------------------------------------------

    async def discover_agents(self, run: dict[str, Any], bases: list[str]) -> dict[str, Any]:
        """Fetch capability descriptors from explicit endpoints; register them."""
        if not bases or len(bases) > MAX_DISCOVERY_BASES:
            raise DiscoveryError("Discovery needs 1-8 explicitly configured endpoints.")
        descriptors: list[dict[str, Any]] = []
        for base in bases:
            try:
                descriptors.append(await fetch_card(base, transport=self._transport))
            except A2AError as a2a_error:
                # Fixture descriptors remain for the project's isolated test
                # harness. Public agents must publish a real A2A Agent Card.
                if not self._fixture_allowlist:
                    raise a2a_error
                try:
                    descriptors.extend(await discover_agents(
                        [base], allowlist=set(self._fixture_allowlist),
                        auth_token=self._fixture_token, transport=self._transport,
                    ))
                except Exception:
                    raise a2a_error
        registered: list[dict[str, Any]] = []
        for descriptor in descriptors:
            identity_claim = descriptor.get("identity_claim")
            identity_status = identity_claim if identity_claim in IDENTITY_STATUSES else "unknown"
            is_a2a = descriptor.get("protocol") == "A2A"
            record = self._registry.register(
                str(descriptor["agent_id"]),
                endpoint=str(descriptor["endpoint"]),
                identity_status=identity_status,
                capabilities_declared=[str(item) for item in descriptor.get("capabilities_declared", [])]
                or [str(item.get("name") or item.get("id") or "") for item in descriptor.get("skills", []) if isinstance(item, dict)],
                provenance={
                    "asserted_by": f"local-agent:{run['agent_id']}",
                    "evidence_refs": [f"discovery:{descriptor.get('capabilities_url', descriptor['endpoint'])}"],
                },
                auth={"type": "bearer-header" if self._fixture_token and not is_a2a else "none"},
                protocol="a2a" if is_a2a else "keplerv-gateway",
                protocol_version=str(descriptor.get("protocol_version") or ""),
                display_name=str(descriptor.get("name") or ""),
                tenant=str(descriptor.get("tenant") or ""),
            )
            registered.append({
                "agent_id": record["agent_id"],
                "endpoint": record["endpoint"],
                "identity_status": record["identity_status"],
                "capabilities_declared": record["capabilities_declared"],
                "fixture": descriptor.get("fixture"),
                "surface_label": descriptor.get("surface_label"),
                "protocol": record["protocol"],
                "name": descriptor.get("name"),
                "description": descriptor.get("description"),
                "skills": descriptor.get("skills", []),
            })
        self._store.add_event(run, "gateway.discover.completed", "success", {
            "summary": f"Discovered {len(registered)} external agent(s) from explicit endpoints.",
            "agents": [item["agent_id"] for item in registered],
            "content_is_untrusted": True,
        }, "gateway.discover")
        return {
            "agents": registered,
            "descriptors": [{**d, "content_is_untrusted": True} for d in descriptors],
            "content_is_untrusted": True,
        }

    # -- sessions ------------------------------------------------------

    async def open_session(
        self, run: dict[str, Any], external_agent_id: str,
        *, label: str = "live", max_messages: int = 50,
    ) -> dict[str, Any]:
        if label not in {"live", "replay"}:
            raise ValueError("Session label must be 'live' or 'replay'.")
        session = await self._gateway.open_session(
            run, external_agent_id, label=label, max_messages=max_messages,
            fixture_allowlist=set(self._fixture_allowlist),
        )
        return {
            "session_id": session["id"],
            "external_agent_id": session["external_agent_id"],
            "label": session["label"],
            "status": session["status"],
        }

    async def close_session(self, run: dict[str, Any], session_id: str) -> dict[str, Any]:
        session = await self._gateway.close_session(session_id)
        if session is None:
            return {"ok": False, "session_id": session_id, "error": "Unknown gateway session."}
        return {"ok": True, "session_id": session_id, "status": session["status"]}

    # -- messaging -----------------------------------------------------

    async def send_message(
        self, run: dict[str, Any], session_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Scrub, send, and return the merchant reply as untrusted data."""
        clean = scrub_outbound_payload(payload)
        inbound = await self._gateway.send(
            session_id, sender=run["agent_id"], payload=clean,
            auth_token=self._fixture_token,
            fixture_allowlist=set(self._fixture_allowlist),
        )
        return {
            "session_id": inbound.session_id,
            "message_id": inbound.message_id,
            "reply_to": inbound.reply_to,
            "sender": inbound.sender,
            "kind": inbound.kind,
            "label": inbound.label,
            "payload": dict(inbound.payload),
            "content_is_untrusted": True,
        }

    # -- evidence ------------------------------------------------------

    async def request_evidence(
        self,
        run: dict[str, Any],
        session_id: str,
        *,
        product_id: str,
        asserted_price_credits: int | None = None,
        claim_text: str | None = None,
        request_merchant_evidence: bool = False,
    ) -> dict[str, Any]:
        """Verify a merchant statement; optionally ask the merchant for more.

        Local verification always runs against the trusted catalog. When
        ``request_merchant_evidence`` is set, the merchant is asked for
        supporting evidence first; its reply is untrusted data and can only
        add a statement-level record — never proof by itself.
        """
        session = self._sessions.get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown gateway session '{session_id}'.")
        merchant_reply: dict[str, Any] | None = None
        if request_merchant_evidence:
            merchant_reply = await self.send_message(run, session_id, {
                "action": "evidence",
                "product_id": product_id,
                "subject": claim_text or "price",
            })
        statement_ref = f"session:{session_id}:verify:{product_id}"
        verdicts: list[dict[str, Any]] = []
        if asserted_price_credits is not None:
            verdicts.append(claim_service.verify_price_claim(
                product_id=product_id, asserted_price_credits=asserted_price_credits,
                asserted_by=session["external_agent_id"], statement_ref=statement_ref,
            ))
        if claim_text:
            verdicts.append(claim_service.verify_text_claim(
                text=claim_text, product_id=product_id,
                asserted_by=session["external_agent_id"], statement_ref=statement_ref,
            ))
        if merchant_reply is not None:
            verdicts.extend(claim_service.verify_merchant_payload(
                payload=merchant_reply["payload"], product_id=product_id,
                asserted_by=session["external_agent_id"],
                statement_ref=f"session:{session_id}:merchant-evidence:{merchant_reply['message_id']}",
            ))
        self._store.add_event(run, "gateway.verify.completed", "success", {
            "summary": f"Verified {len(verdicts)} claim(s) against the trusted catalog.",
            "session_id": session_id,
            "verdicts": [item["verdict"] for item in verdicts],
            "content_is_untrusted": True,
        }, "gateway.verify")
        return {
            "session_id": session_id,
            "label": session["label"],
            "verdicts": verdicts,
            "merchant_evidence_reply": merchant_reply,
            "content_is_untrusted": True,
        }
