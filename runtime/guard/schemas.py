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
