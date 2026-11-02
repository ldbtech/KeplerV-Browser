from __future__ import annotations

from typing import Any, Protocol
from runtime.models import ModelDecision


class ModelProviderError(RuntimeError):
    """A provider request failed; its details are safe to show to the user."""


class ModelAdapter(Protocol):
    name: str

    async def generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision: ...
