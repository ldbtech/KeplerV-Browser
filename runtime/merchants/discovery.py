"""Explicit-endpoint discovery for synthetic merchants (Phase 6 M3).

Discovery fetches capability descriptors from explicitly configured base
URLs only — no scanning, no guessing, no crawling. Every URL is
SSRF-validated (loopback fixtures allowed only via the explicit
``host:port`` allowlist), redirects are never followed automatically, and
descriptors are shape-checked with secret-bearing fields rejected.
"""

from __future__ import annotations

from typing import Any

import httpx

from runtime.gateway.registry import SECRET_FIELD_NAMES
from runtime.gateway.ssrf import SSRFBlocked, validate_endpoint
from runtime.merchants.base import CAPABILITIES_PATH

MAX_REDIRECTS = 3

REQUIRED_DESCRIPTOR_KEYS = frozenset({
    "agent_id", "endpoint", "capabilities_declared", "fixture",
})


class DiscoveryError(Exception):
    pass


class DiscoveryAuthError(DiscoveryError):
    pass


def _reject_secrets(value: Any, trail: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in SECRET_FIELD_NAMES:
                raise DiscoveryError(f"Descriptor must not contain secrets (field '{trail}{key}').")
            _reject_secrets(item, f"{trail}{key}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{trail}{index}.")


def validate_descriptor(raw: Any) -> dict[str, Any]:
    """Shape-check a served capability descriptor (untrusted data)."""
    if not isinstance(raw, dict):
        raise DiscoveryError("Capability descriptor must be a JSON object.")
    missing = REQUIRED_DESCRIPTOR_KEYS - set(raw)
    if missing:
        raise DiscoveryError(f"Capability descriptor is missing keys: {sorted(missing)}.")
    if raw.get("fixture") is not True:
        raise DiscoveryError("Capability descriptor is not marked as a fixture.")
    if not isinstance(raw.get("agent_id"), str) or not raw["agent_id"].strip():
        raise DiscoveryError("Capability descriptor has no agent_id.")
    if not isinstance(raw.get("capabilities_declared"), list):
        raise DiscoveryError("Capability descriptor has no capabilities_declared list.")
    _reject_secrets(raw)
    return dict(raw)


async def _fetch_descriptor(
    base: str,
    *,
    allowlist: set[str],
    timeout_s: float,
    auth_token: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    capabilities_url = base.rstrip("/") + CAPABILITIES_PATH
    validate_endpoint(capabilities_url, allowlist=allowlist)
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    client_kwargs: dict[str, Any] = {"follow_redirects": False, "timeout": timeout_s}
    if transport is not None:
        client_kwargs["transport"] = transport
    current_url = capabilities_url
    async with httpx.AsyncClient(**client_kwargs) as client:
        for _ in range(MAX_REDIRECTS + 1):
            response = await client.get(current_url, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                # Discovery never follows redirects automatically: re-validate
                # and refuse cross-host hops outright.
                raise DiscoveryError(f"Descriptor redirect refused (SSRF-safe): {response.headers.get('location')!r}.")
            if response.status_code == 401:
                raise DiscoveryAuthError("Descriptor fetch rejected: fixture token required.")
            if response.status_code != 200:
                raise DiscoveryError(f"Descriptor fetch failed: HTTP {response.status_code}.")
            try:
                return validate_descriptor(response.json())
            except ValueError as exc:
                raise DiscoveryError(f"Descriptor is not JSON: {exc}") from exc
    raise DiscoveryError("Descriptor fetch failed: too many redirects.")


async def discover_agents(
    bases: list[str],
    *,
    allowlist: set[str] | None = None,
    timeout_s: float = 10.0,
    auth_token: str | None = None,
    auth_tokens: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | dict[str, httpx.AsyncBaseTransport] | None = None,
) -> list[dict[str, Any]]:
    """Fetch descriptors for exactly the configured base URLs, in order.

    ``transport`` may be a single transport or a per-base mapping (tests).
    Raises :class:`SSRFBlocked` before any fetch to a non-allowlisted URL.
    """
    if not bases:
        raise DiscoveryError("Discovery needs at least one explicitly configured endpoint.")
    per_base = auth_tokens or {}
    descriptors: list[dict[str, Any]] = []
    for base in bases:
        if not base or len(base) > 2000:
            raise DiscoveryError("Configured endpoint is missing or too long.")
        picked: httpx.AsyncBaseTransport | None
        if isinstance(transport, dict):
            picked = transport.get(base)
        else:
            picked = transport
        descriptors.append(await _fetch_descriptor(
            base,
            allowlist=allowlist or set(),
            timeout_s=timeout_s,
            auth_token=per_base.get(base, auth_token),
            transport=picked,
        ))
    return descriptors
