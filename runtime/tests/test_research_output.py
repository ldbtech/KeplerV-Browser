import json

import pytest

from runtime.models import ModelDecision, RunRequest, ToolCall
from runtime.scheduler import RuntimeService, _canonicalize_research_quotes, _parse_research_result
from runtime.store import RuntimeStore


SOURCE = "https://example.com/article"
PAGE_TEXT = (
    "This is the first exact phrase from the source.\n"
    "The second quote is valid source text.\n"
    "The third quote comes directly from the document."
)


def _finding(index: int, quote: str, **extra):
    return {
        "finding": f"Finding {index} summarizes a source passage accurately.",
        "quote": quote,
        "source_url": SOURCE,
        **extra,
    }


def test_parse_research_result_accepts_fenced_json_and_ignores_extra_fields():
    message = "Here are the findings:\n```json\n" + json.dumps({
        "summary": "A model-added summary is harmless.",
        "findings": [
            _finding(1, "This is the first exact phrase from the source.", confidence=0.9),
            _finding(2, "The second quote is valid source text."),
            _finding(3, "The third quote comes directly from the document."),
        ],
    }) + "\n```"

    output = _parse_research_result(message)
    output = _canonicalize_research_quotes(output, {SOURCE: PAGE_TEXT})

    assert len(output.findings) == 3
    assert output.findings[0].quote in PAGE_TEXT


def test_canonicalize_research_quotes_restores_source_whitespace():
    output = _parse_research_result(json.dumps({
        "findings": [
            _finding(1, "This is the first exact phrase from the source."),
            _finding(2, "The second quote is valid\nsource text."),
            _finding(3, "The third quote comes directly from the document."),
        ],
    }))

    canonical = _canonicalize_research_quotes(output, {SOURCE: PAGE_TEXT})

    assert canonical.findings[1].quote == "The second quote is valid source text."
    assert all(item.quote in PAGE_TEXT for item in canonical.findings)


def test_canonicalize_research_quotes_rejects_unverifiable_or_misattributed_quotes():
    output = _parse_research_result(json.dumps({
        "findings": [
            _finding(1, "This is the first exact phrase from the source."),
            _finding(2, "A completely invented source quote goes right here."),
            _finding(3, "The third quote comes directly from the document."),
        ],
    }))

    with pytest.raises(ValueError, match="source quote"):
        _canonicalize_research_quotes(output, {SOURCE: PAGE_TEXT})

    wrong_source = _parse_research_result(json.dumps({
        "findings": [
            _finding(1, "This is the first exact phrase from the source.", source_url="https://other.example/"),
            _finding(2, "The second quote is valid source text."),
            _finding(3, "The third quote comes directly from the document."),
        ],
    }))
    with pytest.raises(ValueError, match="source quote"):
        _canonicalize_research_quotes(wrong_source, {SOURCE: PAGE_TEXT})


class _FakeBrowser:
    def __init__(self, run_id, authorized_origin, data_dir):
        pass

    async def navigate(self, arguments):
        return {"ok": True, "url": SOURCE, "title": "Example article", "visible_text": PAGE_TEXT}

    async def extract_text(self, arguments):
        return {"ok": True, "url": SOURCE, "text": PAGE_TEXT}

    async def close(self):
        pass


class _MalformedFindingsModel:
    def __init__(self):
        self.turn = 0

    async def generate(self, model, messages, tools):
        self.turn += 1
        if self.turn <= 2:
            name = "browser_navigate" if self.turn == 1 else "browser_extract_text"
            arguments = {"url": SOURCE} if self.turn == 1 else {}
            call_id = f"call-{self.turn}"
            call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            return ModelDecision(
                tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                raw_assistant_message={"role": "assistant", "content": None, "tool_calls": [call]},
            )
        return ModelDecision(message="These are three findings, but not valid JSON.")


class _ProviderRegistry:
    active_provider = "test"
    active_model = "malformed-output-model"

    def __init__(self):
        self.adapter = _MalformedFindingsModel()

    def get(self, provider_id):
        return self.adapter


@pytest.mark.asyncio
async def test_invalid_model_format_falls_back_to_three_source_exact_excerpts(tmp_path):
    service = RuntimeService(
        store=RuntimeStore(tmp_path / "runtime.sqlite3"),
        providers=_ProviderRegistry(),
        browser_factory=_FakeBrowser,
        public_host_check=lambda _host: True,
    )
    agent = await service.create_run(RunRequest(
        goal_id="goal-format-fallback",
        goal_title="Read the example article",
        goal_description="Summarize the source article.",
        task="Return three source-backed findings.",
        source_url=SOURCE,
    ))
    await service._tasks[agent["id"]]

    run = service.store.get_run(agent["id"])
    evidence = service.store.list_evidence("goal-format-fallback")
    assert run["status"] == "completed", run["error"]
    assert run["result"]["source_excerpt_fallback"] is True
    assert len(evidence) == 3
    assert all(item["quote"] in PAGE_TEXT for item in evidence)
    assert all(item["source_url"] == SOURCE for item in evidence)
