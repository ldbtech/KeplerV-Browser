from __future__ import annotations

from typing import Any

from runtime.guard.case_manager import GuardCaseManager
from runtime.guard.risk import assess_rule_risk
from runtime.store import RuntimeStore


class GuardObserver:
    """Deterministic high-signal observer; no model calls on ordinary events."""

    def __init__(self, store: RuntimeStore, cases: GuardCaseManager | None = None):
        self.store = store
        self.cases = cases or GuardCaseManager(store)

    def observe(self, event: dict[str, Any]) -> dict[str, Any] | None:
        assessment = assess_rule_risk(event)
        if assessment is None:
            return None
        severity, signal, summary = assessment
        run = self.store.get_run(event["run_id"])
        if not run:
            return None
        return self.cases.open_case(
            run, event, severity=severity, signal=signal,
            summary=summary,
        )
