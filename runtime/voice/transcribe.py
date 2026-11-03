from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

MAX_AUDIO_BYTES = 8 * 1024 * 1024


class VoiceTranscriptionError(RuntimeError):
    pass


class Transcriber(Protocol):
    async def transcribe(self, audio_wav: bytes) -> dict[str, Any]:
        """Return {"transcript": str, "duration_ms": int} for 16-bit WAV audio."""
        ...


def validate_wav(audio: bytes) -> None:
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceTranscriptionError("Voice recording is too long; keep requests under a few minutes.")
    if len(audio) < 44 or audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise VoiceTranscriptionError("Voice recording must be 16-bit WAV audio.")


class MetaVoiceTranscriber:
    """Meta muse-voice-transcribe-1.0 file transcription."""

    name = "Meta Muse Voice"
    model = "muse-voice-transcribe-1.0"
    endpoint = "https://api.meta.ai/v1/asr/transcribe"

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 90.0,
                 client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def transcribe(self, audio_wav: bytes) -> dict[str, Any]:
        validate_wav(audio_wav)
        if not self.api_key:
            raise VoiceTranscriptionError(
                "Voice transcription is not configured. Connect Meta Muse in KeplerV's provider settings."
            )
        request_part = json.dumps({"model": self.model, "audioEncoding": "WAV"})
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"request": (None, request_part, "application/json"),
                 "audio": ("recording.wav", audio_wav, "audio/wav")}
        try:
            if self._client is not None:
                response = await self._client.post(self.endpoint, headers=headers, files=files)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(self.endpoint, headers=headers, files=files)
        except httpx.TimeoutException as exc:
            raise VoiceTranscriptionError("Voice transcription timed out. Try a shorter recording.") from exc
        except httpx.HTTPError as exc:
            raise VoiceTranscriptionError("Could not reach the transcription service. Check your connection.") from exc
        if response.is_error:
            raise VoiceTranscriptionError(
                f"Voice transcription returned HTTP {response.status_code}. Try recording again.")
        try:
            data = response.json()
            transcript = str(data.get("transcript", "") or "").strip()
        except ValueError as exc:
            raise VoiceTranscriptionError("Voice transcription returned an unreadable response.") from exc
        if not transcript:
            raise VoiceTranscriptionError("No speech was recognized. Try speaking closer to the microphone.")
        duration = data.get("audioDurationMs", 0)
        return {"transcript": transcript, "duration_ms": int(duration or 0)}
