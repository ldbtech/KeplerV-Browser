"""Claim verification service (Phase 6 M5)."""

from __future__ import annotations

from runtime.verification.claims import (
    VERDICTS,
    verify_merchant_payload,
    verify_price_claim,
    verify_text_claim,
)

__all__ = [
    "VERDICTS",
    "verify_merchant_payload",
    "verify_price_claim",
    "verify_text_claim",
]
