from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.guard.parsing import ModelOutputError, generate_valid_json
from runtime.providers import ModelProviderError

IntentType = Literal[
    "CREATE_GOAL", "MODIFY_GOAL", "START_AGENT", "PAUSE_AGENT", "RESUME_AGENT",
    "STOP_AGENT", "GET_STATUS", "INSPECT_RESOURCE", "SHOW_EVIDENCE",
    "OPEN_INVESTIGATION", "REQUEST_EXPLANATION", "REQUEST_APPROVAL_DETAILS",
    "APPROVE_ACTION", "DENY_ACTION", "CHANGE_APPEARANCE",
    "CONNECT_ACCOUNT", "DISCONNECT_ACCOUNT", "LIST_CONNECTED_ACCOUNTS",
    "OPEN_SERVICE", "SEARCH_EMAIL", "READ_EMAIL", "PREPARE_EMAIL_DRAFT",
    "REQUEST_SEND_EMAIL", "OPEN_MEDIA", "PAUSE_MEDIA", "RESUME_MEDIA",
    "CLOSE_MEDIA", "SHOW_SERVICE_PERMISSIONS", "REVOKE_AGENT_SERVICE_ACCESS",
]

INTENT_TYPES = list(IntentType.__args__)


class VoiceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goals: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    agents: list[dict[str, Any]] = Field(default_factory=list, max_length=80)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=120)
    resources: list[dict[str, Any]] = Field(default_factory=list, max_length=120)
    approvals: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    cases: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    accounts: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    selection: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[str] = Field(default_factory=list, max_length=12)
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=8)


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(min_length=1, max_length=2000)
    context: VoiceContext


class StructuredIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: IntentType
    target_id: str | None = Field(default=None, max_length=256)
    args: dict[str, Any] = Field(default_factory=dict)
    ambiguous: bool = False
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    permission: Literal["read", "act", "approve"] = "read"
    summary_for_speech: str = Field(min_length=1, max_length=500)


CALL_TIMEOUT_SECONDS = 60


class InterpretationError(RuntimeError):
    pass

_SYSTEM_PROMPT = """You are KeplerV's intent interpreter. Convert one spoken command into a validated structured intent.

Intent types: CREATE_GOAL (new objective; set args.start_research true when the user asks to research, find, or investigate something), MODIFY_GOAL (change title, description, constraints, or tasks of an existing goal), START_AGENT (begin research for a goal), PAUSE_AGENT, RESUME_AGENT, STOP_AGENT, GET_STATUS (progress or status of a goal or agent), INSPECT_RESOURCE (open a web resource), SHOW_EVIDENCE (focus a finding), OPEN_INVESTIGATION (open a Guard case), REQUEST_EXPLANATION (why something happened, using case records), REQUEST_APPROVAL_DETAILS (describe a pending approval), APPROVE_ACTION, DENY_ACTION, CHANGE_APPEARANCE (theme or display), CONNECT_ACCOUNT (connect a gmail/outlook/fake-mail account; args {provider, email}), DISCONNECT_ACCOUNT (args {provider, email}), LIST_CONNECTED_ACCOUNTS, OPEN_SERVICE (open the services surface; args {provider}), SEARCH_EMAIL (args {query, provider?, email?}), READ_EMAIL (args {message_id?, provider?, email?}; use target_id for a listed message), PREPARE_EMAIL_DRAFT (args {to, subject?, body?}; prepares only, never sends), REQUEST_SEND_EMAIL (asks approval to send a draft; never sends by voice), OPEN_MEDIA (args {target: local-video|youtube|netflix, video_id?}), PAUSE_MEDIA, RESUME_MEDIA, CLOSE_MEDIA (args {target?}), SHOW_SERVICE_PERMISSIONS (args {provider?, email?}), REVOKE_AGENT_SERVICE_ACCESS (args {provider?, email?}; needs explicit account confirmation).

Rules:
- target_id must be an exact id from the supplied context, or null. Never invent identifiers. Message ids come from context.messages, account refs from context.accounts (email or provider:email).
- Resolve references ("that agent", "the second paper", "the one working on enrollment", "my gmail", "that email about enrollment") using the selection, then recency, then name matches. Evidence items are numbered in presentation order starting at 1.
- If the target is genuinely ambiguous between equally plausible entities, set needs_clarification true with a short clarification_question and do not choose. Never silently pick between several accounts.
- CREATE_GOAL needs args {title, description, start_research}; MODIFY_GOAL needs args describing the change; CHANGE_APPEARANCE needs args {theme: light|dark|system}; CONNECT_ACCOUNT needs args {provider}; SEARCH_EMAIL needs args {query}; PREPARE_EMAIL_DRAFT needs args {to}; OPEN_MEDIA needs args {target}.
- REQUEST_SEND_EMAIL and REVOKE_AGENT_SERVICE_ACCESS always need explicit human confirmation in the UI; they never execute from voice alone.
- summary_for_speech is one short sentence confirming the understood action, or the clarification question.
- Return JSON only matching the schema."""

#: Required args per intent type. Missing args become a clarification request,
#: never a guessed value.
REQUIRED_INTENT_ARGS: dict[str, tuple[str, ...]] = {
    "CONNECT_ACCOUNT": ("provider",),
    "SEARCH_EMAIL": ("query",),
    "PREPARE_EMAIL_DRAFT": ("to",),
    "OPEN_MEDIA": ("target",),
}

_CLARIFICATION_QUESTIONS = {
    "CONNECT_ACCOUNT": "Which service should I connect: Gmail, Outlook, or the fake test mailbox?",
    "SEARCH_EMAIL": "What should I search your mail for?",
    "PREPARE_EMAIL_DRAFT": "Who should the draft be addressed to?",
    "OPEN_MEDIA": "Which media should I open: local video, YouTube, or Netflix?",
}


def _known_ids(context: VoiceContext) -> set[str]:
    known: set[str] = set()
    for item in (*context.goals, *context.agents, *context.evidence, *context.resources,
                 *context.approvals, *context.cases, *context.messages):
        value = item.get("id")
        if isinstance(value, str) and value:
            known.add(value)
    for account in context.accounts:
        email = account.get("email")
        provider = account.get("provider")
        if isinstance(email, str) and email:
            known.add(email)
            if isinstance(provider, str) and provider:
                known.add(f"{provider}:{email}")
    for value in context.selection.values():
        if isinstance(value, str) and value:
            known.add(value)
    return known


def _missing_required_arg(intent: StructuredIntent) -> str | None:
    for name in REQUIRED_INTENT_ARGS.get(intent.type, ()):
        value = intent.args.get(name)
        if not isinstance(value, str) or not value.strip():
            return name
    return None


async def interpret(transcript: str, context: VoiceContext, providers: Any, model: str | None = None) -> StructuredIntent:
    provider = providers.active_provider
    chosen = model or providers.active_model
    adapter = providers.get(provider)
    payload = json.dumps({"transcript": transcript, "context": context.model_dump(mode="json")},
                         ensure_ascii=False)
    try:
        intent, _ = await generate_valid_json(
            adapter, chosen,
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": payload}],
            StructuredIntent.model_validate, timeout=CALL_TIMEOUT_SECONDS)
    except ModelProviderError as exc:
        raise InterpretationError(f"Intent recognition failed: {exc}") from exc
    except ModelOutputError as exc:
        raise InterpretationError("I could not understand that request. Try rephrasing it.") from exc
    if intent.target_id and intent.target_id not in _known_ids(context):
        return StructuredIntent(
            type=intent.type, target_id=None, args=intent.args, ambiguous=True,
            needs_clarification=True,
            clarification_question="Which one did you mean? I could not match that reference.",
            permission="read", summary_for_speech="Which one did you mean?")
    missing = _missing_required_arg(intent)
    if missing and not intent.needs_clarification:
        question = _CLARIFICATION_QUESTIONS.get(intent.type, "Could you say a little more about that?")
        return StructuredIntent(
            type=intent.type, target_id=intent.target_id, args=intent.args, ambiguous=True,
            needs_clarification=True, clarification_question=question,
            permission="read", summary_for_speech=question)
    return intent
