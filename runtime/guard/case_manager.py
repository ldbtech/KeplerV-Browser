from __future__ import annotations

from typing import Any
from uuid import uuid4

from runtime.store import RuntimeStore


class GuardCaseManager:
    def __init__(self, store: RuntimeStore):
        self.store = store

    def open_case(self, run: dict[str, Any], event: dict[str, Any], *, severity: str, signal: str, summary: str) -> dict[str, Any]:
        for existing in (self.store.get_guard_case_for_event(event["id"]), self.store.get_active_guard_case_for_run(run["id"])):
            if existing:
                if existing["trigger_event_id"] == event["id"]:
                    return existing
                self.store.append_guard_evidence(
                    existing["id"], "runtime_event", event["id"], event["timestamp"],
                    {"type": event["type"], "status": event["status"], "tool": event.get("tool"), "payload": event.get("payload", {})},
                )
                return self.store.get_guard_case(existing["id"])
        case_id = f"guard-{uuid4()}"
        case = self.store.create_guard_case({
            "id": case_id, "trigger_event_id": event["id"], "run_id": run["id"],
            "goal_id": run["goal_id"], "goal_title": run["goal_title"], "agent_id": run["agent_id"],
            "parent_agent_id": run.get("parent_agent_id"), "provider": run["provider"], "model": run["model"],
            "status": "detected", "severity": severity, "signal": signal, "summary": summary,
        })
        if case is None:
            return self.store.get_guard_case_for_event(event["id"])
        self.store.append_guard_evidence(
            case["id"], "runtime_event", event["id"], event["timestamp"],
            {"type": event["type"], "status": event["status"], "tool": event.get("tool"), "payload": event.get("payload", {})},
        )
        return self.store.get_guard_case(case["id"])

    def list_cases(self, goal_id: str | None = None, principal: str = "local_user") -> list[dict[str, Any]]:
        cases = self.store.list_permitted_guard_cases(principal)
        if goal_id:
            cases = [case for case in cases if case["goal_id"] == goal_id]
        return [self.store.get_guard_case(case["id"]) for case in cases]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.store.get_guard_case(case_id)

    def can_read(self, case_id: str, principal: str = "local_user") -> bool:
        return self.store.has_guard_case_permission(case_id, principal, "read")

    def transition(self, case_id: str, status: str, reason: str, actor: str = "guard") -> dict[str, Any] | None:
        return self.store.transition_guard_case(case_id, status, reason, actor)

    def verify_integrity(self, case_id: str) -> bool:
        return self.store.verify_guard_case_integrity(case_id)
