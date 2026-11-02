"""Opt-in full live workflow: real Muse, real Chromium, and a public article."""

import asyncio
import os
from pathlib import Path

import pytest

from runtime.browser.controller import BrowserController
from runtime.models import RunRequest
from runtime.providers.registry import ProviderRegistry
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


SOURCE = "https://docs.python.org/3/library/asyncio.html"


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MODEL_API_KEY"), reason="set MODEL_API_KEY to run a live Muse workflow")
async def test_live_muse_research_creates_three_source_checked_findings(tmp_path: Path):
    service = RuntimeService(
        store=RuntimeStore(tmp_path / "live-muse.sqlite3"),
        providers=ProviderRegistry(),
        browser_factory=BrowserController,
    )
    agent = await service.create_run(RunRequest(
        goal_id="goal-live-muse-test",
        goal_title="Research Python asyncio",
        goal_description="Read the official Python asyncio documentation and summarize three key facts.",
        task="Research this public article, extract three findings, and cite exact supporting quotes.",
        source_url=SOURCE,
    ))
    await asyncio.wait_for(service._tasks[agent["id"]], timeout=240)

    run = service.store.get_run(agent["id"])
    events = service.store.list_events()
    evidence = service.store.list_evidence("goal-live-muse-test")
    assert run["status"] == "completed", run["error"]
    assert len(evidence) == 3
    assert any(event["type"] == "model.request.started" for event in events)
    assert any(event["type"] == "tool.execution.completed" and event["tool"] == "browser.navigate" for event in events)
    assert any(event["type"] == "tool.execution.completed" and event["tool"] == "browser.extract_text" for event in events)
