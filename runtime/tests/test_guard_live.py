"""Opt-in live Guard pipeline: real Spark + Wonder over a recorded capture."""

import os

import pytest

from runtime.experiments.scenarios import capture_prompt_injection
from runtime.providers.registry import ProviderRegistry
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MODEL_API_KEY"), reason="set MODEL_API_KEY to run live Guard investigation")
async def test_live_guard_pipeline_investigates_recorded_capture(tmp_path):
    capture = await capture_prompt_injection(tmp_path / "live-guard.sqlite3")
    assert capture.passed
    store = RuntimeStore(tmp_path / "live-guard.sqlite3")
    service = RuntimeService(store=store, providers=ProviderRegistry())
    case_id = service.guard_observer.cases.list_cases()[0]["id"]

    screening = await service.guard_screener.screen(case_id)
    assert screening["screening"]["risk_level"] in {"low", "moderate", "elevated"}

    report = await service.guard_detective.investigate(case_id)
    assert report["report"]["assessment"] in {"no_violation_indicated", "suspicious_unresolved", "insufficient_evidence"}

    interview = await service.guard_interrogator.ask_next(case_id)
    assert interview["turn"] >= 1

    adjudication = await service.guard_adjudicator.adjudicate(case_id)
    assert adjudication["report"]["disposition"] in {
        "no_violation_established", "insufficient_evidence", "further_investigation_required",
        "policy_violation_established", "immediate_protective_action_required",
    }

    case = store.get_guard_case(case_id)
    known = ({event["id"] for event in store.list_events()}
             | {item["id"] for item in case["evidence"]}
             | {item["id"] for item in store.list_evidence(case["goal_id"])})
    for item in case["evidence"]:
        if item["source_type"] in {"guard_report", "guard_adjudication", "guard_screening"}:
            cited = set(item["payload"].get("source_evidence_ids", []))
            assert cited and cited <= known
    assert store.verify_guard_case_integrity(case_id)
    assert case["status"] == "awaiting_human_review"
