from __future__ import annotations

import os
from runtime.providers.anthropic import AnthropicAdapter
from runtime.providers.chatgpt import ChatGPTPlanAdapter
from runtime.providers.google import GeminiAdapter
from runtime.providers.meta import MetaMuseAdapter
from runtime.providers.openai_compatible import OpenAICompatibleAdapter


class ProviderRegistry:
    """Maps provider IDs to adapters; agent orchestration depends on this interface."""

    def __init__(self) -> None:
        provider = os.getenv("KEPLERV_MODEL_PROVIDER", "meta")
        model = os.getenv("KEPLERV_MODEL_NAME", "muse-spark-1.3")
        if provider == "meta":
            self._providers = {"meta": MetaMuseAdapter()}
        elif provider == "chatgpt":
            self._providers = {"chatgpt": ChatGPTPlanAdapter()}
        elif provider == "anthropic":
            self._providers = {"anthropic": AnthropicAdapter(os.getenv("KEPLERV_MODEL_BASE_URL", "https://api.anthropic.com/v1"), os.getenv("MODEL_API_KEY", ""))}
        elif provider == "google":
            self._providers = {"google": GeminiAdapter(os.getenv("KEPLERV_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"), os.getenv("MODEL_API_KEY", ""))}
        else:
            self._providers = {provider: OpenAICompatibleAdapter()}
        self._active_provider = provider
        self._active_model = model

    def get(self, provider_id: str):
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ValueError(f"Provider '{provider_id}' has no runtime adapter yet.") from exc

    @property
    def active_provider(self) -> str:
        return self._active_provider

    @property
    def active_model(self) -> str:
        return self._active_model
