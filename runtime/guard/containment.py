from __future__ import annotations

from typing import Any
from uuid import uuid4

from runtime.guard.case_manager import GuardCaseManager
from runtime.store import RuntimeStore


class GuardContainment:
    """Human-authorized containment routed through the existing trusted runtime controls."""

    ACTIVE_RUN_STATES = {"created", "idle", "running", "blocked", "paused"}

    def __init__(self, store: RuntimeStore, runtime: Any, cases: GuardCaseManager | None = None):
        self.store = store
        self.runtime = runtime
        self.cases = cases or GuardCaseManager(store)

    async def apply(self, case_id: str, action: str, reason: str, principal: str = "local_user") -> dict[str, Any]:
        if principal != "local_user" or not self.cases.can_read(case_id, principal):
            raise PermissionError("Only the authorized local user can approve Guard containment.")
        case = self.cases.get_case(case_id)
        if not case:
            raise KeyError("Guard case not found.")
        if case["status"] not in {"awaiting_human_review", "containment_requested"}:
            raise ValueError("Review the current investigation before applying containment.")
        run = self.store.get_run(case["run_id"])
        if not run:
            raise LookupError("The affected agent run is unavailable.")
        if action == "pause_agent":
            if run["status"] not in {"created", "idle", "running", "blocked"}:
                raise ValueError("This agent is not in a state that can be paused.")
            runtime_action = "pause"
        elif action == "resume_agent":
            if run["status"] != "paused":
                raise ValueError("The affected agent is not paused.")
            runtime_action = "resume"
        else:
            raise ValueError("Unsupported containment action.")

        if case["status"] == "awaiting_human_review":
            current = self.cases.transition(case_id, "containment_requested", f"Local user authorized {action}: {reason}", actor=principal)
        else:
            current = case
        action_id = f"guard-containment-{uuid4()}"
        try:
            updated_run = await self.runtime.control(run["id"], runtime_action)
            self.store.append_guard_evidence(case_id, "guard_containment_action", action_id, current["updated_at"], {
                "action": action, "reason": reason, "authorized_by": principal,
                "authorization": "explicit user action", "agent_id": case["agent_id"],
                "run_status": updated_run.get("status"), "aip_policy_modified": False,
            })
            current = self.cases.get_case(case_id)
            if current and current["status"] == "containment_requested":
                self.cases.transition(case_id, "awaiting_human_review", f"{action} applied by the trusted runtime; review remains open.", actor="guard_containment")
            return {"case_id": case_id, "action": action, "status": self.cases.get_case(case_id)["status"],
                    "agent_status": updated_run.get("status"), "authorization": "explicit user action"}
        except Exception as exc:
            latest = self.cases.get_case(case_id)
            self.store.append_guard_evidence(case_id, "guard_containment_error", action_id, latest["updated_at"], {
                "action": action, "authorized_by": principal, "error": str(exc)[:500],
            })
            if latest and latest["status"] == "containment_requested":
                self.cases.transition(case_id, "awaiting_human_review", "Containment was not applied; review the recorded error.", actor="guard_containment")
            raise
