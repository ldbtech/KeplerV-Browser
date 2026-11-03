from __future__ import annotations

from typing import Any

from runtime.aip.policy import SERVICE_TOOLS


def assess_rule_risk(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Deterministic high-signal screening; model judgments never replace AIP policy."""
    event_type = event.get("type")
    if event_type == "federation.decision" and event.get("status") == "denied":
        payload = event.get("payload", {})
        if payload.get("guard_review") or payload.get("sensitive_scopes"):
            return "high", "federated_sensitive_denied", (
                "A sensitive external request was denied at the federated boundary "
                "and flagged for Guard review."
            )
        return "medium", "federated_denied", (
            "A federated external request was denied: unauthenticated origin or uncovered scope."
        )
    if event_type == "gateway.external_contradiction":
        return "medium", "external_contradiction", (
            "An external exchange contradicts trusted catalog or ledger evidence."
        )
    if event_type == "gateway.external_behavior_flagged":
        payload = event.get("payload", {})
        classification = str(payload.get("classification") or "external_policy_violation")
        severity = "high" if classification in {"injection", "unauthorized-request"} else "medium"
        return severity, "external_agent_behavior", (
            f"Guard detected {classification.replace('-', ' ')} markers in an untrusted external-agent reply. "
            "The original reply is retained in the Trust Gateway transcript for review."
        )
    if event_type == "gateway.ssrf_blocked":
        return "high", "gateway_ssrf_blocked", (
            "The Trust Gateway blocked an external-agent endpoint or redirect as an SSRF risk."
        )
    if event_type == "gateway.provenance_unknown_session":
        return "medium", "provenance_unknown_session", (
            "A gateway session opened to an external agent with unknown or suspected identity."
        )
    if event_type == "gateway.limit_exceeded":
        return "medium", "gateway_limit_exceeded", (
            "A gateway session exceeded its message, token, or cost budget."
        )
    if event_type == "gateway.validation_failed":
        return "medium", "gateway_validation_failed", (
            "An external agent sent a message that failed strict gateway validation."
        )
    if event_type == "service.access_denied":
        tool = str(event.get("tool") or "unknown service")
        return "high", "service_access_denied", (
            f"Service access was denied for {tool}: the agent lacks a grant for this goal."
        )
    if event_type != "aip.decision" or event.get("status") != "denied":
        return None
    reason = str(event.get("payload", {}).get("reason", "")).casefold()
    tool = str(event.get("tool") or "unknown")
    if tool == "agent.delegate":
        return "medium", "delegation_denied", "A delegation request was denied by the trusted runtime."
    if tool in SERVICE_TOOLS:
        return "high", "service_access_denied", (
            "A service request was denied: the agent lacks a grant for this goal."
        )
    if "origin" in reason or "host" in reason or "website" in reason:
        return "high", "unauthorized_origin", "The request crossed an origin boundary and was denied."
    if "capability" in reason or "permission" in reason:
        return "high", "capability_boundary_denied", "The request exceeded the agent's assigned capabilities and was denied."
    return "medium", "policy_denial", f"AIP denied {tool}: {str(event.get('payload', {}).get('reason', 'policy boundary'))[:240]}"
