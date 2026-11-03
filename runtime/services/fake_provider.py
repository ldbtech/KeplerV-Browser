"""Clearly-labeled fake mail provider for tests and demos.

Every payload carries :data:`FAKE_LABEL` so fixtures can never be mistaken
for live mail. All senders, addresses, and bodies are fictional.
"""

from __future__ import annotations

FAKE_LABEL = "FAKE — test fixture, not live mail"

_FIXTURES: tuple[dict, ...] = (
    {
        "id": "fake-msg-001",
        "thread_id": "fake-thread-enrollment",
        "from": "Registrar Office <registrar@example.test>",
        "to": ["demo.person@example.test"],
        "subject": "Fictional enrollment checklist for spring term",
        "date": "2026-08-14T09:00:00Z",
        "snippet": "A made-up checklist of enrollment steps for testing only.",
        "body": (
            "Hello,\n\nThis is a fictional message for KeplerV tests. Your made-up "
            "enrollment checklist has three invented steps and no real deadlines.\n\n"
            "— Fictional Registrar"
        ),
    },
    {
        "id": "fake-msg-002",
        "thread_id": "fake-thread-enrollment",
        "from": "Advisor Nova <advisor.nova@example.test>",
        "to": ["demo.person@example.test"],
        "subject": "Re: Fictional enrollment checklist for spring term",
        "date": "2026-08-14T13:30:00Z",
        "snippet": "A made-up follow-up about invented course planning.",
        "body": (
            "Hello,\n\nThis follow-up is also fictional. It mentions invented courses "
            "on world models and cognitive science for search tests.\n\n— Nova (fictional)"
        ),
    },
    {
        "id": "fake-msg-003",
        "thread_id": "fake-thread-library",
        "from": "Library Desk <library@example.test>",
        "to": ["demo.person@example.test"],
        "subject": "Fictional hold notice: imaginary atlas",
        "date": "2026-08-15T10:15:00Z",
        "snippet": "A made-up hold notice for an imaginary atlas.",
        "body": (
            "Hello,\n\nThe imaginary atlas you never requested is waiting at a "
            "fictional desk. Nothing here is real.\n\n— Fictional Library"
        ),
    },
)


def _labeled(message: dict) -> dict:
    labeled = dict(message)
    labeled["fake"] = True
    labeled["label"] = FAKE_LABEL
    labeled["subject"] = f"[{FAKE_LABEL}] {message['subject']}"
    return labeled


class FakeMailProvider:
    """In-memory fictional mailbox. Read-only; no send path exists."""

    provider_id = "fake-mail"

    def __init__(self) -> None:
        self._messages = [_labeled(message) for message in _FIXTURES]

    def list_messages(self, account_email: str, limit: int = 10) -> dict:
        self._check_account(account_email)
        bounded = max(1, min(limit, len(self._messages)))
        return {"label": FAKE_LABEL, "fake": True, "messages": [dict(message) for message in self._messages[:bounded]]}

    def get_message(self, account_email: str, message_id: str) -> dict:
        self._check_account(account_email)
        for message in self._messages:
            if message["id"] == message_id:
                return dict(message)
        raise KeyError(f"Unknown fake message '{message_id}'.")

    def search(self, account_email: str, query: str, limit: int = 10) -> dict:
        self._check_account(account_email)
        needle = query.strip().lower()
        if not needle:
            raise ValueError("Enter a search query.")
        matches = [
            dict(message)
            for message in self._messages
            if needle in message["subject"].lower() or needle in message["body"].lower() or needle in message["from"].lower()
        ]
        bounded = max(1, min(limit, len(matches))) if matches else 0
        return {"label": FAKE_LABEL, "fake": True, "query": query, "messages": matches[:bounded] if bounded else []}

    @staticmethod
    def _check_account(account_email: str) -> None:
        if "@" not in account_email:
            raise ValueError("Enter a valid fake account email.")
