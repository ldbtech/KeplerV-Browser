"""Policy-gated automatic Guard containment.

The local user picks one explicit mode (``off`` / ``pause-only`` /
``full``); ``pause-only`` is the default. The mode decides which
containment actions the trusted runtime may apply on its own when a
high-risk signal fires — a high-risk behavioral classification
(``injection``, ``unauthorized-request``, ``policy-violation``) or a
high-severity Guard case:

- ``off`` — nothing is automatic; every action needs explicit approval.
- ``pause-only`` — ``pause_agent`` and ``deny_pending`` (which also
  pauses) apply automatically; ``isolate_browser`` and
  ``revoke_capability`` still need local-user approval.
- ``full`` — ``isolate_browser`` and ``revoke_capability`` (with an
  explicit capability) may also apply automatically.

This module is pure policy: it never touches the store. Execution lives
in :meth:`GuardContainment.apply_auto`, which records every automatic
action as hash-chained Guard evidence plus a user-visible
``guard.auto_containment`` runtime event, so the user can always see,
keep, or override what the policy did.
"""

from __future__ import annotations

import os

AUTO_CONTAINMENT_ENV = "KEPLERV_GUARD_AUTO_CONTAINMENT"

AUTO_MODES: tuple[str, ...] = ("off", "pause-only", "full")

DEFAULT_AUTO_MODE = "pause-only"

#: Actions the policy may apply without asking in ``pause-only`` mode.
PAUSE_ONLY_ACTIONS: frozenset[str] = frozenset({"pause_agent", "deny_pending"})

#: Actions the policy may apply without asking in ``full`` mode.
FULL_AUTO_ACTIONS: frozenset[str] = frozenset(
    {"pause_agent", "deny_pending", "isolate_browser", "revoke_capability"}
)

#: Behavioral classifications that trigger automatic containment.
AUTO_TRIGGER_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"injection", "unauthorized-request", "policy-violation"}
)

#: Case severities that trigger automatic containment on their own.
AUTO_TRIGGER_SEVERITIES: frozenset[str] = frozenset({"high"})

#: User-visible hint attached to every automatic-containment notice.
AUTO_NOTICE_HINT = (
    "Guard applied this automatically under your auto-containment setting. "
    "Review the Guard case to keep, adjust, or override it."
)


def resolve_auto_mode(explicit: str | None = None) -> str:
    """Resolve the effective mode: explicit setting, then env, then default."""
    if explicit is not None:
        if explicit not in AUTO_MODES:
            raise ValueError(f"Auto-containment mode must be one of: {', '.join(AUTO_MODES)}.")
        return explicit
    configured = (os.getenv(AUTO_CONTAINMENT_ENV) or "").strip()
    if not configured:
        return DEFAULT_AUTO_MODE
    if configured not in AUTO_MODES:
        raise ValueError(
            f"{AUTO_CONTAINMENT_ENV} must be one of: {', '.join(AUTO_MODES)}."
        )
    return configured


def permitted_auto_actions(mode: str) -> frozenset[str]:
    """Actions the policy may apply without asking in this mode."""
    resolved = resolve_auto_mode(mode)
    if resolved == "full":
        return FULL_AUTO_ACTIONS
    if resolved == "pause-only":
        return PAUSE_ONLY_ACTIONS
    return frozenset()


def requires_approval(action: str, mode: str) -> bool:
    """Whether this action still needs explicit local-user approval."""
    return action not in permitted_auto_actions(mode)


def should_auto_contain(
    *,
    classification: str | None = None,
    severity: str | None = None,
) -> bool:
    """Whether the signal is high-risk enough for automatic containment."""
    if classification in AUTO_TRIGGER_CLASSIFICATIONS:
        return True
    return severity in AUTO_TRIGGER_SEVERITIES


def plan_auto_actions(
    *,
    classification: str | None = None,
    severity: str | None = None,
    mode: str,
    capability: str | None = None,
) -> list[str]:
    """Plan the automatic actions for a signal; empty when nothing is automatic.

    ``deny_pending`` denies every pending approval and pauses the agent, so
    it covers the ``pause-only`` response on its own. ``revoke_capability``
    is only planned when the caller names the capability to narrow.
    """
    resolved = resolve_auto_mode(mode)
    if resolved == "off":
        return []
    if not should_auto_contain(classification=classification, severity=severity):
        return []
    if resolved == "full":
        plan = ["deny_pending", "isolate_browser"]
        if capability:
            plan.append("revoke_capability")
        return plan
    return ["deny_pending"]


def auto_authorization(mode: str) -> str:
    """Audit label recorded on every automatic action (never a user action)."""
    return f"policy auto-containment (mode={resolve_auto_mode(mode)})"
