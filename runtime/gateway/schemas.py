"""Gateway message envelope schemas (Phase 6 M1+M2).

Strict validation: unknown fields are rejected (``extra="forbid"``), mirroring
the browser tool-arg models. Every message carries a correlation id
(``message_id`` + optional ``reply_to``) and an explicit trust label:
outbound messages are ``live`` gateway traffic, inbound payloads are always
tagged ``untrusted`` data, and replayed fixtures must be labeled ``replay``.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

GATEWAY_PROTOCOL_VERSION = "keplerv-gateway/1"

MessageKind = Literal["request", "response", "event", "error"]
TrustLabel = Literal["live", "replay"]
Direction = Literal["outbound", "inbound"]


class GatewayMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(default=GATEWAY_PROTOCOL_VERSION, min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    reply_to: str | None = Field(default=None, max_length=128)
    sender: str = Field(min_length=1, max_length=128)
    recipient: str = Field(min_length=1, max_length=128)
    kind: MessageKind = "request"
    label: TrustLabel = "live"
    payload: dict[str, Any] = Field(default_factory=dict)
    untrusted: bool = False


def serialize_message(message: GatewayMessage) -> dict[str, Any]:
    return message.model_dump(mode="json")


def parse_message(raw: Any) -> GatewayMessage:
    """Parse and strictly validate an inbound envelope; rejects extras."""
    if not isinstance(raw, dict):
        raise ValueError("Gateway message must be a JSON object.")
    return GatewayMessage.model_validate(raw)


def tag_untrusted(message: GatewayMessage) -> GatewayMessage:
    """Mark an inbound message as untrusted data (never instructions)."""
    message.untrusted = True
    return message
