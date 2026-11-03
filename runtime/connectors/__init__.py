"""Mail connectors behind one read-only interface.

Gmail is the real read-only connector (official API shapes, minimal scopes).
Outlook is a skeleton behind the same interface. Neither connector fabricates
messages: when no account is connected they raise instead of returning data.
"""

from __future__ import annotations

from runtime.connectors.base import (
    AccountRef,
    AmbiguousAccountError,
    MailAccountStatusError,
    MailConnector,
    MailNotConnectedError,
    ResolvedAccount,
    normalize_message,
)
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.connectors.gmail import GmailConnector
from runtime.connectors.outlook import OUTLOOK_SKELETON_LABEL, OutlookConnector

__all__ = [
    "AccountRef",
    "AmbiguousAccountError",
    "FakeMailAdapter",
    "GmailConnector",
    "MailAccountStatusError",
    "MailConnector",
    "MailNotConnectedError",
    "OUTLOOK_SKELETON_LABEL",
    "OutlookConnector",
    "ResolvedAccount",
    "normalize_message",
]
