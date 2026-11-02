from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4
import httpx
from runtime.models import ModelDecision, ToolCall
from runtime.providers.base import ModelProviderError


class GeminiAdapter:
    def __init__(self, endpoint: str, api_key: str):
        self.endpoint, self.api_key = endpoint.rstrip("/"), api_key

    async def generate(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        system = "\n\n".join(m["content"] for m in messages if m.get("role") in {"system", "developer"} and isinstance(m.get("content"), str))
        contents: list[dict[str, Any]] = []; tool_names: dict[str, str] = {}
        for message in messages:
            role = message.get("role")
            if role in {"system", "developer"}: continue
            if role == "assistant" and message.get("tool_calls"):
                parts = []
                if message.get("content"): parts.append({"text": message["content"]})
                for call in message["tool_calls"]:
                    fn = call.get("function", {}); call_id = call.get("id", ""); tool_names[call_id] = fn.get("name", "")
                    try: args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError: args = {}
                    parts.append({"functionCall": {"id": call_id, "name": fn.get("name", ""), "args": args}})
                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                call_id = message.get("tool_call_id", "")
                try: result = json.loads(message.get("content", "{}"))
                except (TypeError, json.JSONDecodeError): result = {"result": message.get("content", "")}
                contents.append({"role": "user", "parts": [{"functionResponse": {"id": call_id, "name": tool_names.get(call_id, "unknown_tool"), "response": {"result": result}}}]})
            elif role in {"user", "assistant"}:
                contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": str(message.get("content", ""))}]})
        body: dict[str, Any] = {"contents": contents}
        if system: body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools: body["tools"] = [{"functionDeclarations": [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "parameters": t["function"]["parameters"]} for t in tools]}]
        try:
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                response = await client.post(f"{self.endpoint}/models/{model}:generateContent", headers={"x-goog-api-key": self.api_key}, json=body)
                response.raise_for_status(); data = response.json()
            candidate = data["candidates"][0]; blocks = candidate.get("content", {}).get("parts", []); calls, normalized, texts = [], [], []
            for block in blocks:
                if isinstance(block.get("text"), str): texts.append(block["text"])
                elif "functionCall" in block:
                    fn = block["functionCall"]; call_id = fn.get("id") or str(uuid4()); args = fn.get("args", {})
                    calls.append(ToolCall(id=call_id, name=fn["name"], arguments=args)); normalized.append({"id": call_id, "type": "function", "function": {"name": fn["name"], "arguments": json.dumps(args)}})
            usage = data.get("usageMetadata", {})
            raw = {"role": "assistant", "content": "\n".join(texts) or None, "tool_calls": normalized}
            return ModelDecision(message=raw["content"], tool_calls=calls, finish_reason=candidate.get("finishReason"), usage={"prompt_tokens": usage.get("promptTokenCount", 0), "completion_tokens": usage.get("candidatesTokenCount", 0)}, raw_assistant_message=raw)
        except httpx.HTTPStatusError as exc:
            raise ModelProviderError(f"Google Gemini returned HTTP {exc.response.status_code}. Check the key, model, and account access.") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ModelProviderError("Could not complete a Google Gemini request. Check the connection and try again.") from exc
