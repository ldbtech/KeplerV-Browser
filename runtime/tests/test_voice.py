import os
import shutil
import subprocess
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.models import ModelDecision
from runtime.scheduler import RuntimeService
from runtime.store import RuntimeStore
from runtime.voice.transcribe import MAX_AUDIO_BYTES, MetaVoiceTranscriber, VoiceTranscriptionError, validate_wav


def _wav_bytes(payload: bytes = b"\x00\x01" * 8000) -> bytes:
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[8:12] = b"WAVE"
    return bytes(header) + payload


def test_validate_wav_rejects_non_wav_and_oversize():
    validate_wav(_wav_bytes())
    with pytest.raises(VoiceTranscriptionError):
        validate_wav(b"not audio")
    with pytest.raises(VoiceTranscriptionError):
        validate_wav(b"RIFF" + b"\x00" * 40)
    with pytest.raises(VoiceTranscriptionError):
        validate_wav(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * MAX_AUDIO_BYTES)


def test_transcriber_normalizes_success_and_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/asr/transcribe"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"transcript": "  Hello Kepler.  ", "audioDurationMs": 1200})

    async def run_ok():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await MetaVoiceTranscriber(api_key="test-key", client=client).transcribe(_wav_bytes())
        assert result == {"transcript": "Hello Kepler.", "duration_ms": 1200}

    import asyncio
    asyncio.run(run_ok())

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async def run_failing():
        async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
            with pytest.raises(VoiceTranscriptionError):
                await MetaVoiceTranscriber(api_key="test-key", client=client).transcribe(_wav_bytes())

    asyncio.run(run_failing())

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"transcript": "   "})

    async def run_empty():
        async with httpx.AsyncClient(transport=httpx.MockTransport(empty)) as client:
            with pytest.raises(VoiceTranscriptionError):
                await MetaVoiceTranscriber(api_key="test-key", client=client).transcribe(_wav_bytes())

    asyncio.run(run_empty())


def test_transcriber_requires_api_key(monkeypatch):
    import asyncio
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    async def run_missing():
        with pytest.raises(VoiceTranscriptionError):
            await MetaVoiceTranscriber().transcribe(_wav_bytes())

    asyncio.run(run_missing())


def test_transcribe_api_requires_auth_and_validates_audio(tmp_path):
    service = RuntimeService(store=RuntimeStore(tmp_path / "voice.sqlite3"))
    with TestClient(create_app(service)) as client:
        assert client.post("/transcribe", content=b"nope").status_code == 401
        headers = {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}
        response = client.post("/transcribe", content=b"not audio", headers=headers)
        assert response.status_code == 400
        empty = client.post("/transcribe", content=b"", headers=headers)
        assert empty.status_code == 400


@pytest.mark.skipif(not os.getenv("MODEL_API_KEY") or not shutil.which("say") or not shutil.which("afconvert"),
                    reason="set MODEL_API_KEY on macOS with say/afconvert for live transcription")
def test_live_transcription_recognizes_synthetic_speech():
    import asyncio

    with tempfile.TemporaryDirectory() as directory:
        source = f"{directory}/probe.aiff"
        target = f"{directory}/probe.wav"
        subprocess.run(["say", "-v", "Samantha", "-o", source, "Hello Kepler, research world models"],
                       check=True, timeout=60)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", source, target],
                       check=True, timeout=60)
        with open(target, "rb") as handle:
            audio = handle.read()

    async def run_live():
        result = await MetaVoiceTranscriber().transcribe(audio)
        assert "kepler" in result["transcript"].casefold()
        assert "world models" in result["transcript"].casefold()

    asyncio.run(run_live())


def _intent_context(**overrides):
    from runtime.voice.intent import VoiceContext
    base = {
        "goals": [{"id": "goal-1", "title": "World models", "description": "Research world models."}],
        "agents": [{"id": "agent-1", "name": "Researcher", "goalId": "goal-1", "status": "working"}],
        "evidence": [
            {"id": "ev-1", "goalId": "goal-1", "finding": "First finding."},
            {"id": "ev-2", "goalId": "goal-1", "finding": "Second finding."},
        ],
        "resources": [],
        "approvals": [{"id": "ap-1", "goalId": "goal-1", "action": "browser.click"}],
        "cases": [],
        "selection": {"goalId": "goal-1"},
        "recent_events": [],
        "conversation": [],
    }
    base.update(overrides)
    return VoiceContext.model_validate(base)


class _ScriptedIntentAdapter:
    def __init__(self, intent):
        self.intent = intent
        self.calls = 0

    async def generate(self, model, messages, tools):
        import json as _json
        self.calls += 1
        return ModelDecision(message=_json.dumps(self.intent), usage={"total_tokens": 5})


class _ScriptedIntentProviders:
    active_provider = "meta"
    active_model = "muse-spark-test"

    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, provider):
        return self.adapter


def test_interpret_returns_validated_intent():
    import asyncio
    from runtime.voice.intent import interpret

    async def run_test():
        intent = await interpret(
            "show me the second finding",
            _intent_context(),
            _ScriptedIntentProviders(_ScriptedIntentAdapter({
                "type": "SHOW_EVIDENCE", "target_id": "ev-2", "args": {},
                "ambiguous": False, "needs_clarification": False,
                "permission": "read", "summary_for_speech": "Showing the second finding.",
            })),
        )
        assert intent.type == "SHOW_EVIDENCE"
        assert intent.target_id == "ev-2"

    asyncio.run(run_test())


def test_interpret_rejects_invented_target_ids():
    import asyncio
    from runtime.voice.intent import interpret

    async def run_test():
        intent = await interpret(
            "stop that agent",
            _intent_context(),
            _ScriptedIntentProviders(_ScriptedIntentAdapter({
                "type": "STOP_AGENT", "target_id": "agent-invented", "args": {},
                "ambiguous": False, "needs_clarification": False,
                "permission": "act", "summary_for_speech": "Stopping the agent.",
            })),
        )
        assert intent.needs_clarification is True
        assert intent.target_id is None

    asyncio.run(run_test())


def test_interpret_api_requires_auth_and_validates(tmp_path):
    import asyncio
    from runtime.voice.intent import VoiceContext

    service = RuntimeService(store=RuntimeStore(tmp_path / "intent.sqlite3"),
                             providers=_ScriptedIntentProviders(_ScriptedIntentAdapter({
                                 "type": "GET_STATUS", "target_id": "goal-1", "args": {},
                                 "ambiguous": False, "needs_clarification": False,
                                 "permission": "read", "summary_for_speech": "Checking status.",
                             })))
    with TestClient(create_app(service)) as client:
        assert client.post("/interpret", json={"transcript": "x", "context": {}}).status_code == 401
        headers = {"Authorization": f"Bearer {os.environ['KEPLERV_RUNTIME_TOKEN']}"}
        body = {"transcript": "how is my research going",
                "context": _intent_context().model_dump(mode="json")}
        response = client.post("/interpret", json=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["type"] == "GET_STATUS"


def test_live_interpret_classifies_research_request():
    import asyncio
    from runtime.providers.registry import ProviderRegistry
    from runtime.voice.intent import interpret

    if not os.getenv("MODEL_API_KEY"):
        pytest.skip("set MODEL_API_KEY for live intent classification")

    async def run_live():
        intent = await interpret(
            "Research the latest developments in world models for autonomous agents",
            _intent_context(), ProviderRegistry())
        assert intent.type == "CREATE_GOAL"
        assert intent.args.get("start_research") is True

    asyncio.run(run_live())
