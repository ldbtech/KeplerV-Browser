"""Phase 6 M1+M2: External-agent registry + Trust Gateway.

All cross-boundary traffic to external agents flows through
:class:`runtime.gateway.client.TrustGateway`. There is no agent bypass:
callers never contact external endpoints directly.
"""

from __future__ import annotations

from runtime.gateway.client import (
    BudgetExceeded,
    GatewayAuthError,
    GatewayCancelled,
    GatewayRateLimited,
    GatewaySSRFBlocked,
    GatewayTimeout,
    GatewayValidationError,
    TrustGateway,
)
from runtime.gateway.registry import ExternalAgentRegistry
from runtime.gateway.sessions import GatewaySessionStore
from runtime.gateway.schemas import GatewayMessage

__all__ = [
    "BudgetExceeded",
    "ExternalAgentRegistry",
    "GatewayAuthError",
    "GatewayCancelled",
    "GatewayMessage",
    "GatewayRateLimited",
    "GatewaySSRFBlocked",
    "GatewayTimeout",
    "GatewayValidationError",
    "GatewaySessionStore",
    "TrustGateway",
]
