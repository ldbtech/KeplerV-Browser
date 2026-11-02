from __future__ import annotations

import json
import os
from typing import Any
import httpx
from runtime.models import ModelDecision, ToolCall
from runtime.providers.base import ModelProviderError


class OpenAICompatibleAdapter:
    """Chat Completions adapter for hosted vendors and local OpenAI-compatible servers."""
    def __init__(self, endpoint: str | None = None, api_key: str | None = None, timeout_seconds: float = 90.0):
        self.endpoint = (endpoint or os.getenv("KEPLERV_MODEL_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    async def generate(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.post(f"{self.endpoint}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                message = data["choices"][0]["message"]
                calls = []
                for call in message.get("tool_calls", []) or []:
                    function = call.get("function", {})
                    arguments = json.loads(function.get("arguments", "{}"))
                    if not isinstance(arguments, dict):
                        raise ModelProviderError("The model returned invalid tool arguments.")
                    calls.append(ToolCall(id=call["id"], name=function["name"], arguments=arguments))
                return ModelDecision(message=message.get("content") if isinstance(message.get("content"), str) else None,
                    tool_calls=calls, finish_reason=data["choices"][0].get("finish_reason"), usage=data.get("usage") or {}, raw_assistant_message=message)
        except httpx.HTTPStatusError as exc:
            raise ModelProviderError(f"Provider returned HTTP {exc.response.status_code}. Check the key, model, and endpoint.") from exc
        except httpx.TimeoutException as exc:
            raise ModelProviderError("The provider request timed out. Check the connection and try again.") from exc
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProviderError("Could not complete a request with this provider. Check its connection settings.") from exc
