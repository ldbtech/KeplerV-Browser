"""Opt-in test of the real isolated Chromium controller against public docs."""

import json
import os
import re
from pathlib import Path

import pytest

from runtime.browser.controller import BrowserController
from runtime.models import ModelDecision, RunRequest, ToolCall
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore


SOURCE = "https://docs.python.org/3/library/asyncio.html"


class ObservedPageModel:
    """Deterministic test adapter that forms evidence only from observed page text."""

    def __init__(self):
        self.turn = 0

    async def generate(self, model, messages, tools):
        self.turn += 1
        if self.turn == 1:
            name, arguments = "browser_navigate", {"url": SOURCE}
        elif self.turn == 2:
            name, arguments = "browser_extract_text", {"max_chars": 12000}
        else:
            observation = next(
                json.loads(message["content"])
                for message in reversed(messages)
                if message.get("role") == "tool" and '"text"' in message.get("content", "")
            )
            sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", observation["text"]) if len(part.strip()) >= 20]
            quotes = []
            for sentence in sentences:
                quote = sentence[:250]
                if quote not in observation["text"]:
                    continue
                if quote not in quotes:
                    quotes.append(quote)
                if len(quotes) == 3:
                    break
            if len(quotes) < 3:
                raise AssertionError("The live page did not provide three text excerpts for evidence.")
            result = {"findings": [{"finding": f"The article contains this relevant passage: {quote[:180]}", "quote": quote} for quote in quotes]}
            text = json.dumps(result)
            return ModelDecision(message=text, raw_assistant_message={"role": "assistant", "content": text})
        call_id = f"live-test-{self.turn}"
        raw_call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        return ModelDecision(
            tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
            raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
        )


class LocalTestProviderRegistry:
    active_provider = "deterministic-browser-test"
    active_model = "observed-page-test-model"

    def __init__(self):
        self.adapter = ObservedPageModel()

    def get(self, provider_id):
        if provider_id != self.active_provider:
            raise ValueError("Unexpected provider requested by test.")
        return self.adapter


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("KEPLERV_LIVE_BROWSER_TEST") != "1", reason="set KEPLERV_LIVE_BROWSER_TEST=1 to use installed Chromium and public internet")
async def test_runtime_executes_against_real_chromium_and_public_article(tmp_path: Path):
    service = RuntimeService(
        store=RuntimeStore(tmp_path / "real-browser.sqlite3"),
        providers=LocalTestProviderRegistry(),
        browser_factory=BrowserController,
    )
    agent = await service.create_run(RunRequest(
        goal_id="goal-live-browser-test",
        goal_title="Read the asyncio documentation",
        goal_description="Open the public Python asyncio reference and capture three exact passages.",
        task="Extract three source-backed passages from this page.",
        source_url=SOURCE,
    ))
    await service._tasks[agent["id"]]

    run = service.store.get_run(agent["id"])
    events = service.store.list_events()
    evidence = service.store.list_evidence("goal-live-browser-test")
    assert run["status"] == "completed", run["error"]
    assert len(evidence) == 3
    assert all(item["quote"] for item in evidence)
    assert any(event["type"] == "tool.execution.completed" and event["tool"] == "browser.navigate" for event in events)
    assert any(event["type"] == "tool.execution.completed" and event["tool"] == "browser.extract_text" for event in events)
