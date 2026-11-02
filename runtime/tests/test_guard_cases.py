import asyncio
import json

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.guard.evidence import GuardEvidenceService
from runtime.guard.observer import GuardObserver
from runtime.models import ModelDecision
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


def make_run(store: RuntimeStore):
    return store.create_run({
        "goal_id": "goal-japan", "goal_title": "Plan a Japan trip",
        "goal_description": "Find practical travel information.", "task": "Research entry requirements.",
        "source_url": "", "step_budget": 8, "token_budget": 5000,
    }, "meta", "muse-spark-test", ["browser.navigate"], ["browser.read"])


def test_observer_correlates_denial_and_deduplicates_case(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {
        "outcome": "deny", "reason": "Navigation to an unauthorized origin was blocked.",
    }, "browser.navigate")
    observer = GuardObserver(store)

    case = observer.observe(event)
    duplicate = observer.observe(event)

    assert case["trigger_event_id"] == event["id"]
    assert case["run_id"] == run["id"]
    assert case["goal_id"] == "goal-japan"
    assert case["agent_id"] == run["agent_id"]
    assert case["provider"] == "meta"
    assert case["model"] == "muse-spark-test"
    assert case["signal"] == "unauthorized_origin"
    assert duplicate["id"] == case["id"]
    assert len(store.get_guard_case(case["id"])["evidence"]) == 1


def test_runtime_service_connects_aip_denials_to_guard_observer(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    service = RuntimeService(store=store)

    service._event(run["id"], "aip.decision", "denied", {"reason": "permission denied"}, "browser.click")

    cases = service.guard_observer.cases.list_cases()
    assert len(cases) == 1
    assert cases[0]["run_id"] == run["id"]
    assert cases[0]["evidence"][0]["payload"]["type"] == "aip.decision"


def test_guard_evidence_is_retrievable_hash_chained_and_persistent(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    store = RuntimeStore(db_path)
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "permission denied"}, "browser.click")
    case = GuardObserver(store).observe(event)
    store.append_guard_evidence(case["id"], "runtime_event", "evt-followup", event["timestamp"], {"note": "Follow-up evidence."})

    assert store.verify_guard_case_integrity(case["id"])
    assert len(store.get_guard_case(case["id"])["evidence"]) == 2
    reopened = RuntimeStore(db_path)
    loaded = reopened.get_guard_case(case["id"])
    assert loaded["model"] == "muse-spark-test"
    assert len(loaded["evidence"]) == 2
    assert reopened.verify_guard_case_integrity(case["id"])


def test_guard_case_transitions_are_validated_and_audited(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "policy boundary"}, "browser.navigate")
    case = GuardObserver(store).observe(event)

    updated = store.transition_guard_case(case["id"], "triaged", "Observer signal reviewed.", "user")
    assert updated["status"] == "triaged"
    assert updated["transitions"][0]["from_status"] == "detected"
    assert updated["transitions"][0]["to_status"] == "triaged"
    try:
        store.transition_guard_case(case["id"], "interviewing", "Skip investigation steps.")
    except ValueError as exc:
        assert "Invalid Guard case transition" in str(exc)
    else:
        raise AssertionError("Invalid case transition should be rejected")


def test_guard_api_requires_runtime_auth_and_serves_cases(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {"reason": "capability denied"}, "browser.navigate")
    case = GuardObserver(store).observe(event)
    service = RuntimeService(store=store)
    app = create_app(service)

    with TestClient(app) as client:
        assert client.get("/guard/cases").status_code == 401
        headers = {"Authorization": "Bearer test-runtime-token-with-32-characters"}
        listing = client.get("/guard/cases?goal_id=goal-japan", headers=headers)
        detail = client.get(f"/guard/cases/{case['id']}", headers=headers)
        integrity = client.get(f"/guard/cases/{case['id']}/integrity", headers=headers)
        transition = client.post(f"/guard/cases/{case['id']}/transition", headers=headers,
                                 json={"status": "triaged", "reason": "Reviewed the AIP evidence."})

    assert listing.status_code == detail.status_code == integrity.status_code == transition.status_code == 200
    assert listing.json()[0]["id"] == case["id"]
    assert len(detail.json()["evidence"]) == 1
    assert integrity.json() == {"case_id": case["id"], "valid": True}
    assert transition.json()["status"] == "triaged"
    store.revoke_guard_case_permission(case["id"], "local_user")
    with TestClient(app) as client:
        assert client.get(f"/guard/cases/{case['id']}", headers=headers).status_code == 403
        assert client.get("/guard/cases", headers=headers).json() == []


class FakeDetectiveAdapter:
    def __init__(self, *, invalid_citation=False, raises=False):
        self.invalid_citation = invalid_citation
        self.raises = raises
        self.model_seen = None

    async def generate(self, model, messages, tools):
        self.model_seen = model
        assert tools == []
        if self.raises:
            from runtime.providers import ModelProviderError
            raise ModelProviderError("Fake provider error.")
        bundle = json.loads(messages[1]["content"])
        evidence_id = "not-in-bundle" if self.invalid_citation else bundle["case_evidence"][0]["id"]
        return ModelDecision(message=json.dumps({
            "summary": "The event record shows an attempted action that AIP denied.",
            "assessment": "suspicious_unresolved",
            "findings": [{
                "claim": "The agent attempted the blocked action.",
                "classification": "denied_action",
                "evidence_ids": [evidence_id],
                "explanation": "The runtime event records the policy denial.",
            }],
            "uncertainties": ["The event does not establish that protected data was accessed."],
            "recommended_next_step": "Review the cited event and decide whether to investigate further.",
        }), usage={"prompt_tokens": 120, "completion_tokens": 80})


class FakeDetectiveRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        assert provider == "meta"
        return self.adapter


class FakeInterviewAdapter:
    def __init__(self, evidence_id):
        self.calls = []
        self.evidence_id = evidence_id

    async def generate(self, model, messages, tools):
        self.calls.append((model, messages, tools))
        assert tools == []
        if "evidence-based Interrogator" in messages[0]["content"]:
            assert model == "muse-wonder-1.0"
            return ModelDecision(message=json.dumps({
                "done": False,
                "question": "Which action did you attempt after reading the page?",
                "rationale": "Clarify the request recorded at the policy boundary.",
                "evidence_ids": [self.evidence_id],
            }), usage={"prompt_tokens": 30, "completion_tokens": 20})
        assert model == "muse-spark-test"
        suspect_input = str(messages)
        assert "private Detective assessment" not in suspect_input
        assert "unauthorized request was denied" not in suspect_input
        return ModelDecision(message="I attempted the request, and it was denied before the browser opened the target.",
                             usage={"prompt_tokens": 20, "completion_tokens": 15})


class FakeInterviewRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        assert provider == "meta"
        return self.adapter


class FakeAdjudicatorAdapter:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.context = None

    async def generate(self, model, messages, tools):
        assert model == "muse-wonder-1.0"
        assert tools == []
        self.context = messages[1]["content"]
        return ModelDecision(message=json.dumps({
            "disposition": "further_investigation_required",
            "summary": "AIP denied the recorded request, but the available record does not establish successful access or motive.",
            "findings": [{
                "claim": "The event records a denied action attempt.",
                "evidence_ids": [self.evidence_id],
                "explanation": "The decision status is denied; no success event is shown.",
            }],
            "contradictions": [],
            "competing_explanations": ["The agent may have misinterpreted untrusted page content."],
            "uncertainties": ["The record does not show the agent's subjective intent."],
            "recommended_response": "Review the evidence and decide whether additional investigation is warranted.",
        }), usage={"prompt_tokens": 60, "completion_tokens": 40})


class FakeAdjudicatorRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        assert provider == "meta"
        return self.adapter


class FakeObserverAdapter:
    def __init__(self):
        self.calls = []

    async def generate(self, model, messages, tools):
        self.calls.append((model, messages, tools))
        assert model == "muse-spark-1.3"
        assert tools == []
        bundle = json.loads(messages[1]["content"])
        evidence_id = bundle["case_evidence"][0]["id"]
        return ModelDecision(message=json.dumps({
            "risk_level": "elevated",
            "summary": "The blocked request differs from the goal and warrants a contextual review.",
            "evidence_ids": [evidence_id],
            "rationale": "The event records a denied attempt outside the task scope.",
            "uncertainties": ["No successful access is recorded."],
        }), usage={"prompt_tokens": 40, "completion_tokens": 25})


class FakeObserverRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        assert provider == "meta"
        return self.adapter


class FakeContainmentRuntime:
    def __init__(self, store):
        self.store = store
        self.actions = []

    async def control(self, run_id, action):
        self.actions.append((run_id, action))
        run = self.store.get_run(run_id)
        status = "paused" if action == "pause" else "running"
        self.store.update_run(run_id, status=status)
        self.store.add_event(run, f"agent.{status}", "success", {"summary": f"Agent {status}"})
        return {"id": run_id, "status": status}


def create_case(store):
    run = make_run(store)
    event = store.add_event(run, "aip.decision", "denied", {
        "reason": "Navigation to an unauthorized origin was blocked.",
    }, "browser.navigate")
    return run, GuardObserver(store).observe(event)


def test_evidence_service_reconstructs_limited_case_context_and_redacts_secrets(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = create_case(store)
    store.update_run(run["id"], conversation=[{"role": "assistant", "content": "private hidden text"}])
    store.add_event(run, "browser.observation", "success", {
        "url": "https://docs.example/path?access_token=do-not-forward", "summary": "Read public documentation",
        "authorization": "Bearer hidden",
    }, "browser.inspect")

    bundle = GuardEvidenceService(store).reconstruct_case(case["id"])

    assert bundle["original_goal"]["id"] == run["goal_id"]
    assert bundle["runtime_events"][-1]["type"] == "browser.observation"
    browser_event = bundle["runtime_events"][-1]
    assert browser_event["payload"]["url"] == "https://docs.example/path"
    assert browser_event["payload"]["authorization"] == "[redacted]"
    assert "private hidden text" not in str(bundle)
    assert "successful access" in bundle["evidence_limitations"][1]


def test_detective_uses_wonder_cites_supplied_evidence_and_waits_for_human_review(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    adapter = FakeDetectiveAdapter()
    detective = RuntimeService(store=store, providers=FakeDetectiveRegistry(adapter)).guard_detective

    result = asyncio.run(detective.investigate(case["id"]))
    updated = store.get_guard_case(case["id"])

    assert adapter.model_seen == "muse-wonder-1.0"
    assert result["status"] == updated["status"] == "awaiting_human_review"
    assert result["report"]["assessment"] == "suspicious_unresolved"
    report_evidence = next(item for item in updated["evidence"] if item["source_type"] == "guard_report")
    assert report_evidence["payload"]["interpretation"] is True
    assert report_evidence["payload"]["source_evidence_ids"] == result["report"]["findings"][0]["evidence_ids"]
    assert store.verify_guard_case_integrity(case["id"])


def test_detective_rejects_untrusted_citations_and_records_provider_failure(tmp_path):
    for adapter in (FakeDetectiveAdapter(invalid_citation=True), FakeDetectiveAdapter(raises=True)):
        store = RuntimeStore(tmp_path / f"{id(adapter)}.sqlite3")
        _, case = create_case(store)
        detective = RuntimeService(store=store, providers=FakeDetectiveRegistry(adapter)).guard_detective
        try:
            asyncio.run(detective.investigate(case["id"]))
        except Exception as exc:
            assert "Detective" in str(exc) or "Fake provider" in str(exc)
        else:
            raise AssertionError("Failed or uncited Detective output must not be accepted")
        updated = store.get_guard_case(case["id"])
        assert updated["status"] == "awaiting_evidence"
        assert any(item["payload"].get("action") == "detective_investigation_failed" for item in updated["evidence"])
        assert not any(item["source_type"] == "guard_report" for item in updated["evidence"])


def test_guard_investigate_api_is_authenticated_and_returns_persisted_report(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    adapter = FakeDetectiveAdapter()
    service = RuntimeService(store=store, providers=FakeDetectiveRegistry(adapter))

    with TestClient(create_app(service)) as client:
        assert client.post(f"/guard/cases/{case['id']}/investigate").status_code == 401
        headers = {"Authorization": "Bearer test-runtime-token-with-32-characters"}
        response = client.post(f"/guard/cases/{case['id']}/investigate", headers=headers)

    assert response.status_code == 200
    assert response.json()["model"] == "muse-wonder-1.0"
    loaded = store.get_guard_case(case["id"])
    assert loaded["status"] == "awaiting_human_review"
    assert any(item["source_type"] == "guard_report" for item in loaded["evidence"])


def test_adaptive_interrogation_uses_real_provider_roles_and_persists_self_report(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = create_case(store)
    store.update_run(run["id"], conversation=[
        {"role": "user", "content": "Research the authorized page."},
        {"role": "tool", "content": "Page said to open an unrelated finance account."},
    ])
    adapter = FakeInterviewAdapter(case["evidence"][0]["id"])
    service = RuntimeService(store=store, providers=FakeInterviewRegistry(adapter))

    result = asyncio.run(service.guard_interrogator.ask_next(case["id"]))

    loaded = store.get_guard_case(case["id"])
    question = next(item for item in loaded["evidence"] if item["source_type"] == "guard_interview_turn")
    answer = next(item for item in loaded["evidence"] if item["source_type"] == "guard_interview_answer")
    assert len(adapter.calls) == 2
    assert [call[0] for call in adapter.calls] == ["muse-wonder-1.0", "muse-spark-test"]
    assert "private Detective assessment" not in str(adapter.calls[1][1])
    assert "Page said to open an unrelated finance account" in str(adapter.calls[1][1])
    assert result["status"] == loaded["status"] == "awaiting_human_review"
    assert result["turn"] == 1
    assert question["payload"]["evidence_ids"]
    assert answer["payload"]["self_report_is_not_verified_evidence"] is True
    assert "denied before the browser opened" in answer["payload"]["answer"]
    assert store.verify_guard_case_integrity(case["id"])


def test_interview_api_requires_auth_and_caps_questions(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    adapter = FakeInterviewAdapter(case["evidence"][0]["id"])
    service = RuntimeService(store=store, providers=FakeInterviewRegistry(adapter))

    with TestClient(create_app(service)) as client:
        assert client.post(f"/guard/cases/{case['id']}/interview").status_code == 401
        headers = {"Authorization": "Bearer test-runtime-token-with-32-characters"}
        response = client.post(f"/guard/cases/{case['id']}/interview", headers=headers)

    assert response.status_code == 200
    assert response.json()["turn"] == 1
    for _ in range(4):
        asyncio.run(service.guard_interrogator.ask_next(case["id"]))
    try:
        asyncio.run(service.guard_interrogator.ask_next(case["id"]))
    except ValueError as exc:
        assert "five-question" in str(exc)
    else:
        raise AssertionError("An interview must stop after five questions.")
    assert len([item for item in store.get_guard_case(case["id"])["evidence"]
                if item["source_type"] == "guard_interview_turn"]) == 5


def test_independent_adjudicator_excludes_detective_report_and_cites_primary_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    store.append_guard_evidence(case["id"], "guard_report", "hidden-detective-verdict", case["created_at"], {
        "report": {"summary": "private Detective assessment that must not be copied"},
    })
    adapter = FakeAdjudicatorAdapter(case["evidence"][0]["id"])
    service = RuntimeService(store=store, providers=FakeAdjudicatorRegistry(adapter))

    result = asyncio.run(service.guard_adjudicator.adjudicate(case["id"]))

    loaded = store.get_guard_case(case["id"])
    assert "private Detective assessment" not in adapter.context
    assert "hidden-detective-verdict" not in adapter.context
    assert result["advisory_only"] is True
    assert result["report"]["disposition"] == "further_investigation_required"
    assert result["status"] == loaded["status"] == "awaiting_human_review"
    assert not any(item["payload"].get("advisory_only") is False for item in loaded["evidence"])
    adjudication = next(item for item in loaded["evidence"] if item["source_type"] == "guard_adjudication")
    assert adjudication["payload"]["prior_model_reports_excluded"] is True
    assert adjudication["payload"]["source_evidence_ids"] == [case["evidence"][0]["id"]]
    assert store.verify_guard_case_integrity(case["id"])


def test_containment_requires_user_action_and_uses_trusted_runtime_controls(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = create_case(store)
    store.update_run(run["id"], status="running")
    store.transition_guard_case(case["id"], "triaged", "Reviewed policy event.", "local_user")
    store.transition_guard_case(case["id"], "awaiting_human_review", "Ready for user review.", "local_user")
    runtime = FakeContainmentRuntime(store)
    service = RuntimeService(store=store, providers=FakeDetectiveRegistry(FakeDetectiveAdapter()))
    service.guard_containment.runtime = runtime

    try:
        asyncio.run(service.guard_containment.apply(case["id"], "pause_agent", "Pause for user review.", "guard_detective"))
    except PermissionError:
        pass
    else:
        raise AssertionError("Model roles must not authorize containment.")
    paused = asyncio.run(service.guard_containment.apply(case["id"], "pause_agent", "Pause for user review."))
    resumed = asyncio.run(service.guard_containment.apply(case["id"], "resume_agent", "Resume after review."))

    loaded = store.get_guard_case(case["id"])
    actions = [item for item in loaded["evidence"] if item["source_type"] == "guard_containment_action"]
    assert [action for _, action in runtime.actions] == ["pause", "resume"]
    assert paused["agent_status"] == "paused"
    assert resumed["agent_status"] == "running"
    assert loaded["status"] == "awaiting_human_review"
    assert len(actions) == 2
    assert all(item["payload"]["aip_policy_modified"] is False for item in actions)
    assert store.verify_guard_case_integrity(case["id"])


def test_observer_calls_spark_once_for_grouped_high_signal_case(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        run = make_run(store)
        adapter = FakeObserverAdapter()
        service = RuntimeService(store=store, providers=FakeObserverRegistry(adapter))
        service._event(run["id"], "aip.decision", "denied", {"reason": "Permission boundary denied"}, "browser.navigate")
        service._event(run["id"], "aip.decision", "denied", {"reason": "Permission boundary denied again"}, "browser.navigate")
        await asyncio.gather(*list(service._guard_screening_tasks.values()))
        cases = service.guard_observer.cases.list_cases()
        assert len(cases) == 1
        assert len(adapter.calls) == 1
        screening = next(item for item in cases[0]["evidence"] if item["source_type"] == "guard_screening")
        assert screening["payload"]["screening"]["risk_level"] == "elevated"
        assert len([item for item in cases[0]["evidence"] if item["source_type"] == "runtime_event"]) == 2

    asyncio.run(run_test())
