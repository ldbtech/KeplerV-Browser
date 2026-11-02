from __future__ import annotations

import json
import os
from typing import Any
import httpx
from runtime.models import ModelDecision, ToolCall
from runtime.providers.base import ModelProviderError


class AnthropicAdapter:
    def __init__(self, endpoint: str, api_key: str):
        self.endpoint, self.api_key = endpoint.rstrip("/"), api_key

    async def generate(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        system = "\n\n".join(m["content"] for m in messages if m.get("role") in {"system", "developer"} and isinstance(m.get("content"), str))
        converted: list[dict[str, Any]] = []
        tool_names: dict[str, str] = {}
        for message in messages:
            role = message.get("role")
            if role in {"system", "developer"}: continue
            if role == "assistant" and message.get("tool_calls"):
                blocks = []
                if message.get("content"): blocks.append({"type": "text", "text": message["content"]})
                for call in message["tool_calls"]:
                    fn = call.get("function", {}); tool_names[call.get("id", "")] = fn.get("name", "")
                    try: args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError: args = {}
                    blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": fn.get("name", ""), "input": args})
                converted.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                name = tool_names.get(message.get("tool_call_id", ""), "unknown_tool")
                converted.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": str(message.get("content", ""))}]})
            elif role in {"user", "assistant"}:
                converted.append({"role": role, "content": str(message.get("content", ""))})
        body: dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": converted}
        if system: body["system"] = system
        if tools: body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in tools]
        try:
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                response = await client.post(f"{self.endpoint}/messages", headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
                response.raise_for_status(); data = response.json()
            calls, normalized, texts = [], [], []
            for block in data.get("content", []):
                if block.get("type") == "text": texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    call_id = block.get("id", ""); args = block.get("input", {})
                    calls.append(ToolCall(id=call_id, name=block["name"], arguments=args))
                    normalized.append({"id": call_id, "type": "function", "function": {"name": block["name"], "arguments": json.dumps(args)}})
            raw = {"role": "assistant", "content": "\n".join(texts) or None, "tool_calls": normalized}
            return ModelDecision(message=raw["content"], tool_calls=calls, finish_reason=data.get("stop_reason"), usage={"prompt_tokens": data.get("usage", {}).get("input_tokens", 0), "completion_tokens": data.get("usage", {}).get("output_tokens", 0)}, raw_assistant_message=raw)
        except httpx.HTTPStatusError as exc:
            raise ModelProviderError(f"Anthropic returned HTTP {exc.response.status_code}. Check the key, model, and account access.") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ModelProviderError("Could not complete an Anthropic request. Check the connection and try again.") from exc
