from __future__ import annotations

from typing import Any

from runtime.aip.policy import SERVICE_TOOLS


def assess_rule_risk(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Deterministic high-signal screening; model judgments never replace AIP policy."""
    event_type = event.get("type")
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
