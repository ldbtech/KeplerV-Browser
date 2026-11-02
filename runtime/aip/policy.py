from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AIPDecision:
    outcome: str
    reason: str


READ_TOOLS = {"browser.navigate", "browser.inspect", "browser.extract_text", "browser.screenshot"}
APPROVAL_TOOLS = {"browser.click", "browser.fill"}
DELEGATION_TOOL = "agent.delegate"


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
    authorized_origin: tuple[str, str, int],
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
    if tool_name not in READ_TOOLS | APPROVAL_TOOLS:
        return AIPDecision("deny", "This tool is not available to the agent.")
    target_url = arguments.get("url") if tool_name == "browser.navigate" else current_url
    if not target_url:
        return AIPDecision("deny", "Open the user-authorized page before using browser tools.")
    target = _origin(target_url)
    if target is None or target != authorized_origin:
        return AIPDecision("deny", "Browser access is limited to the HTTPS origin the user authorized.")
    if not public_host_check(target[1]):
        return AIPDecision("deny", "The target resolves to a non-public or unreachable host.")
    capability = "browser.read" if tool_name in READ_TOOLS else "browser.interact"
    if capability not in permissions:
        return AIPDecision("deny", f"Capability '{capability}' is outside the agent's permission scope.")
    if tool_name in APPROVAL_TOOLS:
        if user_approved:
            return AIPDecision("allow", "The user approved this specific browser interaction.")
        return AIPDecision("require_approval", "Page interaction requires a human approval before execution.")
    return AIPDecision("allow", "Agent, goal, capability, and authorized origin are valid.")
