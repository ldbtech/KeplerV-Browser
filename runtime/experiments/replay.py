"""Labeled offline replay: verify and step through a recorded run trace.

Replay never makes model calls and is always labeled REPLAY; it must never
be presented as live execution.

Usage: .venv/bin/python -m runtime.experiments.replay <trace.json>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def verify_trace(trace: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if trace.get("trace_version") != 1:
        errors.append("Unsupported trace version.")
    run = trace.get("run")
    if not isinstance(run, dict) or not run.get("id"):
        errors.append("Trace is missing its run record.")
    events = trace.get("events", [])
    if not events:
        errors.append("Trace contains no events.")
        return errors
    if events[0]["type"] != "agent.created":
        errors.append("Trace does not start with agent creation.")
    terminal = {"agent.completed", "agent.failed"}
    if events[-1]["type"] not in terminal | {"agent.delegation_returned", "tool.execution.completed"}:
        errors.append(f"Trace ends unexpectedly at {events[-1]['type']}.")
    previous = None
    for event in events:
        try:
            current = datetime.fromisoformat(event["timestamp"])
        except (KeyError, ValueError):
            errors.append(f"Event {event.get('id')} has an invalid timestamp.")
            continue
        if previous and current < previous:
            errors.append(f"Event {event.get('id')} is out of order.")
            break
        previous = current
        if event["type"] == "aip.decision" and not event.get("payload", {}).get("reason"):
            errors.append(f"Decision {event.get('id')} has no recorded reason.")
    for item in trace.get("evidence", []):
        if not item.get("quote") or not item.get("finding"):
            errors.append(f"Evidence {item.get('id')} is missing its quote or finding.")
    metrics = trace.get("metrics", {})
    if metrics.get("steps", 0) != sum(1 for event in events if event["type"] == "model.response.received"):
        errors.append("Recorded step count disagrees with model responses.")
    return errors


def format_playback(trace: dict[str, Any]) -> str:
    events = trace.get("events", [])
    run = trace.get("run", {})
    start = datetime.fromisoformat(events[0]["timestamp"]) if events else None
    lines = [
        "[REPLAY] Recorded KeplerV run — not live execution.",
        f"[REPLAY] run={trace.get('run_id')} goal={run.get('goal_title')} status={run.get('status')}",
    ]
    for event in events:
        elapsed = (datetime.fromisoformat(event["timestamp"]) - start).total_seconds() if start else 0
        tool = f" {event['tool']}" if event.get("tool") else ""
        summary = event.get("payload", {}).get("summary") or event.get("payload", {}).get("reason") or ""
        lines.append(f"[REPLAY] t+{elapsed:7.2f}s {event['type']}{tool} [{event['status']}] {str(summary)[:100]}".rstrip())
    metrics = trace.get("metrics", {})
    lines.append(
        f"[REPLAY] totals: {metrics.get('steps')} steps, {metrics.get('tokens_used')} tokens, "
        f"{metrics.get('denials')} denials, {len(trace.get('evidence', []))} findings, "
        f"{len(trace.get('guard_cases', []))} guard cases."
    )
    return "\n".join(lines) + "\n"


def main(path: str) -> int:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = verify_trace(trace)
    print(format_playback(trace))
    if errors:
        print("[REPLAY] INVALID TRACE:")
        for error in errors:
            print(f"[REPLAY] - {error}")
        return 1
    print("[REPLAY] Trace verified: event order, reasons, evidence, and metrics are consistent.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m runtime.experiments.replay <trace.json>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
