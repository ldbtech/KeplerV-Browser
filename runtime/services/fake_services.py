"""Clearly-labeled fake service adapters for M8 tests and demos.

Every payload carries :data:`FAKE_SERVICE_LABEL` so fixtures can never be
mistaken for live service data. All people, files, repos, events, and media
sessions are fictional. There is no live send/share/publish path here: the
outbox only records what the fake fixture "sent" so tests can assert that a
denied operation sent nothing.
"""

from __future__ import annotations

from itertools import count
from typing import Any

FAKE_SERVICE_LABEL = "FAKE — service test fixture, not a live service"

_FIXTURE_EVENTS: tuple[dict, ...] = (
    {
        "id": "fake-evt-001",
        "title": "Fictional study group",
        "start": "2026-09-01T15:00:00Z",
        "end": "2026-09-01T16:00:00Z",
        "attendees": ["demo.person@example.test"],
    },
    {
        "id": "fake-evt-002",
        "title": "Fictional library orientation",
        "start": "2026-09-03T10:00:00Z",
        "end": "2026-09-03T11:00:00Z",
        "attendees": ["demo.person@example.test", "advisor.nova@example.test"],
    },
)

_FIXTURE_FILES: tuple[dict, ...] = (
    {"id": "fake-file-001", "name": "fictional-essay-draft.txt", "mime": "text/plain", "size": 4820},
    {"id": "fake-file-002", "name": "imaginary-atlas-notes.txt", "mime": "text/plain", "size": 1290},
)

_FIXTURE_REPOS: tuple[dict, ...] = (
    {"full_name": "fictional/demo-notes", "description": "Made-up study notes for tests.", "stars": 3},
    {"full_name": "fictional/atlas-sketches", "description": "Made-up sketches for tests.", "stars": 1},
)


def _labeled(payload: dict[str, Any]) -> dict[str, Any]:
    return {"label": FAKE_SERVICE_LABEL, "fake": True, **payload}


class FakeServiceAdapter:
    """In-memory fictional services behind one async interface."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.shared: list[dict[str, Any]] = []
        self.published: list[dict[str, Any]] = []
        self._ids = count(1)

    # -- gmail.send (fake outbox only) ------------------------------------

    async def send_gmail(self, account_email: str, to: str, subject: str, body: str) -> dict[str, Any]:
        if "@" not in account_email or "@" not in to:
            raise ValueError("Enter valid sender and recipient addresses.")
        if not subject.strip() or not body.strip():
            raise ValueError("Subject and body are required.")
        record = {
            "id": f"fake-sent-{next(self._ids):03d}",
            "from": account_email.strip().lower(),
            "to": to.strip(),
            "subject": subject.strip()[:300],
            "body": body[:2000],
        }
        self.sent.append(record)
        return _labeled({"message_id": record["id"], "account": record["from"], "to": record["to"]})

    # -- calendar ----------------------------------------------------------

    async def calendar_read(self, limit: int = 10) -> dict[str, Any]:
        bounded = max(1, min(limit, len(_FIXTURE_EVENTS)))
        return _labeled({"events": [dict(event) for event in _FIXTURE_EVENTS[:bounded]]})

    async def calendar_create(self, title: str, start: str, end: str | None = None) -> dict[str, Any]:
        if not title.strip() or not start.strip():
            raise ValueError("Title and start time are required.")
        event = {"id": f"fake-evt-created-{next(self._ids):03d}", "title": title.strip()[:200],
                 "start": start.strip(), "end": (end or "").strip()}
        return _labeled({"event": event})

    async def calendar_delete(self, event_id: str) -> dict[str, Any]:
        if not event_id.strip():
            raise ValueError("Enter an event id.")
        return _labeled({"deleted": event_id.strip()})

    # -- drive -------------------------------------------------------------

    async def drive_read(self, query: str | None = None, limit: int = 10) -> dict[str, Any]:
        files = [dict(item) for item in _FIXTURE_FILES]
        if query and query.strip():
            needle = query.strip().lower()
            files = [item for item in files if needle in item["name"].lower()]
        return _labeled({"files": files[: max(1, min(limit, 25))]})

    async def drive_share(self, file_id: str, share_with: str) -> dict[str, Any]:
        if not file_id.strip() or "@" not in share_with:
            raise ValueError("Enter a file id and a share recipient.")
        record = {"file_id": file_id.strip(), "shared_with": share_with.strip()}
        self.shared.append(record)
        return _labeled({"share": record})

    # -- github ------------------------------------------------------------

    async def github_read(self, repo: str | None = None) -> dict[str, Any]:
        repos = [dict(item) for item in _FIXTURE_REPOS]
        if repo and repo.strip():
            repos = [item for item in repos if repo.strip().lower() in item["full_name"].lower()]
        return _labeled({"repos": repos})

    async def github_publish(self, repo: str, title: str, body: str = "") -> dict[str, Any]:
        if not repo.strip() or not title.strip():
            raise ValueError("Repo and title are required.")
        record = {"repo": repo.strip(), "title": title.strip()[:200], "body": body[:2000]}
        self.published.append(record)
        return _labeled({"issue": {**record, "number": len(self.published)}})

    # -- media -------------------------------------------------------------

    async def media_open(self, target: str, video_id: str | None = None) -> dict[str, Any]:
        if target not in {"local-video", "youtube", "netflix"}:
            raise ValueError("Unknown media target.")
        return _labeled({"session": {"target": target, "video_id": video_id or "", "state": "opened"}})

    async def media_control(self, action: str, target: str | None = None) -> dict[str, Any]:
        if action not in {"pause", "resume", "close"}:
            raise ValueError("Unknown media action.")
        return _labeled({"session": {"target": target or "", "state": action}})
