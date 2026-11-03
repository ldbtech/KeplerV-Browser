"""External-agent registry (Phase 6 M1).

Records hold identity tier, declared vs observed capabilities, provenance
(who asserted what, when, with what evidence), and reliability evidence.
No secrets are stored here, ever: registration rejects secret-bearing
fields, and records are safe to surface to the frontend.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.models import utc_now

IDENTITY_STATUSES = ("verified", "self_declared", "unknown", "suspected")

#: Field names that must never appear in a registry record.
SECRET_FIELD_NAMES = frozenset({
    "secret", "token", "password", "api_key", "apikey",
    "authorization", "credential", "private_key", "client_secret",
})


def _default_store_path() -> Path:
    data_dir = os.getenv("KEPLERV_DATA_DIR", "")
    base = Path(data_dir) if data_dir else Path.cwd() / ".keplerv-data"
    return base / "gateway" / "agents.json"


def _reject_secrets(value: Any, trail: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in SECRET_FIELD_NAMES:
                raise ValueError(f"Registry records must not contain secrets (field '{trail}{key}').")
            _reject_secrets(item, f"{trail}{key}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{trail}{index}.")


class ExternalAgentRegistry:
    """JSON-backed external-agent metadata store (no secrets)."""

    def __init__(self, store_path: Path | str | None = None) -> None:
        self._store_path = Path(store_path) if store_path else _default_store_path()
        self._agents: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ----------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._store_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            for agent_id, record in raw.items():
                if isinstance(record, dict):
                    self._agents[str(agent_id)] = record

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._agents, indent=2, sort_keys=True))
        tmp.replace(self._store_path)

    # -- CRUD ------------------------------------------------------------

    def register(
        self,
        agent_id: str,
        *,
        endpoint: str,
        identity_status: str = "unknown",
        capabilities_declared: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        auth: dict[str, Any] | None = None,
        protocol: str = "keplerv-gateway",
        protocol_version: str = "",
        display_name: str = "",
        tenant: str = "",
    ) -> dict[str, Any]:
        """Register (or replace) an external agent record.

        ``provenance`` must say who asserted this record, e.g.
        ``{"asserted_by": ..., "asserted_at": ..., "evidence_refs": [...]}``.
        ``auth`` describes the auth *mechanism* (``{"type": "none"}`` or
        ``{"type": "bearer-header", "header": "Authorization"}``) — never a
        credential value.
        """
        agent_id = agent_id.strip()
        if not agent_id or len(agent_id) > 128:
            raise ValueError("Agent id must be 1-128 characters.")
        if not endpoint or len(endpoint) > 2000:
            raise ValueError("Endpoint must be 1-2000 characters.")
        if identity_status not in IDENTITY_STATUSES:
            raise ValueError(f"Unknown identity status '{identity_status}'. Expected one of {', '.join(IDENTITY_STATUSES)}.")
        if protocol not in {"keplerv-gateway", "a2a"}:
            raise ValueError("Unsupported external agent protocol.")
        if provenance is None or not str(provenance.get("asserted_by", "")).strip():
            raise ValueError("Provenance with 'asserted_by' is required.")
        _reject_secrets({"provenance": provenance, "capabilities_declared": capabilities_declared, "auth": auth})
        auth = auth or {"type": "none"}
        if auth.get("type") not in {"none", "bearer-header"}:
            raise ValueError("Auth type must be 'none' or 'bearer-header'.")
        record = {
            "agent_id": agent_id,
            "endpoint": endpoint,
            "protocol": protocol,
            "protocol_version": protocol_version[:16],
            "display_name": display_name[:200],
            "tenant": tenant[:256],
            "identity_status": identity_status,
            "capabilities_declared": sorted(set(capabilities_declared or [])),
            "capabilities_observed": [],
            "provenance": {
                "asserted_by": str(provenance["asserted_by"])[:256],
                "asserted_at": str(provenance.get("asserted_at") or utc_now()),
                "evidence_refs": list(provenance.get("evidence_refs") or []),
            },
            "reliability": {
                "sessions": 0,
                "messages_sent": 0,
                "messages_received": 0,
                "validation_failures": 0,
                "timeouts": 0,
            },
            "auth": {"type": auth["type"], **({"header": str(auth.get("header", "Authorization"))[:64]} if auth["type"] == "bearer-header" else {})},
            "registered_at": utc_now(),
        }
        _reject_secrets(record)
        self._agents[agent_id] = record
        self._save()
        return deepcopy(record)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        record = self._agents.get(agent_id)
        return deepcopy(record) if record else None

    def list_agents(self) -> list[dict[str, Any]]:
        return [deepcopy(record) for _, record in sorted(self._agents.items())]

    def set_identity_status(self, agent_id: str, status: str, *, asserted_by: str, evidence_refs: list[str] | None = None) -> dict[str, Any]:
        record = self._agents.get(agent_id)
        if record is None:
            raise KeyError(f"Unknown external agent '{agent_id}'.")
        if status not in IDENTITY_STATUSES:
            raise ValueError(f"Unknown identity status '{status}'.")
        if not asserted_by.strip():
            raise ValueError("'asserted_by' is required.")
        record["identity_status"] = status
        record["provenance"] = {
            "asserted_by": asserted_by[:256],
            "asserted_at": utc_now(),
            "evidence_refs": list(evidence_refs or []),
        }
        self._save()
        return deepcopy(record)

    def record_observed_capability(self, agent_id: str, capability: str) -> dict[str, Any]:
        record = self._agents.get(agent_id)
        if record is None:
            raise KeyError(f"Unknown external agent '{agent_id}'.")
        capability = capability.strip()[:128]
        if capability and capability not in record["capabilities_observed"]:
            record["capabilities_observed"].append(capability)
            record["capabilities_observed"].sort()
            self._save()
        return deepcopy(record)

    def record_reliability(self, agent_id: str, field: str, increment: int = 1) -> dict[str, Any] | None:
        record = self._agents.get(agent_id)
        if record is None:
            return None
        if field in record["reliability"]:
            record["reliability"][field] += increment
            self._save()
        return deepcopy(record)
