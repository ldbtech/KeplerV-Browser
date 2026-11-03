"""Federated authorization for external-agent requests (Phase 6 M6+M7).

Pure policy check plus ledger recording for requests crossing the agent
boundary. Rules:

- Sensitive external requests (credentials, payments, delegation grants,
  identity assertions, session transfer) are *always denied* and flagged
  for Guard review — possession of a valid signature never authorizes
  them. A human re-authorizes through a new local decision, never by
  flipping this one.
- Unknown scopes fail closed: deny + Guard review.
- Routine scopes (quotes, offers, status, evidence) require an
  authenticated operator: ``operator`` proof or better allows;
  ``unverified-signature`` requires explicit human approval.
- Delegation-proof requests must also cover every requested scope.

Decisions are recorded as ``federation.decision`` ledger events; denials
with ``guard_review`` notify the Guard observer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Routine cross-boundary scopes: safe to allow with an authenticated origin.
ROUTINE_EXTERNAL_SCOPES = frozenset({
    "catalog.quote",
    "negotiate.offer",
    "order.status",
    "evidence.provide",
})

#: Sensitive cross-boundary scopes: always deny + Guard review.
SENSITIVE_EXTERNAL_SCOPES = frozenset({
    "credential.read",
    "credential.share",
    "payment.execute",
    "delegation.grant",
    "identity.assert",
    "session.transfer",
})

FEDERATION_DECISION_EVENT = "federation.decision"
FEDERATED_TOOL = "gateway.send"


@dataclass(frozen=True)
class FederatedDecision:
    outcome: str  # allow | deny | require_approval
    reason: str
    guard_review: bool
    proof_level: str
    requested_scopes: tuple[str, ...]
    sensitive_scopes: tuple[str, ...]


def authorize_federated_request(
    *,
    agent_id: str,
    external_agent_id: str,
    scopes: list[str],
    proof_level: str,
    delegation_scopes: list[str] | None = None,
) -> FederatedDecision:
    """Authorize one external request against proof level + scopes (pure)."""
    requested = tuple(sorted({str(scope) for scope in scopes or []}))
    sensitive = tuple(sorted(set(requested) & SENSITIVE_EXTERNAL_SCOPES))
    unknown = sorted(set(requested) - ROUTINE_EXTERNAL_SCOPES - SENSITIVE_EXTERNAL_SCOPES)
    if not agent_id or not external_agent_id:
        return FederatedDecision(
            "deny", "Missing local or external agent identity.",
            True, proof_level, requested, sensitive,
        )
    if not requested:
        return FederatedDecision(
            "deny", "The external request names no scopes; malformed requests are denied.",
            True, proof_level, requested, sensitive,
        )
    if unknown:
        return FederatedDecision(
            "deny",
            f"Unknown external scopes fail closed: {unknown}.",
            True, proof_level, requested, sensitive,
        )
    if sensitive:
        return FederatedDecision(
            "deny",
            f"Sensitive external scopes are never granted across the boundary: {list(sensitive)}. "
            "Flagged for Guard review.",
            True, proof_level, requested, sensitive,
        )
    if proof_level not in {"operator", "delegation", "receipt"}:
        return FederatedDecision(
            "require_approval",
            "The external origin is unauthenticated (unverified signature); "
            "a human must approve before any routine exchange proceeds.",
            False, proof_level, requested, sensitive,
        )
    if proof_level == "delegation":
        granted = {str(scope) for scope in delegation_scopes or []}
        missing = sorted(set(requested) - granted)
        if missing:
            return FederatedDecision(
                "deny",
                f"The delegation credential does not cover scopes: {missing}.",
                True, proof_level, requested, sensitive,
            )
    return FederatedDecision(
        "allow",
        f"Routine external scopes {list(requested)} with authenticated origin ({proof_level}).",
        False, proof_level, requested, sensitive,
    )


def record_federated_decision(
    store,
    run: dict[str, Any],
    decision: FederatedDecision,
    *,
    external_agent_id: str,
    guard_observer=None,
) -> dict[str, Any]:
    """Record a federated decision; notify Guard when review is flagged."""
    status = {"allow": "success", "deny": "denied"}.get(decision.outcome, "pending")
    event = store.add_event(run, FEDERATION_DECISION_EVENT, status, {
        "summary": decision.reason,
        "outcome": decision.outcome,
        "guard_review": decision.guard_review,
        "proof_level": decision.proof_level,
        "requested_scopes": list(decision.requested_scopes),
        "sensitive_scopes": list(decision.sensitive_scopes),
        "external_agent_id": external_agent_id,
    }, FEDERATED_TOOL)
    if guard_observer is not None and decision.guard_review:
        try:
            guard_observer.observe(event)
        except Exception:
            pass
    return event
