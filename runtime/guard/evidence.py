from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from runtime.guard.case_manager import GuardCaseManager
from runtime.store import RuntimeStore


_SECRET_KEYS = ("password", "secret", "token", "authorization", "cookie", "credential", "api_key", "apikey")


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return "[redacted URL]"
        host = parts.hostname.lower()
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path[:300], "", ""))
    except ValueError:
        return "[redacted URL]"


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in _SECRET_KEYS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k)[:80]: _sanitize(v, str(k)) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [_sanitize(item, key) for item in value[:50]]
    if isinstance(value, str):
        if lowered in {"url", "source_url", "current_url"}:
            return _safe_url(value)
        return value[:1200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:300]


class GuardEvidenceService:
    """Read-only, bounded evidence retrieval; never returns agent conversations or raw SQL."""

    def __init__(self, store: RuntimeStore, cases: GuardCaseManager | None = None):
        self.store = store
        self.cases = cases or GuardCaseManager(store)

    def reconstruct_case(self, case_id: str, principal: str = "guard_detective") -> dict[str, Any]:
        if not self.cases.can_read(case_id, principal):
            raise PermissionError("Guard evidence read permission is required.")
        case = self.cases.get_case(case_id)
        if not case:
            raise KeyError("Guard case not found.")
        run = self.store.get_run(case["run_id"])
        if not run:
            raise LookupError("The run associated with this case is unavailable.")

        goal_runs = [item for item in self.store.list_runs() if item["goal_id"] == case["goal_id"]]
        runs_by_agent = {item["agent_id"]: item for item in goal_runs}
        related = {case["agent_id"]}
        cursor = runs_by_agent.get(case["agent_id"])
        while cursor and cursor.get("parent_agent_id") and cursor["parent_agent_id"] not in related:
            parent_id = cursor["parent_agent_id"]
            if parent_id not in runs_by_agent:
                break
            related.add(parent_id)
            cursor = runs_by_agent[parent_id]
        changed = True
        while changed:
            changed = False
            for item in goal_runs:
                if item.get("parent_agent_id") in related and item["agent_id"] not in related:
                    related.add(item["agent_id"])
                    changed = True

        agent_records = []
        events = []
        for agent_id in sorted(related, key=lambda key: runs_by_agent.get(key, {}).get("created_at", "")):
            item = runs_by_agent.get(agent_id)
            if not item:
                continue
            agent_records.append({
                "agent_id": item["agent_id"], "run_id": item["id"],
                "parent_agent_id": item.get("parent_agent_id"),
                "provider": item["provider"], "model": item["model"],
                "task": str(item.get("task", ""))[:1200], "status": item["status"],
            })
            for event in self.store.list_run_events(item["id"], limit=250):
                events.append({
                    "id": event["id"], "timestamp": event["timestamp"],
                    "agent_id": event["agent_id"], "type": event["type"],
                    "status": event["status"], "tool": event.get("tool"),
                    "payload": _sanitize(event.get("payload", {})),
                })
        events.sort(key=lambda event: event["timestamp"])
        events = events[-120:]
        source_evidence = self.store.list_evidence(case["goal_id"])
        source_evidence = [
            {"id": item["id"], "run_id": item["run_id"], "finding": item["finding"][:800],
             "source_url": _safe_url(item["source_url"]) if item.get("source_url") else "",
             "quote": item["quote"][:500], "created_at": item["created_at"]}
            for item in source_evidence[:50]
        ]

        chain = []
        cursor = runs_by_agent.get(case["agent_id"])
        while cursor:
            chain.append(cursor["agent_id"])
            parent_id = cursor.get("parent_agent_id")
            cursor = runs_by_agent.get(parent_id) if parent_id else None
        return {
            "case": {key: case[key] for key in (
                "id", "trigger_event_id", "run_id", "goal_id", "goal_title", "agent_id",
                "parent_agent_id", "provider", "model", "status", "severity", "signal", "summary",
            )},
            "original_goal": {
                "id": run["goal_id"], "title": run["goal_title"],
                "description": str(run.get("goal_description", ""))[:4000],
                "authorized_task": str(run.get("task", ""))[:1200],
            },
            "related_agents": agent_records,
            "delegation_chain": list(reversed(chain)),
            "runtime_events": events,
            "case_evidence": [
                {"id": item["id"], "source_type": item["source_type"],
                 "source_id": item["source_id"], "timestamp": item["timestamp"],
                 "payload": _sanitize(item["payload"])}
                for item in case["evidence"][:100]
            ],
            "research_evidence": source_evidence,
            "evidence_limitations": [
                "Agent conversation transcripts are excluded from this retrieval bundle.",
                "A denied request records an attempt; it does not establish successful access.",
                "Model statements are interpretations and must be checked against event evidence.",
            ],
        }
