"""Mail connectors behind one read-only interface, plus gated senders.

Gmail and Outlook are real connectors (official API shapes, minimal scopes)
behind the shared read-only interface in :mod:`runtime.connectors.base`.
Transmission lives only in ``GmailSender``/``OutlookSender``, which the
scheduler calls after an AIP grant plus explicit human approval. No connector
fabricates messages: when no account is connected they raise instead of
returning data.
"""

from __future__ import annotations

from runtime.connectors.base import (
    LIVE_SEND_LABEL,
    AccountRef,
    AmbiguousAccountError,
    MailAccountStatusError,
    MailConnector,
    MailNotConnectedError,
    ResolvedAccount,
    normalize_message,
)
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.connectors.gmail import (
    GMAIL_API_BASE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailConnector,
    GmailSender,
    build_gmail_send_payload,
    parse_gmail_message,
)
from runtime.connectors.outlook import (
    GRAPH_API_BASE,
    OUTLOOK_READ_SCOPE,
    OUTLOOK_SEND_SCOPE,
    OutlookConnector,
    OutlookSender,
    build_outlook_send_payload,
    parse_outlook_message,
)

__all__ = [
    "AccountRef",
    "AmbiguousAccountError",
    "FakeMailAdapter",
    "GMAIL_API_BASE",
    "GMAIL_READONLY_SCOPE",
    "GMAIL_SEND_SCOPE",
    "GRAPH_API_BASE",
    "LIVE_SEND_LABEL",
    "MailAccountStatusError",
    "MailConnector",
    "MailNotConnectedError",
    "OUTLOOK_READ_SCOPE",
    "OUTLOOK_SEND_SCOPE",
    "OutlookConnector",
    "OutlookSender",
    "GmailConnector",
    "GmailSender",
    "ResolvedAccount",
    "build_gmail_send_payload",
    "build_outlook_send_payload",
    "normalize_message",
    "parse_gmail_message",
    "parse_outlook_message",
]
