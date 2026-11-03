"""SSRF guard for Trust Gateway endpoints (Phase 6 M1+M2).

Default-deny: loopback, private, link-local, reserved, and cloud-metadata
addresses are blocked. Every resolved IP of the target host must be global.
Redirect targets are re-validated per hop by the gateway client.

Explicit exception: approved loopback *fixtures* for tests, allowlisted by
exact ``host:port``. The allowlist is explicit per call (or via the
``KEPLERV_GATEWAY_FIXTURE_ALLOWLIST`` env var, comma-separated ``host:port``
entries); nothing else may reach loopback.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"https"}
LOOPBACK_FIXTURE_HOSTS = {"127.0.0.1", "::1", "localhost"}

#: Well-known cloud metadata endpoints — always blocked.
METADATA_IPS = frozenset({
    "169.254.169.254",  # AWS / GCP / Azure IMDS
    "100.100.100.200",  # Alibaba Cloud metadata
    "192.0.0.192",      # Oracle Cloud metadata (reserved, also caught by is_global)
})

MAX_URL_LENGTH = 2000


class SSRFBlocked(Exception):
    """Raised when an endpoint fails SSRF validation."""


def fixture_allowlist() -> set[str]:
    raw = os.getenv("KEPLERV_GATEWAY_FIXTURE_ALLOWLIST", "")
    entries = {entry.strip().lower() for entry in raw.split(",") if entry.strip()}
    return {entry for entry in entries if entry}


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _resolve_ips(host: str, resolve=socket.getaddrinfo) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    if _is_ip(host):
        return {ipaddress.ip_address(host)}
    try:
        infos = resolve(host, 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise SSRFBlocked(f"Host '{host}' did not resolve: {exc}") from exc
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for item in infos:
        try:
            addresses.add(ipaddress.ip_address(item[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise SSRFBlocked(f"Host '{host}' resolved to no usable addresses.")
    return addresses


def validate_endpoint(
    url: str,
    *,
    allowlist: set[str] | None = None,
    resolve=socket.getaddrinfo,
) -> dict[str, str | int]:
    """Validate an external-agent endpoint URL. Returns parsed origin info.

    Raises :class:`SSRFBlocked` on any denial. Loopback fixtures must match
    the explicit ``host:port`` allowlist (param or env var).
    """
    if not url or len(url) > MAX_URL_LENGTH:
        raise SSRFBlocked("Endpoint URL is missing or too long.")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise SSRFBlocked(f"Endpoint URL is malformed: {exc}") from exc
    if parsed.username or parsed.password:
        raise SSRFBlocked("Credentials in endpoint URLs are blocked.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise SSRFBlocked("Endpoint URL has no host.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    hostport = f"{host}:{port}"

    effective_allowlist = {entry.lower() for entry in (allowlist or set())} | fixture_allowlist()
    if hostport in effective_allowlist:
        if parsed.scheme not in {"http", "https"}:
            raise SSRFBlocked(f"Scheme '{parsed.scheme}' is not allowed, even for fixtures.")
        if host not in LOOPBACK_FIXTURE_HOSTS and not _is_loopback(host):
            raise SSRFBlocked("Fixture allowlist entries must be loopback hosts.")
        return {"scheme": parsed.scheme, "host": host, "port": port, "fixture": "true"}

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked(f"Scheme '{parsed.scheme}' is not allowed; external endpoints must use HTTPS.")
    addresses = _resolve_ips(host, resolve)
    for address in addresses:
        if str(address) in METADATA_IPS:
            raise SSRFBlocked(f"Host '{host}' resolves to a blocked cloud-metadata address.")
        if not address.is_global:
            raise SSRFBlocked(f"Host '{host}' resolves to a non-public address ({address}); blocked.")
    return {"scheme": parsed.scheme, "host": host, "port": port, "fixture": "false"}


def validate_redirect(location: str | None, *, allowlist: set[str] | None = None, resolve=socket.getaddrinfo) -> str:
    """Re-validate a redirect target; returns the location or raises."""
    if not location:
        raise SSRFBlocked("Empty redirect target blocked.")
    validate_endpoint(location, allowlist=allowlist, resolve=resolve)
    return location


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
