from __future__ import annotations

from typing import Any


def assess_rule_risk(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Deterministic high-signal screening; model judgments never replace AIP policy."""
    if event.get("type") != "aip.decision" or event.get("status") != "denied":
        return None
    reason = str(event.get("payload", {}).get("reason", "")).casefold()
    tool = str(event.get("tool") or "unknown")
    if tool == "agent.delegate":
        return "medium", "delegation_denied", "A delegation request was denied by the trusted runtime."
    if "origin" in reason or "host" in reason or "website" in reason:
        return "high", "unauthorized_origin", "The request crossed an origin boundary and was denied."
    if "capability" in reason or "permission" in reason:
        return "high", "capability_boundary_denied", "The request exceeded the agent's assigned capabilities and was denied."
    return "medium", "policy_denial", f"AIP denied {tool}: {str(event.get('payload', {}).get('reason', 'policy boundary'))[:240]}"
