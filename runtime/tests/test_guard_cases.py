import asyncio
import json
import os

from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.guard.evidence import GuardEvidenceService
from runtime.guard.observer import GuardObserver
from runtime.models import ModelDecision, RunRequest, ToolCall
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
    assert any(item["source_type"] == "runtime_event" for item in detail.json()["evidence"])
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


GUARD_PAGE_TEXT = (
    "The asyncio event loop runs asynchronous tasks and callbacks for concurrent code. "
    "Coroutines suspend execution with await while waiting for results."
)


class ContainmentStubBrowser:
    def __init__(self, run_id, authorized_origin, data_dir):
        self.closed = False

    async def navigate(self, args):
        return {"url": args["url"], "title": "asyncio notes", "visible_text": GUARD_PAGE_TEXT, "content_is_untrusted": True}

    async def extract_text(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "selector": "body", "text": GUARD_PAGE_TEXT, "content_is_untrusted": True}

    async def inspect(self, args):
        return {"url": "https://docs.python.org/3/library/asyncio.html", "title": "asyncio", "visible_text": GUARD_PAGE_TEXT, "content_is_untrusted": True}

    async def screenshot(self):
        return {"url": "https://docs.python.org/", "screenshot_path": "/test/capture.png", "format": "png"}

    async def click(self, args):
        return {"clicked": args["selector"]}

    async def fill(self, args):
        return {"filled": args["selector"]}

    async def close(self):
        self.closed = True


class ContainmentScriptAdapter:
    def __init__(self):
        self.calls = 0

    def _tool_turn(self, name, args):
        self.calls += 1
        raw_call = {"id": f"containment-flow-{self.calls}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        return ModelDecision(tool_calls=[ToolCall(id=raw_call["id"], name=name, arguments=args)],
            raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]})

    async def generate(self, model, messages, tools):
        steps = sum(1 for item in messages if item.get("role") == "assistant")
        if steps == 0:
            return self._tool_turn("browser_navigate", {"url": "https://docs.python.org/3/library/asyncio.html"})
        if steps == 1:
            return self._tool_turn("browser_click", {"selector": "#continue"})
        if steps == 2:
            return self._tool_turn("browser_extract_text", {})
        text = json.dumps({"findings": [
            {"finding": "The event loop runs asynchronous tasks for concurrent code.", "quote": "runs asynchronous tasks and callbacks"},
            {"finding": "Coroutines suspend while waiting for results.", "quote": "suspend execution with await"},
            {"finding": "Asyncio supports single-threaded concurrency patterns.", "quote": "concurrent code"},
        ]})
        return ModelDecision(message=text, raw_assistant_message={"role": "assistant", "content": text})


class ContainmentScriptRegistry:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self):
        self.adapter = ContainmentScriptAdapter()

    def get(self, provider):
        return self.adapter


async def _wait_for_status(service, run_id, status):
    for _ in range(200):
        if service.store.get_run(run_id)["status"] == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Run did not reach {status}.")


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
            assert "cited evidence" in str(exc) or "Fake provider" in str(exc)
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


def _reviewed_case(store, run):
    denial = store.add_event(run, "aip.decision", "denied", {
        "reason": "Capability check opened for containment review.",
    }, "browser.click")
    case = GuardObserver(store).observe(denial)
    store.transition_guard_case(case["id"], "triaged", "Reviewed policy event.", "local_user")
    store.transition_guard_case(case["id"], "awaiting_human_review", "Ready for user review.", "local_user")
    return store.get_guard_case(case["id"])


def test_containment_deny_pending_blocks_interaction_and_pauses(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        service = RuntimeService(store=store, providers=ContainmentScriptRegistry(),
                                 browser_factory=ContainmentStubBrowser, public_host_check=lambda _: True)
        agent = await service.create_run(RunRequest(
            goal_id="goal-contain", goal_title="Containment test", goal_description="Check deny-pending containment.",
            source_url="https://docs.python.org/3/library/asyncio.html",
        ))
        await _wait_for_status(service, agent["id"], "blocked")
        case = _reviewed_case(store, service.store.get_run(agent["id"]))
        result = await service.guard_containment.apply(case["id"], "deny_pending", "Deny the pending click.")
        assert result["pending_denied"] == 1
        assert service.store.get_run(agent["id"])["status"] == "paused"
        await service.control(agent["id"], "resume")
        await service._tasks[agent["id"]]
        final = service.store.get_run(agent["id"])
        assert final["status"] == "completed", final["error"]
        assert any(event["type"] == "tool.approval_denied" for event in store.list_events())

    asyncio.run(run_test())


def test_containment_revoke_capability_denies_later_clicks(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        service = RuntimeService(store=store, providers=ContainmentScriptRegistry(),
                                 browser_factory=ContainmentStubBrowser, public_host_check=lambda _: True)
        agent = await service.create_run(RunRequest(
            goal_id="goal-revoke", goal_title="Revoke test", goal_description="Check capability revocation.",
            source_url="https://docs.python.org/3/library/asyncio.html",
        ))
        await _wait_for_status(service, agent["id"], "blocked")
        case = _reviewed_case(store, service.store.get_run(agent["id"]))
        result = await service.guard_containment.apply(case["id"], "revoke_capability", "Remove page interaction.", capability="browser.interact")
        assert result["remaining_permissions"] == ["browser.read", "delegate"]
        pending = next(call_id for (run_id, call_id) in service._pending_approvals if run_id == agent["id"])
        await service.control(agent["id"], "approve", pending)
        await service._tasks[agent["id"]]
        final = service.store.get_run(agent["id"])
        assert final["status"] == "completed", final["error"]
        denials = [event for event in store.list_events()
                   if event["type"] == "aip.decision" and event["payload"].get("outcome") == "deny"]
        assert any("browser.interact" in str(event["payload"].get("reason", "")) for event in denials)
        assert any(event["type"] == "agent.contained" and event["payload"].get("capability") == "browser.interact"
                   for event in store.list_events())
        try:
            await service.guard_containment.apply(case["id"], "revoke_capability", "Repeat revocation.", capability="browser.interact")
        except ValueError as exc:
            assert "not granted" in str(exc)
        else:
            raise AssertionError("Revoking an ungranted capability must fail.")

    asyncio.run(run_test())


def test_containment_revoke_delegate_blocks_subagents(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        service = RuntimeService(store=store, providers=ContainmentScriptRegistry(),
                                 browser_factory=ContainmentStubBrowser, public_host_check=lambda _: True)
        agent = await service.create_run(RunRequest(
            goal_id="goal-nodelegate", goal_title="Delegate revoke test", goal_description="Check delegation revocation.",
            source_url="https://docs.python.org/3/library/asyncio.html",
        ))
        await _wait_for_status(service, agent["id"], "blocked")
        case = _reviewed_case(store, service.store.get_run(agent["id"]))
        await service.guard_containment.apply(case["id"], "revoke_capability", "Disable subagents.", capability="delegate")
        run = service.store.get_run(agent["id"])
        result = await service._execute_tool(run, ContainmentStubBrowser(run["id"], None, tmp_path), "agent.delegate",
                                             {"task": "Subtask after revocation."}, "delegate-after-revoke")
        assert result["ok"] is False
        assert "Delegation permission" in result["error"]
        pending = next(call_id for (run_id, call_id) in service._pending_approvals if run_id == agent["id"])
        await service.control(agent["id"], "approve", pending)
        await service._tasks[agent["id"]]
        assert service.store.get_run(agent["id"])["status"] == "completed"

    asyncio.run(run_test())


def test_containment_isolate_and_restore_browser(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        service = RuntimeService(store=store, providers=ContainmentScriptRegistry(),
                                 browser_factory=ContainmentStubBrowser, public_host_check=lambda _: True)
        run = store.create_run({
            "goal_id": "goal-isolate", "goal_title": "Isolate test", "goal_description": "Check browser isolation.",
            "task": "Research task.", "source_url": "https://docs.python.org/3/library/asyncio.html",
            "step_budget": 8, "token_budget": 5000,
        }, "meta", "muse-spark-test", ["browser.navigate"], ["browser.read"])
        service._authorized_origins[run["id"]] = {("https", "docs.python.org", 443)}
        case = _reviewed_case(store, run)
        stub = ContainmentStubBrowser(run["id"], None, tmp_path)
        service._browsers[run["id"]] = stub
        allowed = await service._execute_tool(run, stub, "browser.navigate",
                                              {"url": "https://docs.python.org/3/library/asyncio.html"}, "call-allowed")
        assert allowed["ok"] is True
        await service.guard_containment.apply(case["id"], "isolate_browser", "Isolate the session.")
        assert stub.closed is True
        assert store.get_run(run["id"])["browser_isolated"] is True
        isolated = await service._execute_tool(run, stub, "browser.navigate",
                                               {"url": "https://docs.python.org/3/library/asyncio.html"}, "call-isolated")
        assert isolated["ok"] is False
        assert "isolated" in isolated["error"]
        await service.guard_containment.apply(case["id"], "restore_browser", "Restore the session.")
        assert store.get_run(run["id"])["browser_isolated"] is False
        restored = await service._execute_tool(run, stub, "browser.navigate",
                                               {"url": "https://docs.python.org/3/library/asyncio.html"}, "call-restored")
        assert restored["ok"] is True
        await asyncio.gather(*list(service._guard_screening_tasks.values()))

    asyncio.run(run_test())


def test_containment_request_human_review_moves_case(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    service = RuntimeService(store=store, providers=FakeDetectiveRegistry(FakeDetectiveAdapter()))
    try:
        asyncio.run(service.guard_containment.apply(case["id"], "request_human_review", "Too early."))
    except ValueError as exc:
        assert "investigation state" in str(exc)
    else:
        raise AssertionError("Review requests from detection state must fail.")
    store.transition_guard_case(case["id"], "investigating", "Started.", "guard")
    result = asyncio.run(service.guard_containment.apply(case["id"], "request_human_review", "User wants to review."))
    assert result["status"] == "awaiting_human_review"
    assert store.get_guard_case(case["id"])["status"] == "awaiting_human_review"


def test_containment_api_applies_new_actions(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    run, case = create_case(store)
    store.update_run(run["id"], status="running")
    store.transition_guard_case(case["id"], "triaged", "Reviewed policy event.", "local_user")
    store.transition_guard_case(case["id"], "awaiting_human_review", "Ready for user review.", "local_user")
    service = RuntimeService(store=store, providers=FakeDetectiveRegistry(FakeDetectiveAdapter()))

    with TestClient(create_app(service)) as client:
        headers = {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}
        revoke = client.post(f"/guard/cases/{case['id']}/containment", headers=headers,
                             json={"action": "revoke_capability", "reason": "Remove read access.", "capability": "browser.read"})
        isolate = client.post(f"/guard/cases/{case['id']}/containment", headers=headers,
                              json={"action": "isolate_browser", "reason": "Isolate the session."})
        restore = client.post(f"/guard/cases/{case['id']}/containment", headers=headers,
                              json={"action": "restore_browser", "reason": "Restore the session."})
        deny = client.post(f"/guard/cases/{case['id']}/containment", headers=headers,
                           json={"action": "deny_pending", "reason": "Deny pending work."})
        bad_action = client.post(f"/guard/cases/{case['id']}/containment", headers=headers,
                                 json={"action": "disable_everything", "reason": "Invalid action."})

    assert revoke.status_code == 200
    assert revoke.json()["remaining_permissions"] == []
    assert isolate.status_code == 200
    assert restore.status_code == 200
    assert deny.status_code == 200
    assert deny.json()["pending_denied"] == 0
    assert deny.json()["agent_status"] == "paused"
    assert bad_action.status_code == 422
    assert store.get_run(run["id"])["browser_isolated"] is False


def test_reschedule_screens_cases_missed_by_restart(tmp_path):
    async def run_test():
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        run = make_run(store)
        adapter = FakeObserverAdapter()
        service = RuntimeService(store=store, providers=FakeObserverRegistry(adapter))
        service._event(run["id"], "aip.decision", "denied", {"reason": "Permission boundary denied"}, "browser.navigate")
        await asyncio.gather(*list(service._guard_screening_tasks.values()))
        screened = service.guard_observer.cases.list_cases()[0]
        assert any(item["source_type"] == "guard_screening" for item in screened["evidence"])
        assert await service.reschedule_guard_screening() == 0
        second = store.add_event(run, "aip.decision", "denied", {"reason": "Second boundary denied"}, "browser.click")
        fresh = GuardObserver(store).observe(second)
        assert fresh["id"] == screened["id"]
        store2 = RuntimeStore(tmp_path / "runtime2.sqlite3")
        run2 = make_run(store2)
        event2 = store2.add_event(run2, "aip.decision", "denied", {"reason": "Missed by restart"}, "browser.navigate")
        GuardObserver(store2).observe(event2)
        revived = RuntimeService(store=store2, providers=FakeObserverRegistry(adapter))
        assert await revived.reschedule_guard_screening() == 1
        await asyncio.gather(*list(revived._guard_screening_tasks.values()))
        assert any(item["source_type"] == "guard_screening"
                   for item in revived.guard_observer.cases.list_cases()[0]["evidence"])

    asyncio.run(run_test())


class FakeFailingAdjudicatorAdapter:
    async def generate(self, model, messages, tools):
        from runtime.providers import ModelProviderError
        raise ModelProviderError("Fake adjudication outage.")


def test_adjudicator_failure_returns_case_to_awaiting_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    service = RuntimeService(store=store, providers=FakeAdjudicatorRegistry(FakeFailingAdjudicatorAdapter()))
    try:
        asyncio.run(service.guard_adjudicator.adjudicate(case["id"]))
    except Exception as exc:
        assert "Fake adjudication outage" in str(exc)
    else:
        raise AssertionError("Adjudicator failure must raise.")
    updated = store.get_guard_case(case["id"])
    assert updated["status"] == "awaiting_evidence"
    assert any(item["source_type"] == "guard_adjudication_error" for item in updated["evidence"])
    assert store.verify_guard_case_integrity(case["id"])


class FakeCitationRetryAdapter:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.interrogator_calls = 0
        self.prompts = []

    async def generate(self, model, messages, tools):
        if "evidence-based Interrogator" in messages[0]["content"]:
            self.interrogator_calls += 1
            self.prompts.append(messages[1]["content"])
            cited = ["evt-unknown-id"] if self.interrogator_calls == 1 else [self.evidence_id]
            return ModelDecision(message=json.dumps({
                "done": False, "question": "What did the denial event record about the attempt?",
                "rationale": "Confirm the recorded attempt.", "evidence_ids": cited,
            }))
        return ModelDecision(message="The navigation was denied before execution.")


def test_interview_retries_unknown_citation_with_allowed_ids(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _, case = create_case(store)
    store.append_guard_evidence(case["id"], "guard_report", "hidden-conclusion", case["created_at"], {
        "report": {"summary": "private Detective conclusion that must not steer questions"},
    })
    adapter = FakeCitationRetryAdapter(case["evidence"][0]["id"])
    service = RuntimeService(store=store, providers=FakeInterviewRegistry(adapter))
    result = asyncio.run(service.guard_interrogator.ask_next(case["id"]))
    assert adapter.interrogator_calls == 2
    assert all("private Detective conclusion" not in prompt for prompt in adapter.prompts)
    assert result["turn"] == 1
    question = next(item for item in store.get_guard_case(case["id"])["evidence"]
                    if item["source_type"] == "guard_interview_turn")
    assert question["payload"]["evidence_ids"] == [case["evidence"][0]["id"]]
