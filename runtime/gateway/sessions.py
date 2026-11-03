"""Gateway session lifecycle + per-session transcript store (Phase 6 M1+M2).

Sessions live in sqlite tables alongside the runtime store (same database
file), following the ``RuntimeStore`` connection pattern: short-lived
connections, WAL journal, RLock-guarded.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from runtime.models import utc_now

SESSION_OPEN = "open"
SESSION_CLOSED = "closed"
SESSION_CANCELLED = "cancelled"


class GatewaySessionStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS gateway_sessions (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                  external_agent_id TEXT NOT NULL, endpoint TEXT NOT NULL,
                  status TEXT NOT NULL, label TEXT NOT NULL,
                  max_messages INTEGER NOT NULL, max_tokens INTEGER NOT NULL,
                  max_cost_micros INTEGER NOT NULL,
                  messages_sent INTEGER NOT NULL DEFAULT 0,
                  messages_received INTEGER NOT NULL DEFAULT 0,
                  tokens_used INTEGER NOT NULL DEFAULT 0,
                  cost_micros INTEGER NOT NULL DEFAULT 0,
                  provenance TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_messages (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES gateway_sessions(id),
                  message_id TEXT NOT NULL, reply_to TEXT, direction TEXT NOT NULL,
                  sender TEXT NOT NULL, recipient TEXT NOT NULL, kind TEXT NOT NULL,
                  label TEXT NOT NULL, untrusted INTEGER NOT NULL DEFAULT 0,
                  payload TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0,
                  cost_micros INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL, UNIQUE(session_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS gateway_messages_session ON gateway_messages(session_id, created_at);
            """)

    # -- sessions ----------------------------------------------------------

    def open_session(
        self,
        *,
        run_id: str,
        agent_id: str,
        external_agent_id: str,
        endpoint: str,
        provenance: dict[str, Any],
        label: str = "live",
        max_messages: int = 50,
        max_tokens: int = 20000,
        max_cost_micros: int = 1_000_000,
    ) -> dict[str, Any]:
        session_id = f"gsess-{uuid4()}"
        now = utc_now()
        row = {
            "id": session_id, "run_id": run_id, "agent_id": agent_id,
            "external_agent_id": external_agent_id, "endpoint": endpoint,
            "status": SESSION_OPEN, "label": label,
            "max_messages": max_messages, "max_tokens": max_tokens,
            "max_cost_micros": max_cost_micros,
            "messages_sent": 0, "messages_received": 0,
            "tokens_used": 0, "cost_micros": 0,
            "provenance": json.dumps(provenance or {}, ensure_ascii=False),
            "created_at": now, "updated_at": now,
        }
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO gateway_sessions
              (id,run_id,agent_id,external_agent_id,endpoint,status,label,
               max_messages,max_tokens,max_cost_micros,messages_sent,messages_received,
               tokens_used,cost_micros,provenance,created_at,updated_at)
              VALUES (:id,:run_id,:agent_id,:external_agent_id,:endpoint,:status,:label,
               :max_messages,:max_tokens,:max_cost_micros,:messages_sent,:messages_received,
               :tokens_used,:cost_micros,:provenance,:created_at,:updated_at)""", row)
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM gateway_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["provenance"] = json.loads(item["provenance"])
        return item

    def list_sessions(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            if run_id:
                rows = db.execute("SELECT * FROM gateway_sessions WHERE run_id=? ORDER BY created_at DESC", (run_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM gateway_sessions ORDER BY created_at DESC").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["provenance"] = json.loads(item["provenance"])
            items.append(item)
        return items

    def close_session(self, session_id: str, status: str = SESSION_CLOSED) -> dict[str, Any] | None:
        if status not in {SESSION_CLOSED, SESSION_CANCELLED}:
            raise ValueError(f"Invalid terminal session status '{status}'.")
        with self._lock, self._connect() as db:
            db.execute("UPDATE gateway_sessions SET status=?,updated_at=? WHERE id=? AND status=?",
                       (status, utc_now(), session_id, SESSION_OPEN))
        return self.get_session(session_id)

    def add_usage(self, session_id: str, *, sent: int = 0, received: int = 0, tokens: int = 0, cost_micros: int = 0) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            db.execute("""UPDATE gateway_sessions SET messages_sent=messages_sent+?,messages_received=messages_received+?,
              tokens_used=tokens_used+?,cost_micros=cost_micros+?,updated_at=? WHERE id=?""",
                       (sent, received, tokens, cost_micros, utc_now(), session_id))
        return self.get_session(session_id)

    # -- transcript ----------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        *,
        message_id: str,
        direction: str,
        sender: str,
        recipient: str,
        kind: str,
        label: str,
        untrusted: bool,
        payload: dict[str, Any],
        reply_to: str | None = None,
        tokens: int = 0,
        cost_micros: int = 0,
    ) -> dict[str, Any]:
        item = {
            "id": f"gmsg-{uuid4()}", "session_id": session_id, "message_id": message_id,
            "reply_to": reply_to, "direction": direction, "sender": sender,
            "recipient": recipient, "kind": kind, "label": label,
            "untrusted": int(bool(untrusted)), "payload": json.dumps(payload or {}, ensure_ascii=False),
            "tokens": tokens, "cost_micros": cost_micros, "created_at": utc_now(),
        }
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO gateway_messages
              (id,session_id,message_id,reply_to,direction,sender,recipient,kind,label,untrusted,payload,tokens,cost_micros,created_at)
              VALUES (:id,:session_id,:message_id,:reply_to,:direction,:sender,:recipient,:kind,:label,:untrusted,:payload,:tokens,:cost_micros,:created_at)""", item)
        stored = dict(item)
        stored["untrusted"] = bool(stored["untrusted"])
        stored["payload"] = json.loads(stored["payload"])
        return stored

    def list_transcript(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM gateway_messages WHERE session_id=? ORDER BY created_at LIMIT ?", (session_id, limit)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["untrusted"] = bool(item["untrusted"])
            item["payload"] = json.loads(item["payload"])
            items.append(item)
        return items
