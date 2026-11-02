import pytest

from runtime.models import ModelDecision
from runtime.providers.base import ModelProviderError
from runtime.providers.meta import MetaMuseAdapter


class Response:
    status_code = 200
    is_error = False

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "browser_navigate", "arguments": '{"url":"https://docs.python.org/"}'}}
        ]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38}}


class FakeAsyncClient:
    last_request = None

    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, url, **kwargs):
        self.last_request = (url, kwargs)
        FakeAsyncClient.last_request = self.last_request
        return Response()


@pytest.mark.asyncio
async def test_meta_adapter_normalizes_tool_calls_and_usage(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = await MetaMuseAdapter(api_key="server-only-key").generate("muse-spark-1.3", [{"role":"user","content":"research"}], [{"type":"function"}])
    assert isinstance(result, ModelDecision)
    assert result.tool_calls[0].name == "browser_navigate"
    assert result.tool_calls[0].arguments["url"] == "https://docs.python.org/"
    assert result.usage["total_tokens"] == 38
    assert FakeAsyncClient.last_request[0] == "https://api.meta.ai/v1/chat/completions"
    assert FakeAsyncClient.last_request[1]["headers"]["Authorization"] == "Bearer server-only-key"


@pytest.mark.asyncio
async def test_meta_adapter_never_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ModelProviderError, match="not configured"):
        await MetaMuseAdapter().generate("muse-spark-1.3", [], [])
