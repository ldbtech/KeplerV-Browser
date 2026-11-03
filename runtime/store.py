from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from contextlib import contextmanager
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
                  browser_isolated INTEGER NOT NULL DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS guard_cases (
                  id TEXT PRIMARY KEY, trigger_event_id TEXT UNIQUE NOT NULL,
                  run_id TEXT NOT NULL, goal_id TEXT NOT NULL, goal_title TEXT NOT NULL,
                  agent_id TEXT NOT NULL, parent_agent_id TEXT, provider TEXT NOT NULL,
                  model TEXT NOT NULL, status TEXT NOT NULL, severity TEXT NOT NULL,
                  signal TEXT NOT NULL, summary TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS guard_cases_goal_created ON guard_cases(goal_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS guard_evidence (
                  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES guard_cases(id),
                  sequence INTEGER NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
                  timestamp TEXT NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL,
                  integrity_hash TEXT NOT NULL, UNIQUE(case_id, sequence), UNIQUE(case_id, source_type, source_id)
                );
                CREATE TABLE IF NOT EXISTS guard_transitions (
                  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES guard_cases(id),
                  from_status TEXT NOT NULL, to_status TEXT NOT NULL, reason TEXT NOT NULL,
                  actor TEXT NOT NULL, timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guard_case_permissions (
                  case_id TEXT NOT NULL REFERENCES guard_cases(id), principal TEXT NOT NULL,
                  permission TEXT NOT NULL, granted_at TEXT NOT NULL,
                  PRIMARY KEY(case_id, principal, permission)
                );
                CREATE TRIGGER IF NOT EXISTS guard_evidence_no_update
                  BEFORE UPDATE ON guard_evidence BEGIN SELECT RAISE(ABORT, 'Guard evidence is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS guard_evidence_no_delete
                  BEFORE DELETE ON guard_evidence BEGIN SELECT RAISE(ABORT, 'Guard evidence is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS guard_transitions_no_update
                  BEFORE UPDATE ON guard_transitions BEGIN SELECT RAISE(ABORT, 'Guard transitions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS guard_transitions_no_delete
                  BEFORE DELETE ON guard_transitions BEGIN SELECT RAISE(ABORT, 'Guard transitions are append-only'); END;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "conversation" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN conversation TEXT NOT NULL DEFAULT '[]'")
            if "current_url" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN current_url TEXT")
            if "browser_isolated" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN browser_isolated INTEGER NOT NULL DEFAULT 0")
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
        values = {**values, "source_url": values.get("source_url") or ""}
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
        item["browser_isolated"] = bool(item["browser_isolated"])
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

    def revoke_run_capability(self, run_id: str, capability: str) -> list[str] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        remaining = [item for item in run["permissions"] if item != capability]
        with self._lock, self._connect() as db:
            db.execute("UPDATE runs SET permissions=?,updated_at=? WHERE id=?",
                       (json.dumps(remaining), utc_now(), run_id))
        return remaining

    def set_browser_isolated(self, run_id: str, isolated: bool) -> None:
        self.update_run(run_id, browser_isolated=int(isolated))

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

    def list_run_events(self, run_id: str, limit: int = 250) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence LIMIT ?", (run_id, limit)).fetchall()
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

    @staticmethod
    def _guard_case_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def create_guard_case(self, values: dict[str, Any]) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock, self._connect() as db:
            cursor = db.execute("""INSERT OR IGNORE INTO guard_cases
              (id,trigger_event_id,run_id,goal_id,goal_title,agent_id,parent_agent_id,provider,model,status,severity,signal,summary,created_at,updated_at)
              VALUES (:id,:trigger_event_id,:run_id,:goal_id,:goal_title,:agent_id,:parent_agent_id,:provider,:model,:status,:severity,:signal,:summary,:created_at,:updated_at)""",
              {**values, "created_at": now, "updated_at": now})
            if cursor.rowcount == 0:
                return None
            db.executemany("INSERT OR IGNORE INTO guard_case_permissions (case_id,principal,permission,granted_at) VALUES (?,?,?,?)",
                           [(values["id"], "local_user", "read", now), (values["id"], "guard_detective", "read", now)])
            row = db.execute("SELECT * FROM guard_cases WHERE id=?", (values["id"],)).fetchone()
        return self._guard_case_dict(row)

    def get_guard_case_for_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM guard_cases WHERE trigger_event_id=?", (event_id,)).fetchone()
        return self.get_guard_case(row["id"]) if row else None

    def get_active_guard_case_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("""SELECT id FROM guard_cases WHERE run_id=?
              AND status NOT IN ('closed','inconclusive') ORDER BY created_at DESC LIMIT 1""", (run_id,)).fetchone()
        return self.get_guard_case(row["id"]) if row else None

    def append_guard_evidence(self, case_id: str, source_type: str, source_id: str,
                              timestamp: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            existing = db.execute("SELECT * FROM guard_evidence WHERE case_id=? AND source_type=? AND source_id=?",
                                  (case_id, source_type, source_id)).fetchone()
            if existing:
                return self._guard_evidence_dict(existing)
            previous = db.execute("SELECT sequence,integrity_hash FROM guard_evidence WHERE case_id=? ORDER BY sequence DESC LIMIT 1", (case_id,)).fetchone()
            sequence = (previous["sequence"] + 1) if previous else 1
            previous_hash = previous["integrity_hash"] if previous else "0" * 64
            evidence_id = f"guard-evidence-{uuid4()}"
            canonical = json.dumps({"id": evidence_id, "case_id": case_id, "sequence": sequence,
                "source_type": source_type, "source_id": source_id, "timestamp": timestamp,
                "payload": json.loads(payload_json), "previous_hash": previous_hash},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            integrity_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            db.execute("""INSERT INTO guard_evidence
              (id,case_id,sequence,source_type,source_id,timestamp,payload,previous_hash,integrity_hash)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              (evidence_id, case_id, sequence, source_type, source_id, timestamp, payload_json, previous_hash, integrity_hash))
            row = db.execute("SELECT * FROM guard_evidence WHERE id=?", (evidence_id,)).fetchone()
        return self._guard_evidence_dict(row)

    @staticmethod
    def _guard_evidence_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item.pop("sequence", None)
        item["payload"] = json.loads(item["payload"])
        return item

    def list_guard_cases(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM guard_cases", ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at DESC"
        with self._lock, self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._guard_case_dict(row) for row in rows]

    def list_permitted_guard_cases(self, principal: str, permission: str = "read") -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT c.* FROM guard_cases c
              JOIN guard_case_permissions p ON p.case_id=c.id
              WHERE p.principal=? AND p.permission=? ORDER BY c.created_at DESC""",
              (principal, permission)).fetchall()
        return [self._guard_case_dict(row) for row in rows]

    def has_guard_case_permission(self, case_id: str, principal: str, permission: str = "read") -> bool:
        with self._lock, self._connect() as db:
            return db.execute("SELECT 1 FROM guard_case_permissions WHERE case_id=? AND principal=? AND permission=?",
                              (case_id, principal, permission)).fetchone() is not None

    def grant_guard_case_permission(self, case_id: str, principal: str, permission: str = "read") -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR IGNORE INTO guard_case_permissions (case_id,principal,permission,granted_at) VALUES (?,?,?,?)",
                       (case_id, principal, permission, utc_now()))

    def revoke_guard_case_permission(self, case_id: str, principal: str, permission: str = "read") -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM guard_case_permissions WHERE case_id=? AND principal=? AND permission=?",
                       (case_id, principal, permission))

    def get_guard_case(self, case_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM guard_cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                return None
            case = self._guard_case_dict(row)
            evidence = db.execute("SELECT * FROM guard_evidence WHERE case_id=? ORDER BY sequence", (case_id,)).fetchall()
            transitions = db.execute("SELECT * FROM guard_transitions WHERE case_id=? ORDER BY timestamp,id", (case_id,)).fetchall()
        case["evidence"] = [self._guard_evidence_dict(item) for item in evidence]
        case["transitions"] = [dict(item) for item in transitions]
        return case

    def transition_guard_case(self, case_id: str, status: str, reason: str, actor: str = "guard") -> dict[str, Any] | None:
        allowed = {
            "detected": {"triaged", "investigating", "inconclusive", "closed"},
            "triaged": {"investigating", "awaiting_human_review", "inconclusive", "closed"},
            "investigating": {"interviewing", "evaluating", "awaiting_evidence", "awaiting_human_review", "inconclusive", "closed"},
            "interviewing": {"evaluating", "awaiting_evidence", "awaiting_human_review", "inconclusive"},
            "evaluating": {"awaiting_evidence", "awaiting_human_review", "containment_requested", "inconclusive", "closed"},
            "awaiting_evidence": {"investigating", "interviewing", "evaluating", "inconclusive", "closed"},
            "awaiting_human_review": {"investigating", "interviewing", "containment_requested", "closed", "reopened"},
            "containment_requested": {"awaiting_human_review", "closed", "reopened"},
            "inconclusive": {"reopened", "closed"}, "closed": {"reopened"}, "reopened": {"investigating", "closed"},
        }
        with self._lock, self._connect() as db:
            row = db.execute("SELECT status FROM guard_cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                return None
            previous = row["status"]
            if status not in allowed.get(previous, set()):
                raise ValueError(f"Invalid Guard case transition: {previous} → {status}.")
            now = utc_now()
            db.execute("UPDATE guard_cases SET status=?,updated_at=? WHERE id=?", (status, now, case_id))
            db.execute("INSERT INTO guard_transitions (id,case_id,from_status,to_status,reason,actor,timestamp) VALUES (?,?,?,?,?,?,?)",
                       (f"guard-transition-{uuid4()}", case_id, previous, status, reason, actor, now))
        return self.get_guard_case(case_id)

    def verify_guard_case_integrity(self, case_id: str) -> bool:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM guard_evidence WHERE case_id=? ORDER BY sequence", (case_id,)).fetchall()
        previous_hash = "0" * 64
        for expected_sequence, row in enumerate(rows, start=1):
            if row["sequence"] != expected_sequence or row["previous_hash"] != previous_hash:
                return False
            canonical = json.dumps({"id": row["id"], "case_id": row["case_id"], "sequence": row["sequence"],
                "source_type": row["source_type"], "source_id": row["source_id"], "timestamp": row["timestamp"],
                "payload": json.loads(row["payload"]), "previous_hash": row["previous_hash"]},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual != row["integrity_hash"]:
                return False
            previous_hash = actual
        return bool(rows) and self.get_guard_case(case_id) is not None
