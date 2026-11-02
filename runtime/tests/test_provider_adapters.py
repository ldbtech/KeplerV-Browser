import json
import pytest

from runtime.providers.anthropic import AnthropicAdapter
from runtime.providers.chatgpt import ChatGPTPlanAdapter
from runtime.providers.google import GeminiAdapter
from runtime.providers.openai_compatible import OpenAICompatibleAdapter


class Response:
    status_code = 200
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return self.value


class FakeClient:
    response_value = {}
    last_request = None
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, url, **kwargs):
        FakeClient.last_request = (url, kwargs)
        return Response(FakeClient.response_value)


@pytest.mark.asyncio
async def test_openai_compatible_adapter_normalizes_tool_call(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    FakeClient.response_value = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-x", "function": {"name": "browser_inspect", "arguments": "{}"}}
    ]}, "finish_reason": "tool_calls"}], "usage": {"total_tokens": 5}}
    result = await OpenAICompatibleAdapter("https://api.deepseek.com", "key").generate("deepseek-flash", [], [{"type": "function"}])
    assert result.tool_calls[0].name == "browser_inspect"
    assert FakeClient.last_request[0] == "https://api.deepseek.com/chat/completions"
    assert FakeClient.last_request[1]["headers"]["Authorization"] == "Bearer key"


@pytest.mark.asyncio
async def test_anthropic_adapter_converts_tool_use(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    FakeClient.response_value = {"content": [{"type": "tool_use", "id": "tu-1", "name": "browser_inspect", "input": {}}], "stop_reason": "tool_use", "usage": {"input_tokens": 4, "output_tokens": 2}}
    result = await AnthropicAdapter("https://api.anthropic.com/v1", "key").generate("model-x", [{"role": "user", "content": "inspect"}], [{"type": "function", "function": {"name": "browser_inspect", "parameters": {"type": "object"}}}])
    assert result.tool_calls[0].id == "tu-1"
    assert FakeClient.last_request[0] == "https://api.anthropic.com/v1/messages"
    assert FakeClient.last_request[1]["headers"]["x-api-key"] == "key"


@pytest.mark.asyncio
async def test_gemini_adapter_converts_function_call(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    FakeClient.response_value = {"candidates": [{"content": {"parts": [{"functionCall": {"id": "fc-1", "name": "browser_inspect", "args": {}}}]}, "finishReason": "STOP"}], "usageMetadata": {"totalTokenCount": 7}}
    result = await GeminiAdapter("https://generativelanguage.googleapis.com/v1beta", "key").generate("gemini-model", [], [{"type": "function", "function": {"name": "browser_inspect", "parameters": {"type": "object"}}}])
    assert result.tool_calls[0].id == "fc-1"
    assert FakeClient.last_request[0].endswith("/models/gemini-model:generateContent")
    assert FakeClient.last_request[1]["headers"]["x-goog-api-key"] == "key"


class StreamResponse:
    status_code = 200
    is_error = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def aiter_lines(self):
        events = [
            {"type": "response.output_text.delta", "delta": "hello"},
            {"type": "response.completed", "response": {"usage": {"total_tokens": 3}}},
        ]
        for event in events: yield "data: " + json.dumps(event)


class FakeStreamingClient(FakeClient):
    def stream(self, *args, **kwargs):
        FakeClient.last_request = (args, kwargs)
        return StreamResponse()


@pytest.mark.asyncio
async def test_chatgpt_adapter_waits_for_completed_response(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeStreamingClient)
    result = await ChatGPTPlanAdapter("access-token").generate("model-x", [{"role": "user", "content": "hello"}], [])
    assert result.message == "hello"
    assert result.usage["total_tokens"] == 3
    assert FakeClient.last_request[0][1] == "https://api.openai.com/v1/responses"
    assert FakeClient.last_request[1]["json"]["store"] is False
