from __future__ import annotations

from typing import Any

from runtime.guard.case_manager import GuardCaseManager
from runtime.store import RuntimeStore


class GuardObserver:
    """Deterministic high-signal observer; no model calls on ordinary events."""

    def __init__(self, store: RuntimeStore, cases: GuardCaseManager | None = None):
        self.store = store
        self.cases = cases or GuardCaseManager(store)

    def observe(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") != "aip.decision" or event.get("status") != "denied":
            return None
        reason = str(event.get("payload", {}).get("reason", "")).casefold()
        tool = str(event.get("tool") or "unknown")
        if "origin" in reason or "host" in reason or "website" in reason:
            severity, signal = "high", "unauthorized_origin"
        elif "capability" in reason or "permission" in reason:
            severity, signal = "high", "capability_boundary_denied"
        elif tool == "agent.delegate":
            severity, signal = "medium", "delegation_denied"
        else:
            severity, signal = "medium", "policy_denial"
        run = self.store.get_run(event["run_id"])
        if not run:
            return None
        return self.cases.open_case(
            run, event, severity=severity, signal=signal,
            summary=f"AIP denied {tool}: {str(event.get('payload', {}).get('reason', 'policy boundary'))[:240]}",
        )
