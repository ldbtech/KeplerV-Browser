"""Small, public-endpoint A2A client for the user-facing Trust Gateway.

This adapter intentionally supports public HTTPS Agent Cards without bearer
credentials. Authenticated cards are surfaced as unsupported rather than
silently contacted without the advertised security scheme.
"""
from __future__ import annotations

from typing import Any
import asyncio
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx

from runtime.gateway.ssrf import SSRFBlocked, validate_endpoint

AGENT_CARD_PATHS = (".well-known/agent-card.json", ".well-known/agent.json")


class A2AError(ValueError):
    pass


def _public_https(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise A2AError("A2A agents must use a public HTTPS endpoint without URL credentials.")
    try:
        validate_endpoint(url)
    except SSRFBlocked as exc:
        raise A2AError(f"Endpoint blocked by the network safety check: {exc}") from exc
    return url


def _card_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2000:
        raise A2AError("Enter a valid public Agent Card URL or agent base URL.")
    parsed = urlparse(value)
    if parsed.path.endswith(".json"):
        return _public_https(value)
    base = value.rstrip("/") + "/"
    # Use the current published card path first; accept the previous path for
    # deployed agents that have not migrated yet.
    return _public_https(urljoin(base, AGENT_CARD_PATHS[0]))


def validate_card(raw: Any, card_url: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise A2AError("Agent Card response must be a JSON object.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise A2AError("Agent Card must include a name.")
    interfaces = raw.get("supportedInterfaces")
    selected = None
    if isinstance(interfaces, list):
        selected = next((item for item in interfaces if isinstance(item, dict)
                         and "jsonrpc" in str(item.get("protocolBinding", "")).casefold().replace("-", "")), None)
        if selected is None:
            raise A2AError("This A2A agent does not publish a supported JSON-RPC interface.")
    endpoint = selected.get("url") if selected else raw.get("url")
    if not isinstance(endpoint, str):
        raise A2AError("Agent Card does not include a JSON-RPC service URL.")
    if not interfaces and raw.get("preferredTransport") and "jsonrpc" not in str(raw["preferredTransport"]).casefold().replace("-", ""):
        raise A2AError("This A2A agent does not publish a supported JSON-RPC interface.")
    _public_https(endpoint)
    if raw.get("security"):
        raise A2AError("This agent requires authentication. KeplerV currently connects only to public, unauthenticated A2A agents.")
    skills = raw.get("skills", [])
    if not isinstance(skills, list):
        raise A2AError("Agent Card skills must be a list.")
    return {
        "agent_id": str(raw.get("id") or f"{urlparse(endpoint).hostname}-{name.casefold().replace(' ', '-')}")[:128],
        "name": name.strip()[:200],
        "endpoint": endpoint,
        "card_url": card_url,
        "version": str(raw.get("version") or "unknown")[:64],
        "protocol_version": str((selected or {}).get("protocolVersion") or raw.get("protocolVersion") or ("1.0" if selected else "0.3"))[:16],
        "tenant": str((selected or {}).get("tenant") or "")[:256],
        "description": str(raw.get("description") or "")[:2000],
        "skills": [{"id": str(s.get("id", ""))[:128], "name": str(s.get("name", ""))[:200], "description": str(s.get("description", ""))[:1000]} for s in skills[:30] if isinstance(s, dict)],
        "protocol": "A2A",
        "identity_status": "unknown",
        "authenticated": False,
    }


async def fetch_card(card_or_base_url: str, *, timeout_s: float = 10.0, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    card_url = _card_url(card_or_base_url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s, transport=transport) as client:
        try:
            response = await client.get(card_url, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            raise A2AError("A2A Agent Card request timed out.") from exc
        except httpx.RequestError as exc:
            raise A2AError("A2A Agent Card network request failed.") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            raise A2AError("Agent Card redirects are refused. Enter the final HTTPS Agent Card URL.")
        if response.status_code != 200:
            # Try the legacy well-known path only when the current path was
            # derived from a base URL, never when a precise card URL was given.
            parsed = urlparse(card_url)
            if parsed.path.endswith(AGENT_CARD_PATHS[0]):
                legacy = urljoin(card_url.rsplit(".well-known/", 1)[0], AGENT_CARD_PATHS[1])
                _public_https(legacy)
                try:
                    response = await client.get(legacy, headers={"Accept": "application/json"})
                except httpx.TimeoutException as exc:
                    raise A2AError("A2A Agent Card request timed out.") from exc
                except httpx.RequestError as exc:
                    raise A2AError("A2A Agent Card network request failed.") from exc
                if response.status_code == 200:
                    card_url = legacy
            if response.status_code != 200:
                raise A2AError(f"Agent Card request failed with HTTP {response.status_code}.")
        if len(response.content) > 256_000:
            raise A2AError("Agent Card is larger than the 256 KB safety limit.")
        try:
            return validate_card(response.json(), card_url)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, A2AError):
                raise
            raise A2AError("Agent Card did not contain valid JSON.") from exc


def _extract_text(result: Any) -> str:
    if not isinstance(result, dict):
        raise A2AError("A2A agent returned an unsupported response.")
    message = result.get("result", result)
    if not isinstance(message, dict):
        raise A2AError("A2A response result is malformed.")
    message = message.get("message") or message.get("task") or message
    parts: list[str] = []
    candidate_parts = message.get("parts", [])
    if not candidate_parts and isinstance(message.get("status"), dict):
        candidate_parts = message["status"].get("message", {}).get("parts", [])
    if not candidate_parts and isinstance(message.get("artifacts"), list):
        candidate_parts = [part for artifact in message["artifacts"] if isinstance(artifact, dict)
                           for part in artifact.get("parts", []) if isinstance(artifact.get("parts", []), list)]
    for part in candidate_parts if isinstance(candidate_parts, list) else []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"][:12000])
    if not parts:
        raise A2AError("A2A agent returned no readable text. Its response remains recorded in the transcript.")
    return "\n".join(parts)[:12000]


async def send_message(endpoint: str, text: str, *, context_id: str, protocol_version: str = "1.0", tenant: str = "", timeout_s: float = 45.0, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, dict[str, Any]]:
    _public_https(endpoint)
    if not text.strip() or len(text) > 8000:
        raise A2AError("Message must contain 1–8,000 characters.")
    request_id = str(uuid4())
    version = "0.3" if protocol_version.startswith("0.") else "1.0"
    role = "user" if version == "0.3" else "ROLE_USER"
    method = "message/send" if version == "0.3" else "SendMessage"
    params: dict[str, Any] = {"message": {"messageId": str(uuid4()), "contextId": context_id,
                                          "role": role, "parts": [{"text": text}]},
                              "configuration": {"acceptedOutputModes": ["text/plain"]}}
    if tenant:
        params["tenant"] = tenant
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s, transport=transport) as client:
        try:
            response = await client.post(endpoint, json=body, headers={"Content-Type": "application/json", "Accept": "application/json", "A2A-Version": version})
        except httpx.TimeoutException as exc:
            raise A2AError("A2A message request timed out.") from exc
        except httpx.RequestError as exc:
            raise A2AError("A2A network request failed.") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            raise A2AError("A2A endpoint redirects are refused.")
        if response.status_code != 200:
            raise A2AError(f"A2A message request failed with HTTP {response.status_code}.")
        if len(response.content) > 128_000:
            raise A2AError("A2A response is larger than the 128 KB safety limit.")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise A2AError("A2A endpoint returned invalid JSON.") from exc
        if not isinstance(envelope, dict) or envelope.get("id") != request_id:
            raise A2AError("A2A JSON-RPC response did not match the request id.")
        if envelope.get("error"):
            error = envelope["error"]
            message = error.get("message", "Agent returned an error") if isinstance(error, dict) else "Agent returned an error"
            raise A2AError(str(message)[:500])
        result = envelope.get("result")
        task_result = result.get("task") if isinstance(result, dict) and isinstance(result.get("task"), dict) else result
        # A2A agents may return a Task before its work is complete. Poll the
        # same endpoint using the standard tasks/get RPC until a terminal state.
        if isinstance(task_result, dict) and isinstance(task_result.get("id"), str) and isinstance(task_result.get("status"), dict):
            result = task_result
            task_id = task_result["id"]
            deadline = asyncio.get_running_loop().time() + min(timeout_s, 45.0)
            state = task_result["status"].get("state")
            while state in {"submitted", "working", "input-required", "auth-required"} and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1)
                poll_id = str(uuid4())
                poll_method = "tasks/get" if version == "0.3" else "GetTask"
                try:
                    poll = await client.post(endpoint, json={"jsonrpc": "2.0", "id": poll_id, "method": poll_method, "params": {"id": task_id}}, headers={"Content-Type": "application/json", "Accept": "application/json", "A2A-Version": version})
                except httpx.TimeoutException as exc:
                    raise A2AError("A2A task status request timed out.") from exc
                except httpx.RequestError as exc:
                    raise A2AError("A2A task status request failed.") from exc
                if poll.status_code in {301, 302, 303, 307, 308}:
                    raise A2AError("A2A task polling redirects are refused.")
                if poll.status_code != 200:
                    raise A2AError(f"A2A task status request failed with HTTP {poll.status_code}.")
                if len(poll.content) > 128_000:
                    raise A2AError("A2A task response is larger than the 128 KB safety limit.")
                poll_envelope = poll.json()
                if not isinstance(poll_envelope, dict) or poll_envelope.get("id") != poll_id or poll_envelope.get("error"):
                    raise A2AError("A2A task status response was invalid.")
                result = poll_envelope.get("result")
                if not isinstance(result, dict):
                    raise A2AError("A2A task status response was malformed.")
                task_result = result.get("task") if isinstance(result.get("task"), dict) else result
                state = task_result.get("status", {}).get("state") if isinstance(task_result.get("status"), dict) else None
                result = task_result
            if state in {"failed", "rejected", "canceled", "cancelled"}:
                raise A2AError(f"A2A task ended with state '{state}'.")
            if state in {"submitted", "working"}:
                raise A2AError("A2A task is still running. Retry after the agent finishes.")
    return _extract_text(result), result if isinstance(result, dict) else {"result": str(result)[:2000]}
