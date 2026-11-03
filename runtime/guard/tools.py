from __future__ import annotations

"""Guard investigative tools: named, schema-validated, permission-checked operations.

Read tools return sanitized, bounded slices of the evidence service; they never
expose agent conversations, raw SQL, or filesystem access. Action tools route
through the trusted Detective, Interrogator, and containment implementations.
"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from runtime.guard.case_manager import GuardCaseManager
from runtime.guard.evidence import GuardEvidenceService, _sanitize
from runtime.guard.schemas import DetectiveReport
from runtime.store import RuntimeStore


class CaseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=128)


class GetAgentArgs(CaseArgs):
    agent_id: str | None = Field(default=None, max_length=128)


class GetEventsArgs(CaseArgs):
    event_type: str | None = Field(default=None, max_length=128)
    tool: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=50, ge=1, le=120)


class GetBrowserEvidenceArgs(CaseArgs):
    limit: int = Field(default=20, ge=1, le=50)


class GetCaseHistoryArgs(CaseArgs):
    limit: int = Field(default=50, ge=1, le=100)


class RequestEvidenceArgs(CaseArgs):
    event_type: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=50, ge=1, le=120)


class SubmitFindingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=128)
    report: dict[str, Any]


class RequestContainmentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=1000)
    capability: str | None = Field(default=None, max_length=64)


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "guard.get_agent", "description": "Read sanitized records for agents related to the case.", "args": "GetAgentArgs"},
    {"name": "guard.get_goal", "description": "Read the original authorized user goal for the case.", "args": "CaseArgs"},
    {"name": "guard.get_events", "description": "Read sanitized runtime events, optionally filtered by type or tool.", "args": "GetEventsArgs"},
    {"name": "guard.get_delegation_chain", "description": "Read the suspect delegation chain from root to suspect.", "args": "CaseArgs"},
    {"name": "guard.get_browser_evidence", "description": "Read sanitized page-derived research evidence.", "args": "GetBrowserEvidenceArgs"},
    {"name": "guard.get_aip_decisions", "description": "Read AIP authorization decisions for the case.", "args": "GetEventsArgs"},
    {"name": "guard.get_related_agents", "description": "Read sanitized records for every agent in the case scope.", "args": "CaseArgs"},
    {"name": "guard.get_case_history", "description": "Read case transitions and appended case evidence.", "args": "GetCaseHistoryArgs"},
    {"name": "guard.ask_agent", "description": "Run one adaptive interview turn with the suspect agent.", "args": "CaseArgs"},
    {"name": "guard.request_additional_evidence", "description": "Reconstruct a filtered evidence bundle for follow-up review.", "args": "RequestEvidenceArgs"},
    {"name": "guard.submit_findings", "description": "Validate and persist an evidence-cited Detective report.", "args": "SubmitFindingsArgs"},
    {"name": "guard.request_containment", "description": "Request containment for human approval without applying it.", "args": "RequestContainmentArgs"},
]

CONTAINABLE_ACTIONS = {"pause_agent", "deny_pending", "revoke_capability", "isolate_browser"}


class GuardToolError(RuntimeError):
    pass


class GuardTools:
    """Schema-validated Guard operations over the sanitized evidence service."""

    def __init__(self, store: RuntimeStore, providers: Any = None, runtime: Any = None,
                 cases: GuardCaseManager | None = None,
                 evidence: GuardEvidenceService | None = None):
        self.store = store
        self.providers = providers
        self.runtime = runtime if runtime is not None else providers
        self.cases = cases or GuardCaseManager(store)
        self.evidence = evidence or GuardEvidenceService(store, self.cases)

    def _bundle(self, case_id: str, principal: str) -> dict[str, Any]:
        try:
            return self.evidence.reconstruct_case(case_id, principal)
        except PermissionError as exc:
            raise GuardToolError(str(exc)) from exc

    def get_agent(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = GetAgentArgs.model_validate(args)
        bundle = self._bundle(parsed.case_id, principal)
        agents = bundle["related_agents"]
        if parsed.agent_id:
            agents = [item for item in agents if item["agent_id"] == parsed.agent_id]
            if not agents:
                raise GuardToolError("Agent is not in this case scope.")
        return {"agents": agents}

    def get_goal(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = CaseArgs.model_validate(args)
        return {"goal": self._bundle(parsed.case_id, principal)["original_goal"]}

    def get_events(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = GetEventsArgs.model_validate(args)
        events = self._bundle(parsed.case_id, principal)["runtime_events"]
        if parsed.event_type:
            events = [item for item in events if item["type"] == parsed.event_type]
        if parsed.tool:
            events = [item for item in events if item.get("tool") == parsed.tool]
        return {"events": events[-parsed.limit:]}

    def get_delegation_chain(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = CaseArgs.model_validate(args)
        bundle = self._bundle(parsed.case_id, principal)
        return {"delegation_chain": bundle["delegation_chain"], "related_agents": bundle["related_agents"]}

    def get_browser_evidence(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = GetBrowserEvidenceArgs.model_validate(args)
        return {"research_evidence": self._bundle(parsed.case_id, principal)["research_evidence"][:parsed.limit]}

    def get_aip_decisions(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = GetEventsArgs.model_validate(args)
        events = [item for item in self._bundle(parsed.case_id, principal)["runtime_events"] if item["type"] == "aip.decision"]
        if parsed.tool:
            events = [item for item in events if item.get("tool") == parsed.tool]
        return {"decisions": events[-parsed.limit:]}

    def get_related_agents(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = CaseArgs.model_validate(args)
        return {"agents": self._bundle(parsed.case_id, principal)["related_agents"]}

    def get_case_history(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = GetCaseHistoryArgs.model_validate(args)
        if not self.cases.can_read(parsed.case_id, principal):
            raise GuardToolError("Guard evidence read permission is required.")
        case = self.cases.get_case(parsed.case_id)
        if not case:
            raise GuardToolError("Guard case not found.")
        return {
            "status": case["status"],
            "transitions": case["transitions"][-parsed.limit:],
            "case_evidence": [
                {"id": item["id"], "source_type": item["source_type"], "source_id": item["source_id"],
                 "timestamp": item["timestamp"], "payload": _sanitize(item["payload"])}
                for item in case["evidence"][-parsed.limit:]
            ],
        }

    def request_additional_evidence(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        parsed = RequestEvidenceArgs.model_validate(args)
        bundle = self._bundle(parsed.case_id, principal)
        events = bundle["runtime_events"]
        if parsed.event_type:
            events = [item for item in events if item["type"] == parsed.event_type]
        bundle["runtime_events"] = events[-parsed.limit:]
        return bundle

    def submit_findings(self, args: dict[str, Any], principal: str = "guard_detective",
                        provider: str = "", model: str = "", usage: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed = SubmitFindingsArgs.model_validate(args)
        try:
            report = DetectiveReport.model_validate(parsed.report)
        except ValidationError as exc:
            raise GuardToolError("Findings did not match the evidence report schema.") from exc
        bundle = self._bundle(parsed.case_id, principal)
        allowed_ids = ({item["id"] for item in bundle["runtime_events"]}
                       | {item["id"] for item in bundle["case_evidence"]}
                       | {item["id"] for item in bundle["research_evidence"]})
        cited_ids = {evidence_id for finding in report.findings for evidence_id in finding.evidence_ids}
        unknown = cited_ids - allowed_ids
        if unknown:
            raise GuardToolError(
                f"Findings cited evidence not supplied ({', '.join(sorted(unknown)[:4])}); nothing was recorded.")
        case = self.cases.get_case(parsed.case_id)
        report_id = f"detective-report-{uuid4()}"
        self.store.append_guard_evidence(parsed.case_id, "guard_report", report_id, case["updated_at"], {
            "interpretation": True, "provider": provider, "model": model,
            "usage": usage or {}, "report": report.model_dump(mode="json"),
            "source_evidence_ids": sorted(cited_ids),
        })
        return {"report_id": report_id, "report": report.model_dump(mode="json"), "source_evidence_ids": sorted(cited_ids)}

    async def ask_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        from runtime.guard.interrogation import GuardInterrogator
        parsed = CaseArgs.model_validate(args)
        if self.providers is None:
            raise GuardToolError("An interview requires configured model providers.")
        return await GuardInterrogator(self.store, self.providers, self.cases, self.evidence).ask_next(parsed.case_id)

    def request_containment(self, args: dict[str, Any], principal: str = "guard_detective") -> dict[str, Any]:
        # Trusted application stays in containment.apply with a local_user principal;
        # this tool only records the request and routes the case to human review.
        parsed = RequestContainmentArgs.model_validate(args)
        if parsed.action not in CONTAINABLE_ACTIONS:
            raise GuardToolError("Containment request must name a supported action.")
        if not self.cases.can_read(parsed.case_id, principal):
            raise GuardToolError("Guard evidence read permission is required.")
        case = self.cases.get_case(parsed.case_id)
        if not case:
            raise GuardToolError("Guard case not found.")
        if case["status"] not in {"investigating", "interviewing", "evaluating", "awaiting_human_review"}:
            raise GuardToolError("Containment can only be requested while a case is under review.")
        self.store.append_guard_evidence(parsed.case_id, "guard_action", f"guard-containment-request-{uuid4()}", case["updated_at"], {
            "action": "containment_requested_by_guard", "requested_action": parsed.action,
            "capability": parsed.capability, "reason": parsed.reason, "requested_by": principal,
            "authorization": "pending local user approval",
        })
        if case["status"] != "awaiting_human_review":
            self.cases.transition(parsed.case_id, "awaiting_human_review",
                                  f"Guard requested {parsed.action}; waiting for local user approval.", actor=principal)
        return {"case_id": parsed.case_id, "requested_action": parsed.action,
                "status": "awaiting_human_review", "authorization": "pending local user approval"}
