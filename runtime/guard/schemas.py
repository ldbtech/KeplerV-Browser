from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CaseStatus = Literal[
    "detected", "triaged", "investigating", "interviewing", "evaluating",
    "awaiting_evidence", "awaiting_human_review", "containment_requested",
    "inconclusive", "closed", "reopened",
]


class GuardEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    case_id: str
    source_type: str
    source_id: str
    timestamp: str
    payload: dict[str, Any]
    previous_hash: str
    integrity_hash: str


class GuardCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    trigger_event_id: str
    run_id: str
    goal_id: str
    goal_title: str
    agent_id: str
    parent_agent_id: str | None = None
    provider: str
    model: str
    status: CaseStatus
    severity: Literal["low", "medium", "high"]
    signal: str
    summary: str
    created_at: str
    updated_at: str
    evidence: list[GuardEvidence] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)


class GuardTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CaseStatus
    reason: str = Field(min_length=3, max_length=1000)


class GuardContainmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause_agent", "resume_agent", "deny_pending", "revoke_capability",
                    "isolate_browser", "restore_browser", "request_human_review"]
    reason: str = Field(min_length=3, max_length=1000)
    capability: str | None = Field(default=None, max_length=64)


class DetectiveFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=8, max_length=1000)
    classification: Literal[
        "observed_attempt", "authorized_completion", "denied_action", "failed_action",
        "agent_statement", "unverified_claim", "inference",
    ]
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=8, max_length=1000)


class DetectiveReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=20, max_length=1600)
    assessment: Literal["no_violation_indicated", "suspicious_unresolved", "insufficient_evidence"]
    findings: list[DetectiveFinding] = Field(min_length=1, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)
    recommended_next_step: str = Field(min_length=8, max_length=1000)


class InterviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool
    question: str | None = Field(default=None, min_length=8, max_length=1200)
    rationale: str = Field(min_length=8, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)

    @classmethod
    def validate_decision(cls, value: dict[str, Any]) -> "InterviewDecision":
        decision = cls.model_validate(value)
        if decision.done and decision.question:
            raise ValueError("A completed interview cannot include another question.")
        if not decision.done and not decision.question:
            raise ValueError("An active interview must include a question.")
        if not decision.done and not decision.evidence_ids:
            raise ValueError("An interview question must cite evidence IDs.")
        return decision


class AdjudicatorFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=8, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=8, max_length=1000)


class AgentContradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_statement: str = Field(min_length=8, max_length=1000)
    observed_record: str = Field(min_length=8, max_length=1000)
    evidence_ids: list[str] = Field(min_length=2, max_length=8)
    explanation: str = Field(min_length=8, max_length=1000)


class AdjudicatorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: Literal[
        "no_violation_established", "insufficient_evidence", "further_investigation_required",
        "policy_violation_established", "immediate_protective_action_required",
    ]
    summary: str = Field(min_length=20, max_length=1600)
    findings: list[AdjudicatorFinding] = Field(min_length=1, max_length=12)
    contradictions: list[AgentContradiction] = Field(default_factory=list, max_length=8)
    competing_explanations: list[str] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)
    recommended_response: str = Field(min_length=8, max_length=1000)


class ObserverScreening(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_level: Literal["low", "moderate", "elevated"]
    summary: str = Field(min_length=20, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=8, max_length=1000)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
