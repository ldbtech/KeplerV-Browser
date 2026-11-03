"""Gateway event recording (Phase 6 M1+M2).

Per-message events plus session lifecycle events, recorded into the runtime
event ledger through the same ``store.add_event`` path the scheduler uses,
with a Guard observe hook so gateway anomalies open cases.
"""

from __future__ import annotations

from typing import Any

SESSION_OPENED = "gateway.session.opened"
SESSION_CLOSED = "gateway.session.closed"
MESSAGE_SENT = "gateway.message.sent"
MESSAGE_RECEIVED = "gateway.message.received"
LIMIT_EXCEEDED = "gateway.limit_exceeded"
SSRF_BLOCKED = "gateway.ssrf_blocked"
VALIDATION_FAILED = "gateway.validation_failed"
TIMEOUT = "gateway.timeout"
CANCELLED = "gateway.cancelled"


def record_gateway_event(
    store,
    run: dict[str, Any],
    kind: str,
    status: str,
    payload: dict[str, Any] | None = None,
    *,
    guard_observer=None,
) -> dict[str, Any]:
    """Record a gateway event; notify Guard for anomaly kinds."""
    event = store.add_event(run, kind, status, payload or {}, "gateway.send")
    if guard_observer is not None and kind in {
        SSRF_BLOCKED, LIMIT_EXCEEDED, VALIDATION_FAILED, TIMEOUT,
        "gateway.provenance_unknown_session",
        "gateway.external_contradiction",
        "gateway.external_behavior_flagged",
    }:
        try:
            guard_observer.observe(event)
        except Exception:
            pass
    return event
