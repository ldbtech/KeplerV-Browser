from __future__ import annotations

"""Shared validation for Guard model outputs, with one correction retry."""

import asyncio
import json
from typing import Any, Callable, TypeVar

from pydantic import ValidationError


T = TypeVar("T")


class ModelOutputError(RuntimeError):
    pass


def _decode_candidate(content: str) -> Any:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    value, _ = json.JSONDecoder().raw_decode(candidate)
    return value


async def generate_valid_json(adapter: Any, model: str, messages: list[dict[str, Any]],
                              validate: Callable[[Any], T], *, timeout: float,
                              attempts: int = 2) -> tuple[T, dict[str, Any]]:
    """Generate schema-valid JSON, retrying once with the validation error shown.

    Returns the validated object and the successful call's usage. Raises
    ModelOutputError after the final attempt without recording anything.
    """
    conversation = [dict(item) for item in messages]
    last_error = "no response was produced"
    for _ in range(max(1, attempts)):
        decision = await asyncio.wait_for(adapter.generate(model, conversation, tools=[]), timeout=timeout)
        if decision.message:
            try:
                return validate(_decode_candidate(decision.message)), decision.usage
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_error = str(exc)[:400]
            conversation = [*conversation,
                            {"role": "assistant", "content": decision.message},
                            {"role": "user", "content": (
                                f"Your previous response was invalid: {last_error} "
                                "Return ONLY one JSON object matching the required schema, with no other text.")}]
        else:
            last_error = "empty response"
            conversation = [*conversation, {"role": "user", "content": (
                "Your previous response was empty. Return ONLY one JSON object matching the required schema.")}]
    raise ModelOutputError(last_error)
