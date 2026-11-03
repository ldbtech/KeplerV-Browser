"""Fake-mail adapter: fictional fixtures behind the shared interface.

Every payload carries the fake label; the adapter is read-only like the
real connectors (no send path exists).
"""

from __future__ import annotations

from typing import Any

from runtime.connectors.base import ResolvedAccount
from runtime.services.fake_provider import FAKE_LABEL, FakeMailProvider


class FakeMailAdapter:
    """MailConnector-shaped wrapper over the fictional FakeMailProvider."""

    provider_id = "fake-mail"

    def __init__(self, provider: FakeMailProvider | None = None) -> None:
        self._provider = provider or FakeMailProvider()

    async def list_messages(self, account: ResolvedAccount, limit: int = 10) -> dict[str, Any]:
        payload = self._provider.list_messages(account.email, limit)
        return {"provider": "fake-mail", "account": account.email, **payload}

    async def search(self, account: ResolvedAccount, query: str, limit: int = 10) -> dict[str, Any]:
        payload = self._provider.search(account.email, query, limit)
        return {"provider": "fake-mail", "account": account.email, **payload}

    async def get_message(self, account: ResolvedAccount, message_id: str) -> dict[str, Any]:
        message = self._provider.get_message(account.email, message_id)
        adapted = dict(message)
        adapted["provider"] = "fake-mail"
        adapted["thread_id"] = message.get("thread_id", "")
        return adapted

    async def get_thread(self, account: ResolvedAccount, thread_id: str) -> dict[str, Any]:
        needle = thread_id.strip()
        if not needle:
            raise ValueError("Enter a thread id.")
        listing = self._provider.list_messages(account.email, limit=100)
        parts = [message for message in listing["messages"] if message.get("thread_id") == needle]
        if not parts:
            raise KeyError(f"Unknown fake thread '{thread_id}'.")
        return {
            "provider": "fake-mail",
            "account": account.email,
            "label": FAKE_LABEL,
            "fake": True,
            "thread_id": needle,
            "messages": parts,
        }
