"""Outlook adapter skeleton behind the shared mail interface.

Every method raises a clearly-labeled error: this skeleton performs no live
calls and returns no fixtures, so it can never be mistaken for real mail.
"""

from __future__ import annotations

from typing import Any

from runtime.connectors.base import ResolvedAccount

OUTLOOK_SKELETON_LABEL = "OUTLOOK SKELETON — not implemented, fixture only"


class OutlookSkeletonError(NotImplementedError):
    """Raised for every Outlook call until the adapter is implemented."""


def _skeleton_error(operation: str) -> OutlookSkeletonError:
    return OutlookSkeletonError(f"{OUTLOOK_SKELETON_LABEL}: {operation} is not available.")


class OutlookConnector:
    """Skeleton Outlook adapter. Same interface, no live behavior yet."""

    provider_id = "outlook"

    async def list_messages(self, account: ResolvedAccount, limit: int = 10) -> dict[str, Any]:
        raise _skeleton_error("list_messages")

    async def search(self, account: ResolvedAccount, query: str, limit: int = 10) -> dict[str, Any]:
        raise _skeleton_error("search")

    async def get_message(self, account: ResolvedAccount, message_id: str) -> dict[str, Any]:
        raise _skeleton_error("get_message")

    async def get_thread(self, account: ResolvedAccount, thread_id: str) -> dict[str, Any]:
        raise _skeleton_error("get_thread")
