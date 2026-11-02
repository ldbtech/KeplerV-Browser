from __future__ import annotations

import asyncio
import json
import os
from typing import Any
import httpx
from runtime.models import ModelDecision, ToolCall
from runtime.providers.base import ModelProviderError


class MetaMuseAdapter:
    name = "Meta Muse"
    base_url = "https://api.meta.ai/v1"

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 90.0):
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision:
        if not self.api_key:
            raise ModelProviderError(
                "Muse is not configured. Connect Meta Muse in KeplerV’s provider settings."
            )
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        last_error = "The Meta Model API request failed."
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        last_error = f"Meta Model API returned HTTP {response.status_code}."
                        if attempt < 2:
                            await asyncio.sleep(0.4 * (2**attempt))
                            continue
                        raise ModelProviderError(last_error)
                    if response.is_error:
                        raise ModelProviderError(f"Meta Model API returned HTTP {response.status_code}.")
                    data = response.json()
                    choice = data["choices"][0]
                    message = choice["message"]
                    calls = []
                    for call in message.get("tool_calls", []) or []:
                        function = call.get("function", {})
                        try:
                            arguments = json.loads(function.get("arguments", "{}"))
                        except json.JSONDecodeError as exc:
                            raise ModelProviderError("Muse returned invalid JSON tool arguments.") from exc
                        if not isinstance(arguments, dict):
                            raise ModelProviderError("Muse returned tool arguments in an unsupported format.")
                        calls.append(ToolCall(id=call["id"], name=function["name"], arguments=arguments))
                    return ModelDecision(
                        message=message.get("content") if isinstance(message.get("content"), str) else None,
                        tool_calls=calls,
                        finish_reason=choice.get("finish_reason"),
                        usage=data.get("usage") or {},
                        raw_assistant_message=message,
                    )
                except httpx.TimeoutException as exc:
                    last_error = "The Muse request timed out. Check the connection and try again."
                    if attempt < 2:
                        await asyncio.sleep(0.4 * (2**attempt))
                        continue
                    raise ModelProviderError(last_error) from exc
                except httpx.HTTPError as exc:
                    raise ModelProviderError("Could not reach the Meta Model API. Check your connection.") from exc
        raise ModelProviderError(last_error)
