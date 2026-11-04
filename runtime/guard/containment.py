from __future__ import annotations

from typing import Any
from uuid import uuid4

from runtime.guard.auto_containment import (
    AUTO_NOTICE_HINT,
    auto_authorization,
    permitted_auto_actions,
    resolve_auto_mode,
)
from runtime.guard.case_manager import GuardCaseManager
from runtime.store import RuntimeStore


class GuardContainment:
    """Human-authorized containment routed through the existing trusted runtime controls.

    Policy-gated automatic containment (:meth:`apply_auto`) reuses the same
    trusted controls but records a policy authorization instead of a user
    action, and always emits a user-visible audit event.
    """

    ACTIVE_RUN_STATES = {"created", "idle", "running", "blocked", "paused"}
    REVOCABLE_CAPABILITIES = {"browser.read", "browser.interact", "delegate"}

    #: Case-state hops that route an early case to human review for auto actions.
    _AUTO_ROUTE = {
        "detected": "triaged",
        "triaged": "awaiting_human_review",
        "investigating": "awaiting_human_review",
        "interviewing": "awaiting_human_review",
        "evaluating": "awaiting_human_review",
        "awaiting_evidence": "investigating",
        "reopened": "investigating",
    }

    def __init__(self, store: RuntimeStore, runtime: Any, cases: GuardCaseManager | None = None):
        self.store = store
        self.runtime = runtime
        self.cases = cases or GuardCaseManager(store)

    async def apply(self, case_id: str, action: str, reason: str, principal: str = "local_user",
                    capability: str | None = None) -> dict[str, Any]:
        if principal != "local_user" or not self.cases.can_read(case_id, principal):
            raise PermissionError("Only the authorized local user can approve Guard containment.")
        case = self.cases.get_case(case_id)
        if not case:
            raise KeyError("Guard case not found.")
        if action == "request_human_review":
            if case["status"] not in {"investigating", "interviewing", "evaluating"}:
                raise ValueError("This case is not in an investigation state awaiting review.")
            self.cases.transition(case_id, "awaiting_human_review", f"Local user requested review: {reason}", actor=principal)
            self.store.append_guard_evidence(case_id, "guard_action", f"guard-review-{uuid4()}", case["updated_at"], {
                "action": action, "reason": reason, "authorized_by": principal,
            })
            return {"case_id": case_id, "action": action, "status": "awaiting_human_review",
                    "authorization": "explicit user action"}
        if case["status"] not in {"awaiting_human_review", "containment_requested"}:
            raise ValueError("Review the current investigation before applying containment.")
        run = self.store.get_run(case["run_id"])
        if not run:
            raise LookupError("The affected agent run is unavailable.")
        if action in {"pause_agent", "resume_agent"}:
            if action == "pause_agent":
                if run["status"] not in {"created", "idle", "running", "blocked"}:
                    raise ValueError("This agent is not in a state that can be paused.")
                runtime_action = "pause"
            elif run["status"] != "paused":
                raise ValueError("The affected agent is not paused.")
            else:
                runtime_action = "resume"
            return await self._apply_control(case, run, action, runtime_action, reason, principal, {})
        if action == "deny_pending":
            return await self._apply_control(case, run, action, "pause", reason, principal,
                                             {"pending_denied": self.runtime.deny_pending_approvals(run["id"])},
                                             allow_paused=True)
        if action == "revoke_capability":
            if capability not in self.REVOCABLE_CAPABILITIES:
                raise ValueError("Capability must be one of: browser.read, browser.interact, delegate.")
            if capability not in run["permissions"]:
                raise ValueError("That capability is not granted to this agent.")
            remaining = self.store.revoke_run_capability(run["id"], capability)
            self.store.add_event(run, "agent.contained", "success", {
                "action": action, "capability": capability,
                "summary": f"Capability {capability} revoked by the local user.",
            })
            return await self._record(case, run, action, reason, principal, {
                "capability": capability, "remaining_permissions": remaining,
                "run_status": self.store.get_run(run["id"])["status"],
            })
        if action == "isolate_browser":
            await self.runtime.isolate_browser(run["id"])
            return await self._record(case, run, action, reason, principal, {
                "run_status": self.store.get_run(run["id"])["status"],
            })
        if action == "restore_browser":
            await self.runtime.restore_browser(run["id"])
            return await self._record(case, run, action, reason, principal, {
                "run_status": self.store.get_run(run["id"])["status"],
            })
        raise ValueError("Unsupported containment action.")

    async def apply_auto(self, case_id: str, action: str, reason: str, *,
                       mode: str, capability: str | None = None) -> dict[str, Any]:
        """Apply one policy-gated automatic action (no user principal).

        The mode must permit the action; anything else raises PermissionError
        so the caller routes it to explicit local-user approval instead.
        Every automatic action records hash-chained Guard evidence and emits
        a user-visible ``guard.auto_containment`` audit event.
        """
        resolved = resolve_auto_mode(mode)
        if action not in permitted_auto_actions(resolved):
            raise PermissionError(
                f"{action} needs local-user approval in '{resolved}' auto-containment mode.")
        case = self.cases.get_case(case_id)
        if not case:
            raise KeyError("Guard case not found.")
        case = self._route_for_auto(case_id, action, resolved)
        run = self.store.get_run(case["run_id"])
        if not run:
            raise LookupError("The affected agent run is unavailable.")
        authorization = auto_authorization(resolved)
        actor = "guard_auto_containment"
        if action == "pause_agent":
            if run["status"] not in {"created", "idle", "running", "blocked"}:
                raise ValueError("This agent is not in a state that can be paused.")
            result = await self._apply_control(
                case, run, action, "pause", reason, actor, {},
                authorization=authorization)
        elif action == "deny_pending":
            result = await self._apply_control(
                case, run, action, "pause", reason, actor,
                {"pending_denied": self.runtime.deny_pending_approvals(run["id"])},
                allow_paused=True, authorization=authorization)
        elif action == "revoke_capability":
            if capability not in self.REVOCABLE_CAPABILITIES:
                raise ValueError("Capability must be one of: browser.read, browser.interact, delegate.")
            if capability not in run["permissions"]:
                raise ValueError("That capability is not granted to this agent.")
            remaining = self.store.revoke_run_capability(run["id"], capability)
            self.store.add_event(run, "agent.contained", "success", {
                "action": action, "capability": capability,
                "summary": f"Capability {capability} revoked ({authorization}).",
            })
            result = await self._record(
                case, run, action, reason, actor, {
                    "capability": capability, "remaining_permissions": remaining,
                    "run_status": self.store.get_run(run["id"])["status"],
                }, authorization=authorization)
        elif action == "isolate_browser":
            await self.runtime.isolate_browser(run["id"])
            result = await self._record(
                case, run, action, reason, actor, {
                    "run_status": self.store.get_run(run["id"])["status"],
                }, authorization=authorization)
        else:
            raise ValueError("Unsupported automatic containment action.")
        fresh = self.store.get_run(run["id"]) or run
        self.store.add_event(fresh, "guard.auto_containment", "success", {
            "case_id": case_id, "action": action, "mode": resolved,
            "authorization": authorization, "reason": reason[:500],
            "capability": capability,
            "summary": f"Guard automatically applied {action} ({authorization}): {reason[:200]}",
            "notice": AUTO_NOTICE_HINT,
        })
        return {**result, "mode": resolved}

    def _route_for_auto(self, case_id: str, action: str, mode: str) -> dict[str, Any]:
        case = self.cases.get_case(case_id)
        for _ in range(6):
            if case["status"] in {"awaiting_human_review", "containment_requested"}:
                return case
            nxt = self._AUTO_ROUTE.get(case["status"])
            if not nxt:
                raise ValueError("Reopen the case before applying containment.")
            case = self.cases.transition(
                case_id, nxt,
                f"Policy auto-containment ({mode}) routed {action} to human review.",
                actor="guard_auto_containment")
        raise ValueError("The case could not be routed for automatic containment.")

    async def _apply_control(self, case: dict[str, Any], run: dict[str, Any], action: str,
                             runtime_action: str, reason: str, principal: str,
                             extra: dict[str, Any], allow_paused: bool = False,
                             authorization: str = "explicit user action") -> dict[str, Any]:
        if runtime_action == "pause" and run["status"] not in (self.ACTIVE_RUN_STATES if allow_paused else {"created", "idle", "running", "blocked"}):
            raise ValueError("This agent is not in a state that can be paused.")
        updated_run = await self.runtime.control(run["id"], runtime_action)
        return await self._record(case, run, action, reason, principal, {
            **extra, "run_status": updated_run.get("status"),
        }, authorization=authorization)

    async def _record(self, case: dict[str, Any], run: dict[str, Any], action: str,
                      reason: str, principal: str, extra: dict[str, Any],
                      authorization: str = "explicit user action") -> dict[str, Any]:
        case_id = case["id"]
        if case["status"] == "awaiting_human_review":
            if authorization == "explicit user action":
                transition_reason = f"Local user authorized {action}: {reason}"
            else:
                transition_reason = f"{authorization} applied {action}: {reason}"
            current = self.cases.transition(case_id, "containment_requested", transition_reason, actor=principal)
        else:
            current = case
        action_id = f"guard-containment-{uuid4()}"
        try:
            self.store.append_guard_evidence(case_id, "guard_containment_action", action_id, current["updated_at"], {
                "action": action, "reason": reason, "authorized_by": principal,
                "authorization": authorization, "agent_id": case["agent_id"],
                "aip_policy_modified": False, **extra,
            })
            current = self.cases.get_case(case_id)
            if current and current["status"] == "containment_requested":
                self.cases.transition(case_id, "awaiting_human_review", f"{action} applied by the trusted runtime; review remains open.", actor="guard_containment")
            return {"case_id": case_id, "action": action, "status": self.cases.get_case(case_id)["status"],
                    "agent_status": extra.get("run_status"), "authorization": authorization, **extra}
        except Exception as exc:
            latest = self.cases.get_case(case_id)
            self.store.append_guard_evidence(case_id, "guard_containment_error", action_id, latest["updated_at"], {
                "action": action, "authorized_by": principal, "error": str(exc)[:500],
            })
            if latest and latest["status"] == "containment_requested":
                self.cases.transition(case_id, "awaiting_human_review", "Containment was not applied; review the recorded error.", actor="guard_containment")
            raise
