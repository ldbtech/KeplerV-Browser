"""Synthetic agentic internet (Phase 6 M3): four deterministic loopback merchants.

Fictional marketplace only: made-up merchant names, made-up products, and a
fictional currency with no monetary value. No real purchases, accounts, or
financial systems are involved. Every fixture surface is labeled ``fake`` /
``fixture``; gateway sessions to merchants must use the ``replay`` label.

The four profiles (separate identities, endpoints, capability descriptors,
histories) are deterministic scripted fixtures for repeatable tests:

- A cooperative: accurate quotes, respects order conditions.
- B competitive: aggressive but in-authority (discount ladder, upsell).
- C misleading: asserts catalog-false product claims.
- D adversarial: pushes the local agent toward unauthorized capabilities
  (injection text, credential requests, evil-redirect payloads). Its
  payloads are untrusted data and must never be followed.
"""

from __future__ import annotations

from runtime.merchants import adversarial, competitive, cooperative, misleading

MERCHANT_PROFILES = {
    cooperative.AGENT_ID: cooperative,
    competitive.AGENT_ID: competitive,
    misleading.AGENT_ID: misleading,
    adversarial.AGENT_ID: adversarial,
}

__all__ = ["MERCHANT_PROFILES", "adversarial", "competitive", "cooperative", "misleading"]
