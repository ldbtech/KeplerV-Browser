"""Per-agent/per-goal service grants (Phase 5 M8).

Grants are exact permission strings stored on the run row, so each grant is
inherently scoped to one (agent_id, goal_id) pair. Child agents start with a
fresh permission list and never inherit service grants — the scheduler builds
child permissions explicitly without copying any ``SERVICE_PERMISSIONS``
entries. Revocation edits the run row directly, so it takes effect on the
very next tool call (the scheduler re-reads live permissions per call).
"""

from __future__ import annotations

from runtime.aip.policy import (
    SENSITIVE_SERVICE_PERMISSIONS,
    SERVICE_PERMISSIONS,
    SERVICE_TOOL_PERMISSION,
    SERVICE_TOOLS,
)

#: Voice/control action name that revokes an agent's service access.
REVOKE_ACTION = "REVOKE_AGENT_SERVICE_ACCESS"

__all__ = [
    "REVOKE_ACTION",
    "SENSITIVE_SERVICE_PERMISSIONS",
    "SERVICE_PERMISSIONS",
    "SERVICE_TOOL_PERMISSION",
    "SERVICE_TOOLS",
    "grant_to",
    "is_sensitive",
    "revoke_from",
    "validate_permission",
]


def validate_permission(permission: str) -> str:
    """Return the permission if grantable, else raise ValueError."""
    if permission not in SERVICE_PERMISSIONS:
        raise ValueError(
            f"Unknown service permission '{permission}'. "
            f"Grantable: {', '.join(sorted(SERVICE_PERMISSIONS))}."
        )
    return permission


def is_sensitive(permission: str) -> bool:
    return validate_permission(permission) in SENSITIVE_SERVICE_PERMISSIONS


def grant_to(permissions: list[str], permission: str) -> list[str]:
    """Return a new permission list with the grant added (idempotent)."""
    validate_permission(permission)
    if permission in permissions:
        return list(permissions)
    return [*permissions, permission]


def revoke_from(permissions: list[str], permission: str | None) -> tuple[list[str], list[str]]:
    """Return ``(remaining, removed)`` after revoking one grant (or all when None)."""
    if permission is None:
        removed = [entry for entry in permissions if entry in SERVICE_PERMISSIONS]
        return [entry for entry in permissions if entry not in SERVICE_PERMISSIONS], removed
    validate_permission(permission)
    removed = [permission] if permission in permissions else []
    return [entry for entry in permissions if entry != permission], removed
