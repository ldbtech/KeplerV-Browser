"""Multi-round negotiation flows against synthetic merchants (Phase 6 M4).

:class:`NegotiationDriver` runs real multi-round quote/counter/order flows
through the Trust Gateway: every round is an actual gateway round-trip to
a (fixture) merchant, recorded in a full transcript with claim verdicts.

Injection resistance is structural, not advisory:

- Inbound merchant content is *data*: threat-shaped fields (injected
  instructions, credential requests, redirect/follow URLs) are flagged,
  redacted from the policy's view, and never acted on. The driver never
  fetches a URL from inbound content and never sends credentials.
- The local agent's goal and AIP grants are snapshotted before round one
  and re-checked from the store after every round; any drift aborts the
  negotiation. External agents cannot alter goals or AIP policy.

:class:`FakeModelPolicy` is the deterministic fake-model local agent used
by tests: fixed quote-then-order logic labeled ``fake``. It never emits
secret-bearing fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from runtime.verification import claims as claim_service

FAKE_POLICY_LABEL = "FAKE — deterministic fixture policy, not a live model"

#: Inbound keys treated as threat-shaped: flagged and redacted from the
#: policy's view. Matching is case-insensitive and substring-based so
#: renamed variants ("system_instruction", "redirectUrl") still match.
THREAT_KEY_MARKERS = (
    "instruction", "system", "override", "jailbreak", "prompt",
    "credential", "token", "password", "secret", "apikey", "api_key",
    "follow_url", "followurl", "redirect", "location", "checkout_url",
    "escalat", "approve", "authorize",
)

THREAT_VALUE_MARKERS = (
    "ignore your", "system instruction", "send your",
    "approve immediately", "confirm this order",
)


def _is_threat_key(key: str) -> bool:
    lowered = str(key).casefold()
    return any(marker in lowered for marker in THREAT_KEY_MARKERS)


def _threat_value_hits(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    lowered = value.casefold()
    return [marker for marker in THREAT_VALUE_MARKERS if marker in lowered]


def scan_inbound(payload: Any, trail: str = "") -> list[dict[str, str]]:
    """Flag threat-shaped fields in inbound merchant content (untrusted data)."""
    findings: list[dict[str, str]] = []
    if isinstance(payload, dict):
        for key, item in payload.items():
            path = f"{trail}{key}"
            if _is_threat_key(key):
                findings.append({"path": path, "kind": "threat-shaped-key"})
            for marker in _threat_value_hits(item):
                findings.append({"path": path, "kind": f"injection-marker:{marker}"})
            findings.extend(scan_inbound(item, f"{path}."))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            for marker in _threat_value_hits(item):
                findings.append({"path": f"{trail}{index}", "kind": f"injection-marker:{marker}"})
            findings.extend(scan_inbound(item, f"{trail}{index}."))
    return findings


def sanitize_for_policy(payload: Any) -> Any:
    """Redact threat-shaped fields before the policy sees them.

    The full original stays in the transcript (marked untrusted) for audit;
    the policy only ever sees the sanitized view, so injected instructions
    cannot become its input, let alone system instructions.
    """
    if isinstance(payload, dict):
        clean: dict[str, Any] = {}
        for key, item in payload.items():
            if _is_threat_key(key) or _threat_value_hits(item):
                clean[key] = "[redacted-untrusted]"
            else:
                clean[key] = sanitize_for_policy(item)
        return clean
    if isinstance(payload, list):
        return [
            "[redacted-untrusted]" if _threat_value_hits(item) else sanitize_for_policy(item)
            for item in payload
        ]
    return payload


@dataclass(frozen=True)
class NegotiationGoal:
    """Frozen local goal: immutable for the whole negotiation."""

    product_id: str
    max_price_credits: int
    quantity: int = 1
    max_rounds: int = 4


class LocalAgentPolicy(Protocol):
    """Decides the next merchant action from the sanitized history."""

    label: str

    def next_action(
        self,
        *,
        goal: NegotiationGoal,
        round_index: int,
        history: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the next action payload, or None to stop negotiating."""
        ...


class FakeModelPolicy:
    """Deterministic fake-model local agent for tests.

    Round 0 quotes; later rounds order when the verified-or-asserted price
    fits the cap, re-quote while rounds remain, else stop. Never emits
    secret-bearing fields; every decision is labeled ``fake``.
    """

    label = FAKE_POLICY_LABEL

    def next_action(
        self,
        *,
        goal: NegotiationGoal,
        round_index: int,
        history: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        base = {"product_id": goal.product_id, "policy_label": "fake"}
        if round_index == 0 or not history:
            return {"action": "quote", **base}
        last = history[-1]
        response = last.get("sanitized_response") or {}
        status = response.get("status")
        if status in {"accepted", "declined"}:
            return None
        price = response.get("price_credits")
        if isinstance(price, int) and not isinstance(price, bool):
            if price <= goal.max_price_credits:
                return {
                    "action": "order", **base,
                    "quantity": goal.quantity,
                    "max_price_credits": goal.max_price_credits,
                }
            if round_index < goal.max_rounds - 1:
                return {"action": "quote", **base, "note": "fake haggle: re-quote once"}
            return None
        if status == "unknown-product":
            return None
        # Counter-offers, escalation demands, unsupported actions: never
        # follow the merchant's lead — stop rather than improvise.
        return None


@dataclass
class NegotiationResult:
    status: str
    merchant_id: str
    session_id: str
    label: str
    rounds: list[dict[str, Any]] = field(default_factory=list)
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    threats_flagged: list[dict[str, str]] = field(default_factory=list)
    goal_unchanged: bool = False
    credential_requests_refused: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "merchant_id": self.merchant_id,
            "session_id": self.session_id,
            "label": self.label,
            "rounds": self.rounds,
            "verdicts": self.verdicts,
            "threats_flagged": self.threats_flagged,
            "goal_unchanged": self.goal_unchanged,
            "credential_requests_refused": self.credential_requests_refused,
            "note": self.note,
            "content_is_untrusted": True,
        }


class GoalDriftError(Exception):
    """Raised when the run's goal or grants changed mid-negotiation."""


class NegotiationDriver:
    """Runs bounded multi-round negotiations through gateway tools."""

    def __init__(self, tools, store) -> None:
        self._tools = tools
        self._store = store

    @staticmethod
    def _goal_snapshot(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "goal_id": run.get("goal_id"),
            "goal_title": run.get("goal_title"),
            "goal_description": run.get("goal_description"),
            "task": run.get("task"),
            "permissions": sorted(run.get("permissions") or []),
        }

    def _assert_goal_unchanged(self, run_id: str, snapshot: dict[str, Any]) -> None:
        live = self._store.get_run(run_id) or {}
        if self._goal_snapshot(live) != snapshot:
            raise GoalDriftError(
                "Run goal or permissions changed during negotiation; aborting. "
                "External agents cannot alter goals or AIP policy."
            )

    async def negotiate(
        self,
        run: dict[str, Any],
        external_agent_id: str,
        goal: NegotiationGoal,
        policy: LocalAgentPolicy,
        *,
        session_label: str = "replay",
    ) -> NegotiationResult:
        """Negotiate with one merchant; return the full transcript."""
        snapshot = self._goal_snapshot(run)
        session = await self._tools.open_session(
            run, external_agent_id, label=session_label, max_messages=goal.max_rounds * 2 + 4,
        )
        session_id = session["session_id"]
        result = NegotiationResult(
            status="incomplete", merchant_id=external_agent_id,
            session_id=session_id, label=session.get("label", session_label),
        )
        try:
            history: list[dict[str, Any]] = []
            for round_index in range(goal.max_rounds):
                self._assert_goal_unchanged(run["id"], snapshot)
                action = policy.next_action(
                    goal=goal, round_index=round_index, history=history,
                )
                if action is None:
                    result.status = "stopped-by-policy"
                    result.note = "Fake-model policy chose to stop; nothing improvised."
                    break
                reply = await self._tools.send_message(run, session_id, dict(action))
                payload = reply.get("payload") or {}
                threats = scan_inbound(payload)
                result.threats_flagged.extend([
                    {"round": str(round_index), **finding} for finding in threats
                ])
                credential_hits = [
                    finding for finding in threats
                    if "credential" in finding["path"].casefold() or "token" in finding["path"].casefold()
                    or "send your" in finding["kind"]
                ]
                if credential_hits:
                    result.credential_requests_refused += 1
                    self._store.add_event(run, "negotiation.credential_request_refused", "blocked", {
                        "summary": "Merchant requested credentials; refused without complying.",
                        "session_id": session_id,
                        "round": round_index,
                        "paths": sorted({hit["path"] for hit in credential_hits}),
                    }, "gateway.send")
                statement_ref = f"session:{session_id}:round:{round_index}:{reply.get('message_id')}"
                round_verdicts = claim_service.verify_merchant_payload(
                    payload=payload, product_id=goal.product_id,
                    asserted_by=external_agent_id, statement_ref=statement_ref,
                )
                result.verdicts.extend(round_verdicts)
                sanitized = sanitize_for_policy(payload)
                history.append({
                    "round": round_index,
                    "action": dict(action),
                    "response": dict(payload),
                    "sanitized_response": sanitized,
                    "verdicts": [item["verdict"] for item in round_verdicts],
                    "threats": threats,
                    "content_is_untrusted": True,
                })
                self._assert_goal_unchanged(run["id"], snapshot)
                status = payload.get("status")
                if status == "accepted":
                    result.status = "accepted"
                    result.note = "Fictional order accepted; no real purchase exists."
                    break
                if status == "declined":
                    result.status = "declined"
                    result.note = "Merchant declined within the stated condition."
                    break
            else:
                result.status = "rounds-exhausted"
                result.note = "Negotiation ended at the round cap."
            result.rounds = history
            result.goal_unchanged = True
        finally:
            await self._tools.close_session(run, session_id)
        self._store.add_event(run, "negotiation.completed", "success", {
            "summary": f"Negotiation with {external_agent_id} ended: {result.status}.",
            "session_id": session_id,
            "status": result.status,
            "rounds": len(result.rounds),
            "verdicts": [item["verdict"] for item in result.verdicts],
            "threats_flagged": len(result.threats_flagged),
            "content_is_untrusted": True,
        }, "gateway.send")
        return result
