import asyncio
import json

import pytest
from pydantic import ValidationError

from runtime.guard.observer import GuardObserver
from runtime.guard.tools import TOOL_SCHEMAS, GuardToolError, GuardTools
from runtime.models import ModelDecision
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


def _seeded_case(tmp_path):
    store = RuntimeStore(tmp_path / "tools.sqlite3")
    run = store.create_run({
        "goal_id": "goal-tools", "goal_title": "Tooling research",
        "goal_description": "Check Guard tool retrieval.", "task": "Research task.",
        "source_url": "https://docs.python.org/3/library/asyncio.html",
        "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"], ["browser.read"])
    event = store.add_event(run, "aip.decision", "denied", {
        "outcome": "deny", "reason": "Navigation to an unauthorized origin was blocked.",
        "api_key": "must-not-leak",
    }, "browser.navigate")
    store.add_event(run, "agent.completed", "success", {"summary": "Research completed."})
    case = GuardObserver(store).observe(event)
    return store, run, case


def test_tool_registry_lists_all_investigative_tools():
    names = [item["name"] for item in TOOL_SCHEMAS]
    assert names == [
        "guard.get_agent", "guard.get_goal", "guard.get_events", "guard.get_delegation_chain",
        "guard.get_browser_evidence", "guard.get_aip_decisions", "guard.get_related_agents",
        "guard.get_case_history", "guard.ask_agent", "guard.request_additional_evidence",
        "guard.submit_findings", "guard.request_containment",
    ]


def test_read_tools_return_sanitized_bounded_slices(tmp_path):
    store, run, case = _seeded_case(tmp_path)
    tools = GuardTools(store)

    goal = tools.get_goal({"case_id": case["id"]})
    assert goal["goal"]["id"] == "goal-tools"
    agents = tools.get_agent({"case_id": case["id"]})
    assert agents["agents"][0]["agent_id"] == run["agent_id"]
    with pytest.raises(GuardToolError):
        tools.get_agent({"case_id": case["id"], "agent_id": "agent-unknown"})
    decisions = tools.get_aip_decisions({"case_id": case["id"]})
    assert len(decisions["decisions"]) == 1
    assert decisions["decisions"][0]["payload"]["api_key"] == "[redacted]"
    assert "must-not-leak" not in json.dumps(decisions)
    filtered = tools.get_events({"case_id": case["id"], "event_type": "agent.completed", "limit": 5})
    assert [item["type"] for item in filtered["events"]] == ["agent.completed"]
    chain = tools.get_delegation_chain({"case_id": case["id"]})
    assert chain["delegation_chain"] == [run["agent_id"]]
    history = tools.get_case_history({"case_id": case["id"]})
    assert history["status"] == "detected"
    assert history["transitions"] == []
    assert len(history["case_evidence"]) == 1
    browser = tools.get_browser_evidence({"case_id": case["id"], "limit": 5})
    assert browser["research_evidence"] == []
    related = tools.get_related_agents({"case_id": case["id"]})
    assert [item["agent_id"] for item in related["agents"]] == [run["agent_id"]]


def test_read_tools_reject_unknown_args_and_missing_permission(tmp_path):
    store, _, case = _seeded_case(tmp_path)
    tools = GuardTools(store)
    with pytest.raises(ValidationError):
        tools.get_events({"case_id": case["id"], "limit": 500})
    with pytest.raises(ValidationError):
        tools.get_goal({"case_id": case["id"], "raw_sql": "SELECT * FROM runs"})
    store.revoke_guard_case_permission(case["id"], "guard_detective")
    with pytest.raises(GuardToolError):
        tools.get_goal({"case_id": case["id"]})
    with pytest.raises(GuardToolError):
        tools.get_case_history({"case_id": case["id"]})


def _valid_report(case):
    return {
        "summary": "The event record shows one denied navigation attempt and a completed research run.",
        "assessment": "suspicious_unresolved",
        "findings": [{
            "claim": "The agent attempted the blocked navigation.",
            "classification": "denied_action",
            "evidence_ids": [case["evidence"][0]["id"]],
            "explanation": "The runtime event records the policy denial.",
        }],
        "uncertainties": ["No successful access is recorded."],
        "recommended_next_step": "Review the cited denial event.",
    }


def test_submit_findings_validates_schema_and_citations(tmp_path):
    store, _, case = _seeded_case(tmp_path)
    tools = GuardTools(store)
    stored = tools.submit_findings({"case_id": case["id"], "report": _valid_report(case)},
                                   provider="meta", model="muse-wonder-1.0", usage={"total_tokens": 10})
    assert stored["source_evidence_ids"] == [case["evidence"][0]["id"]]
    persisted = store.get_guard_case(case["id"])
    assert any(item["source_type"] == "guard_report" for item in persisted["evidence"])
    assert store.verify_guard_case_integrity(case["id"])
    bad_citation = _valid_report(case)
    bad_citation["findings"][0]["evidence_ids"] = ["evt-not-supplied"]
    with pytest.raises(GuardToolError):
        tools.submit_findings({"case_id": case["id"], "report": bad_citation})
    with pytest.raises(GuardToolError):
        tools.submit_findings({"case_id": case["id"], "report": {"summary": "too short"}})
    assert len([item for item in store.get_guard_case(case["id"])["evidence"] if item["source_type"] == "guard_report"]) == 1


def test_request_containment_routes_to_human_review_without_applying(tmp_path):
    store, run, case = _seeded_case(tmp_path)
    tools = GuardTools(store)
    store.transition_guard_case(case["id"], "investigating", "Started.", "guard")
    result = tools.request_containment({"case_id": case["id"], "action": "isolate_browser", "reason": "Session looks risky."})
    assert result["status"] == "awaiting_human_review"
    assert result["authorization"] == "pending local user approval"
    assert store.get_run(run["id"])["browser_isolated"] is False
    assert store.get_run(run["id"])["status"] == run["status"]
    with pytest.raises(GuardToolError):
        tools.request_containment({"case_id": case["id"], "action": "rewrite_policy", "reason": "Unsupported action."})
    store.transition_guard_case(case["id"], "closed", "Done.", "local_user")
    with pytest.raises(GuardToolError):
        tools.request_containment({"case_id": case["id"], "action": "isolate_browser", "reason": "Case is closed."})


class _InterviewRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, evidence_id):
        self.evidence_id = evidence_id

    def get(self, provider):
        evidence_id = self.evidence_id

        class _Adapter:
            async def generate(self, model, messages, tools):
                if "evidence-based Interrogator" in messages[0]["content"]:
                    return ModelDecision(message=json.dumps({
                        "done": False, "question": "What did the denial event record?",
                        "rationale": "Confirm the recorded attempt.", "evidence_ids": [evidence_id],
                    }))
                return ModelDecision(message="The navigation was denied before execution.")

        return _Adapter()


def test_ask_agent_runs_one_interview_turn(tmp_path):
    async def run_test():
        store, _, case = _seeded_case(tmp_path)
        providers = _InterviewRegistry(case["evidence"][0]["id"])
        tools = GuardTools(store, providers)
        result = await tools.ask_agent({"case_id": case["id"]})
        assert result["turn"] == 1
        assert "denial event" in result["question"]
        loaded = store.get_guard_case(case["id"])
        assert loaded["status"] == "awaiting_human_review"
        assert any(item["source_type"] == "guard_interview_answer" for item in loaded["evidence"])

    asyncio.run(run_test())


def test_ask_agent_requires_providers(tmp_path):
    store, _, case = _seeded_case(tmp_path)
    with pytest.raises(GuardToolError):
        asyncio.run(GuardTools(store).ask_agent({"case_id": case["id"]}))


def _flaky_report(evidence_id, classification):
    return {
        "summary": "The event record shows one denied navigation attempt during research.",
        "assessment": "suspicious_unresolved",
        "findings": [{
            "claim": "The agent attempted the blocked navigation.",
            "classification": classification,
            "evidence_ids": [evidence_id],
            "explanation": "The runtime event records the policy denial.",
        }],
        "uncertainties": [],
        "recommended_next_step": "Review the cited denial event.",
    }


class _FlakyAdapter:
    def __init__(self, evidence_id, recover=True):
        self.evidence_id = evidence_id
        self.recover = recover
        self.calls = 0

    async def generate(self, model, messages, tools):
        self.calls += 1
        classification = "denied_action" if (self.recover and self.calls > 1) else "observed_event"
        return ModelDecision(message=json.dumps(_flaky_report(self.evidence_id, classification)),
                             usage={"total_tokens": 10})


class _FlakyRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        return self.adapter


def test_detective_retries_once_after_schema_correction(tmp_path):
    import asyncio as _asyncio

    from runtime.guard.detective import GuardDetective

    async def run_test():
        store, _, case = _seeded_case(tmp_path)
        adapter = _FlakyAdapter(case["evidence"][0]["id"])
        detective = GuardDetective(store, _FlakyRegistry(adapter))
        result = await detective.investigate(case["id"])
        assert adapter.calls == 2
        assert result["status"] == "awaiting_human_review"
        assert result["report"]["findings"][0]["classification"] == "denied_action"

    _asyncio.run(run_test())


def test_detective_records_failure_after_exhausted_retries(tmp_path):
    import asyncio as _asyncio

    from runtime.guard.detective import GuardDetective, GuardInvestigationError

    async def run_test():
        store, _, case = _seeded_case(tmp_path)
        adapter = _FlakyAdapter(case["evidence"][0]["id"], recover=False)
        detective = GuardDetective(store, _FlakyRegistry(adapter))
        try:
            await detective.investigate(case["id"])
        except GuardInvestigationError as exc:
            assert "not a valid evidence report" in str(exc)
        else:
            raise AssertionError("Exhausted retries must raise.")
        assert adapter.calls == 2
        assert store.get_guard_case(case["id"])["status"] == "awaiting_evidence"

    _asyncio.run(run_test())


def test_generate_valid_json_retries_empty_then_invalid(tmp_path):
    import asyncio as _asyncio

    from runtime.guard.parsing import ModelOutputError, generate_valid_json
    from runtime.guard.schemas import ObserverScreening

    responses = [None, "not json", json.dumps({
        "risk_level": "low", "summary": "A single denied attempt with no successful access recorded.",
        "evidence_ids": ["evt-1"], "rationale": "The denial held.", "uncertainties": [],
    })]

    class _Adapter:
        def __init__(self):
            self.calls = 0

        async def generate(self, model, messages, tools):
            self.calls += 1
            return ModelDecision(message=responses[self.calls - 1], usage={"total_tokens": 5})

    async def run_test():
        adapter = _Adapter()
        screening, usage = await generate_valid_json(
            adapter, "test-model", [{"role": "user", "content": "{}"}],
            ObserverScreening.model_validate, timeout=5, attempts=3)
        assert screening.risk_level == "low"
        assert usage == {"total_tokens": 5}
        assert adapter.calls == 3
        with pytest.raises(ModelOutputError):
            await generate_valid_json(
                _Adapter(), "test-model", [{"role": "user", "content": "{}"}],
                ObserverScreening.model_validate, timeout=5, attempts=1)

    _asyncio.run(run_test())


def test_trim_keeps_trigger_aip_and_guard_events(tmp_path):
    from runtime.guard.evidence import bundle_size, trim_bundle_to_fit

    store, _, case = _seeded_case(tmp_path)
    bundle = {
        "runtime_events": [
            {"id": f"evt-routine-{index}", "type": "model.response.received",
             "payload": {"filler": "x" * 600}} for index in range(30)
        ] + [
            {"id": case["trigger_event_id"], "type": "agent.completed", "payload": {}},
            {"id": "evt-aip", "type": "aip.decision", "payload": {}},
            {"id": "evt-guard", "type": "guard.screening.completed", "payload": {}},
        ],
        "case_evidence": [],
    }
    trimmed = trim_bundle_to_fit(bundle, 2500, minimum_events=4, trigger_event_id=case["trigger_event_id"])
    survivors = {event["id"] for event in trimmed["runtime_events"]}
    assert case["trigger_event_id"] in survivors
    assert "evt-aip" in survivors and "evt-guard" in survivors
    assert bundle_size(trimmed) <= 2500
    assert sum(1 for event in trimmed["runtime_events"] if event["id"].startswith("evt-routine-")) <= 3


def test_trim_drops_oldest_case_evidence_last(tmp_path):
    from runtime.guard.evidence import bundle_size, trim_bundle_to_fit

    bundle = {
        "runtime_events": [{"id": "evt-aip", "type": "aip.decision", "payload": {}}],
        "case_evidence": [
            {"id": f"case-{index}", "source_type": "guard_action", "payload": {"filler": "y" * 900}}
            for index in range(8)
        ],
    }
    trimmed = trim_bundle_to_fit(bundle, 4000, trigger_event_id="evt-aip")
    survivors = [item["id"] for item in trimmed["case_evidence"]]
    assert bundle_size(trimmed) <= 4000
    assert survivors == ["case-4", "case-5", "case-6", "case-7"]
