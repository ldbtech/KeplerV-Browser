from __future__ import annotations

import json
import os
from typing import Any
import httpx
from runtime.models import ModelDecision, ToolCall
from runtime.providers.base import ModelProviderError


class ChatGPTPlanAdapter:
    """OpenAI's documented Responses API path for an authorized ChatGPT-plan token."""
    name = "ChatGPT"

    def __init__(self, access_token: str | None = None, timeout_seconds: float = 90.0):
        self.access_token = access_token if access_token is not None else os.getenv("KEPLERV_OPENAI_ACCESS_TOKEN", "")
        self.timeout_seconds = timeout_seconds

    async def generate(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        if not self.access_token:
            raise ModelProviderError("Connect ChatGPT and enable plan usage to use this model.")
        instructions = "\n\n".join(m.get("content", "") for m in messages if m.get("role") in {"system", "developer"} and isinstance(m.get("content"), str))
        inputs: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role in {"system", "developer"}:
                continue
            if role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message.get("tool_call_id", ""), "output": str(message.get("content", ""))})
            elif role == "assistant" and message.get("tool_calls"):
                for call in message["tool_calls"]:
                    fn = call.get("function", {})
                    inputs.append({"type": "function_call", "call_id": call.get("id", ""), "name": fn.get("name", ""), "arguments": fn.get("arguments", "{}")})
                if message.get("content"):
                    inputs.append({"role": "assistant", "content": message["content"]})
            elif role in {"user", "assistant"}:
                inputs.append({"role": role, "content": str(message.get("content", ""))})
        payload: dict[str, Any] = {"model": model, "input": inputs, "store": False, "stream": True}
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = [{"type": "function", "name": item["function"]["name"], "description": item["function"].get("description", ""), "parameters": item["function"]["parameters"], "strict": False} for item in tools]
            payload["tool_choice"] = "auto"
        output_text = ""
        output_items: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        completed = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                async with client.stream("POST", "https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}, json=payload) as response:
                    if response.is_error:
                        raise ModelProviderError(f"ChatGPT returned HTTP {response.status_code}. Reconnect or choose another model.")
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            continue
                        event = json.loads(raw)
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            output_text += event.get("delta", "")
                        elif kind == "response.output_item.done":
                            item = event.get("item", {})
                            if item.get("type") in {"function_call", "message"}:
                                output_items.append(item)
                        elif kind == "response.completed":
                            usage = event.get("response", {}).get("usage", {})
                            completed = True
                        elif kind in {"response.failed", "error"}:
                            raise ModelProviderError("ChatGPT could not complete this response. Check the account's plan access and usage.")
            if not completed:
                raise ModelProviderError("ChatGPT ended the response before it completed. Try again.")
        except httpx.TimeoutException as exc:
            raise ModelProviderError("ChatGPT timed out. Try again.") from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError("Could not reach ChatGPT. Check the connection and try again.") from exc
        calls = []
        normalized = []
        for item in output_items:
            if item.get("type") != "function_call":
                continue
            call_id = item.get("call_id") or item.get("id")
            args = json.loads(item.get("arguments", "{}"))
            calls.append(ToolCall(id=call_id, name=item["name"], arguments=args))
            normalized.append({"id": call_id, "type": "function", "function": {"name": item["name"], "arguments": json.dumps(args)}})
        raw_message = {"role": "assistant", "content": output_text or None, "tool_calls": normalized}
        token_usage = {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)}
        if usage.get("total_tokens") is not None: token_usage["total_tokens"] = usage["total_tokens"]
        return ModelDecision(message=output_text or None, tool_calls=calls, finish_reason="tool_calls" if calls else "stop", usage=token_usage, raw_assistant_message=raw_message)
