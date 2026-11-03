from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import socket
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AIPDecision:
    outcome: str
    reason: str


READ_TOOLS = {"web.search", "browser.navigate", "browser.inspect", "browser.extract_text", "browser.screenshot"}
APPROVAL_TOOLS = {"browser.click", "browser.fill"}
DELEGATION_TOOL = "agent.delegate"
#: Read-only legacy mail tools (kept for backward compatibility; new code
#: uses the gmail.* service tools below).
MAIL_READ_TOOLS = {"mail.read", "mail.search", "mail.get"}

#: Canonical service tool -> required per-agent/per-goal grant. Grants are
#: exact strings stored on the run row; there are no wildcard grants
#: ("calendar.*" in the plan denotes the calendar.* family below, not a
#: grantable name). "mail.send" is a legacy alias for the same gmail.send
#: grant so older callers are still gated instead of silently dropped.
SERVICE_TOOL_PERMISSION = {
    "gmail.read": "gmail.read",
    "gmail.search": "gmail.search",
    "gmail.send": "gmail.send",
    "mail.send": "gmail.send",
    "calendar.read": "calendar.read",
    "calendar.create": "calendar.create",
    "calendar.delete": "calendar.delete",
    "drive.read": "drive.read",
    "drive.share": "drive.share",
    "github.read": "github.read",
    "github.publish": "github.publish",
    "media.open": "media.open",
    "media.control": "media.control",
}
SERVICE_TOOLS = frozenset(SERVICE_TOOL_PERMISSION)
SERVICE_PERMISSIONS = frozenset(SERVICE_TOOL_PERMISSION.values())
#: Sensitive service ops: a grant alone is never enough; each execution
#: also requires explicit human approval (send/share/publish + calendar
#: writes, which publish to other people).
SENSITIVE_SERVICE_TOOLS = frozenset({
    "gmail.send", "mail.send", "calendar.create", "calendar.delete",
    "drive.share", "github.publish",
})
SENSITIVE_SERVICE_PERMISSIONS = frozenset(
    SERVICE_TOOL_PERMISSION[tool] for tool in SENSITIVE_SERVICE_TOOLS
)


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port or 443
        if port != 443:
            return None
        return parsed.scheme, parsed.hostname.lower().rstrip("."), port
    except ValueError:
        return None


def demo_mode() -> bool:
    return os.getenv("KEPLERV_DEMO_MODE") == "1"


def is_demo_url(url: str | None) -> bool:
    """Explicitly opted-in loopback fixture origins; never a public bypass."""
    if not demo_mode() or not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (parsed.scheme == "http"
            and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost"}
            and not parsed.username and not parsed.password
            and parsed.path.startswith("/demo/"))


def is_public_host(host: str) -> bool:
    try:
        addresses = {ipaddress.ip_address(host)} if _is_ip(host) else {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(address.is_global for address in addresses)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def authorize_tool(
    *,
    agent_id: str,
    registered_agent_id: str,
    goal_id: str,
    registered_goal_id: str,
    permissions: set[str],
    tool_name: str,
    arguments: dict,
    authorized_origin: tuple[str, str, int] | None,
    authorized_origins: set[tuple[str, str, int]] | None = None,
    current_url: str | None = None,
    user_approved: bool = False,
    delegation_allowed: bool = False,
    public_host_check=is_public_host,
) -> AIPDecision:
    if agent_id != registered_agent_id:
        return AIPDecision("deny", "Agent identity is not registered for this run.")
    if goal_id != registered_goal_id:
        return AIPDecision("deny", "The agent is not assigned to the requested goal.")
    if tool_name == DELEGATION_TOOL:
        if "delegate" not in permissions or not delegation_allowed:
            return AIPDecision("deny", "Delegation permission or budget is unavailable.")
        return AIPDecision("allow", "Delegation is within the parent's assigned scope and limits.")
    if tool_name not in READ_TOOLS | APPROVAL_TOOLS | MAIL_READ_TOOLS | SERVICE_TOOLS:
        return AIPDecision("deny", "This tool is not available to the agent.")
    if tool_name in MAIL_READ_TOOLS:
        if "mail.read" not in permissions:
            return AIPDecision("deny", "Mail reading is outside the agent's permission scope.")
        return AIPDecision("allow", "Mail read is limited to connected, non-revoked accounts.")
    if tool_name in SERVICE_TOOLS:
        required = SERVICE_TOOL_PERMISSION[tool_name]
        if required not in permissions:
            return AIPDecision(
                "deny",
                f"Service permission '{required}' is not granted to this agent for this goal.",
            )
        if tool_name in SENSITIVE_SERVICE_TOOLS:
            if user_approved:
                return AIPDecision("allow", "The user approved this specific sensitive service operation.")
            return AIPDecision(
                "require_approval",
                "Sending, sharing, or publishing through a connected service requires explicit human approval before execution.",
            )
        return AIPDecision("allow", "Service read is limited to granted, connected, non-revoked accounts.")
    if tool_name == "web.search":
        if "browser.read" not in permissions:
            return AIPDecision("deny", "Web search is outside the agent's permission scope.")
        return AIPDecision("allow", "Search is limited to the public web search service.")
    target_url = arguments.get("url") if tool_name == "browser.navigate" else current_url
    if not target_url:
        return AIPDecision("deny", "Open the user-authorized page before using browser tools.")
    demo_fixture = is_demo_url(target_url if isinstance(target_url, str) else None)
    target = _origin(target_url)
    allowed_origins = authorized_origins or ({authorized_origin} if authorized_origin else set())
    if not demo_fixture and (target is None or target not in allowed_origins):
        return AIPDecision("deny", "Browser access is limited to HTTPS origins authorized by the user's web search or directly provided by the user.")
    if not demo_fixture and target is not None and not public_host_check(target[1]):
        return AIPDecision("deny", "The target resolves to a non-public or unreachable host.")
    capability = "browser.read" if tool_name in READ_TOOLS else "browser.interact"
    if capability not in permissions:
        return AIPDecision("deny", f"Capability '{capability}' is outside the agent's permission scope.")
    if tool_name in APPROVAL_TOOLS:
        if user_approved:
            return AIPDecision("allow", "The user approved this specific browser interaction.")
        return AIPDecision("require_approval", "Page interaction requires a human approval before execution.")
    return AIPDecision("allow", "Agent, goal, capability, and authorized origin are valid.")
