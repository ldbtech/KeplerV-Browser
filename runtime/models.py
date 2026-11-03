from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class ModelDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_assistant_message: dict[str, Any] = Field(default_factory=dict)


class RunRequest(StrictModel):
    goal_id: str = Field(min_length=1, max_length=128)
    goal_title: str = Field(min_length=1, max_length=240)
    goal_description: str = Field(min_length=1, max_length=8000)
    task: str = Field(default="Search the public web for relevant sources and summarize three source-backed findings.", min_length=1, max_length=2000)
    source_url: HttpUrl | None = None
    step_budget: int = Field(default=16, ge=1, le=24)
    token_budget: int = Field(default=50000, ge=1000, le=50000)

    @field_validator("source_url")
    @classmethod
    def require_public_https_origin(cls, value: HttpUrl | None) -> HttpUrl | None:
        from runtime.aip.policy import is_demo_url
        if value is None:
            return None
        if is_demo_url(str(value)):
            return value
        if value.scheme != "https" or value.port not in (None, 443) or value.username or value.password:
            raise ValueError("The research source must be a credential-free HTTPS URL on the standard port.")
        return value


class ControlRequest(StrictModel):
    action: Literal["pause", "resume", "stop", "approve", "deny"]
    call_id: str | None = Field(default=None, max_length=128)


class MailAccountRef(StrictModel):
    provider: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=256)


class MailListQuery(MailAccountRef):
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=10, ge=1, le=25)


class MailReadQuery(MailAccountRef):
    pass


class RuntimeEvent(BaseModel):
    id: str
    sequence: int
    run_id: str
    agent_id: str
    goal_id: str
    type: str
    status: str
    tool: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)


class AgentRecord(BaseModel):
    id: str
    run_id: str
    goal_id: str
    parent_agent_id: str | None = None
    name: str = "Muse Researcher"
    provider: str
    model: str
    task: str
    status: str
    permissions: list[str]
    tools: list[str]
    step_budget: int
    token_budget: int
    steps: int = 0
    tokens_used: int = 0
    error: str | None = None
    created_at: str
    updated_at: str


class EvidenceRecord(BaseModel):
    id: str
    goal_id: str
    agent_id: str
    run_id: str
    finding: str
    source_url: str
    quote: str = ""
    created_at: str
