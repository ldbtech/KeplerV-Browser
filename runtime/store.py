from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from runtime.models import utc_now


class RuntimeStore:
    def __init__(self, db_path: str | Path | None = None):
        root = Path(os.getenv("KEPLERV_DATA_DIR", Path.home() / ".keplerv"))
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else root / "runtime.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, agent_id TEXT UNIQUE NOT NULL, goal_id TEXT NOT NULL,
                  parent_agent_id TEXT, goal_title TEXT NOT NULL, goal_description TEXT NOT NULL,
                  task TEXT NOT NULL, source_url TEXT NOT NULL, provider TEXT NOT NULL,
                  model TEXT NOT NULL, status TEXT NOT NULL, permissions TEXT NOT NULL,
                  tools TEXT NOT NULL, step_budget INTEGER NOT NULL, token_budget INTEGER NOT NULL,
                  steps INTEGER NOT NULL DEFAULT 0, tokens_used INTEGER NOT NULL DEFAULT 0,
                  browser_visited INTEGER NOT NULL DEFAULT 0, text_extracted INTEGER NOT NULL DEFAULT 0,
                  result TEXT, error TEXT, conversation TEXT NOT NULL DEFAULT '[]', current_url TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  run_id TEXT NOT NULL, agent_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                  type TEXT NOT NULL, status TEXT NOT NULL, tool TEXT,
                  payload TEXT NOT NULL, timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS evidence (
                  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                  run_id TEXT NOT NULL, finding TEXT NOT NULL, source_url TEXT NOT NULL,
                  quote TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "conversation" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN conversation TEXT NOT NULL DEFAULT '[]'")
            if "current_url" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN current_url TEXT")
            interrupted = db.execute("SELECT id,agent_id,goal_id FROM runs WHERE status IN ('created','idle','running','blocked')").fetchall()
            for row in interrupted:
                db.execute("UPDATE runs SET status='paused',error='KeplerV restarted; resume this run to continue.' WHERE id=?", (row["id"],))
                db.execute(
                    "INSERT INTO events (id,run_id,agent_id,goal_id,type,status,tool,payload,timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"evt-{uuid4()}", row["id"], row["agent_id"], row["goal_id"], "agent.recovered", "paused", None,
                     json.dumps({"summary": "KeplerV restarted; the agent is paused and can be resumed."}), utc_now()),
                )

    def create_run(self, values: dict[str, Any], provider: str, model: str, tools: list[str], permissions: list[str], parent_agent_id: str | None = None) -> dict[str, Any]:
        run_id, agent_id = f"run-{uuid4()}", f"agent-{uuid4()}"
        now = utc_now()
        row = {"id": run_id, "agent_id": agent_id, "parent_agent_id": parent_agent_id, **values, "provider": provider, "model": model,
               "status": "created", "permissions": permissions, "tools": tools,
               "steps": 0, "tokens_used": 0, "browser_visited": False, "text_extracted": False,
               "result": None, "error": None, "conversation": [], "current_url": None,
               "created_at": now, "updated_at": now}
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO runs (id,agent_id,parent_agent_id,goal_id,goal_title,goal_description,task,source_url,
              provider,model,status,permissions,tools,step_budget,token_budget,conversation,created_at,updated_at)
              VALUES (:id,:agent_id,:parent_agent_id,:goal_id,:goal_title,:goal_description,:task,:source_url,
              :provider,:model,:status,:permissions,:tools,:step_budget,:token_budget,:conversation,:created_at,:updated_at)""",
              {**row, "permissions": json.dumps(permissions), "tools": json.dumps(tools), "conversation": "[]"})
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [self._run_dict(row) for row in rows]

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["permissions"] = json.loads(item["permissions"])
        item["tools"] = json.loads(item["tools"])
        item["browser_visited"] = bool(item["browser_visited"])
        item["text_extracted"] = bool(item["text_extracted"])
        if item["result"]:
            item["result"] = json.loads(item["result"])
        item["conversation"] = json.loads(item["conversation"])
        return item

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"])
        if "conversation" in fields:
            fields["conversation"] = json.dumps(fields["conversation"], ensure_ascii=False)
        assignments = ",".join(f"{key}=?" for key in fields)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE runs SET {assignments} WHERE id=?", (*fields.values(), run_id))

    def add_event(self, run: dict[str, Any], event_type: str, status: str, payload: dict[str, Any] | None = None, tool: str | None = None) -> dict[str, Any]:
        event_id, timestamp = f"evt-{uuid4()}", utc_now()
        with self._lock, self._connect() as db:
            cursor = db.execute("INSERT INTO events (id,run_id,agent_id,goal_id,type,status,tool,payload,timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (event_id, run["id"], run["agent_id"], run["goal_id"], event_type, status, tool,
                 json.dumps(payload or {}, ensure_ascii=False), timestamp))
            sequence = cursor.lastrowid
        return {"id": event_id, "sequence": sequence, "run_id": run["id"], "agent_id": run["agent_id"],
                "goal_id": run["goal_id"], "type": event_type, "status": status, "tool": tool,
                "payload": payload or {}, "timestamp": timestamp}

    def list_events(self, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def add_evidence(self, run: dict[str, Any], finding: str, quote: str, source_url: str | None = None) -> dict[str, Any]:
        item = {"id": f"evidence-{uuid4()}", "goal_id": run["goal_id"], "agent_id": run["agent_id"],
                "run_id": run["id"], "finding": finding, "source_url": source_url or run["source_url"],
                "quote": quote, "created_at": utc_now()}
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)", tuple(item.values()))
        return item

    def list_evidence(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM evidence", ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at DESC"
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute(query, args).fetchall()]
