"""Claim verification service (Phase 6 M5).

Distinguishes four evidence levels for merchant statements — and nothing
else. The service never invents evidence: every evidence reference in a
verdict was supplied by the caller (transcript ids, catalog checks the
service ran itself, attestation blobs presented for verification).

Verdicts:

- ``cryptographically-attested`` — a presented attestation verifies over
  the exact claim (fixture HMAC in tests; established primitives only).
- ``independently-observed`` — the claim matches the trusted catalog or
  another caller-supplied independent source.
- ``statement-made`` — the merchant said it (transcript statement ref) but
  no independent check applies; recorded as said, nothing more.
- ``unverified`` — no usable evidence, a failed attestation, or a conflict
  with the trusted catalog. Conflicts are reported honestly, never
  silently resolved.

Merchant-sourced text stays ``content_is_untrusted`` in every verdict.
"""

from __future__ import annotations

from typing import Any, Literal

from runtime.merchants import trusted_catalog

Verdict = Literal[
    "statement-made",
    "independently-observed",
    "cryptographically-attested",
    "unverified",
]

VERDICTS: tuple[str, ...] = (
    "statement-made",
    "independently-observed",
    "cryptographically-attested",
    "unverified",
)


def _verdict(
    *,
    claim: dict[str, Any],
    verdict: str,
    reason: str,
    evidence_refs: list[str],
    asserted_by: str,
) -> dict[str, Any]:
    return {
        "claim": dict(claim),
        "verdict": verdict,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "asserted_by": asserted_by,
        "content_is_untrusted": True,
    }


def verify_price_claim(
    *,
    product_id: str,
    asserted_price_credits: Any,
    asserted_by: str,
    statement_ref: str,
    attestation: dict[str, Any] | None = None,
    catalog_entries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify a merchant's asserted price for a catalog product."""
    claim = {
        "kind": "price",
        "product_id": product_id,
        "asserted_price_credits": asserted_price_credits,
    }
    if not statement_ref or not str(statement_ref).strip():
        return _verdict(
            claim=claim, verdict="unverified",
            reason="No transcript statement reference was supplied; nothing to verify.",
            evidence_refs=[], asserted_by=asserted_by,
        )
    refs = [str(statement_ref).strip()]
    if attestation is not None:
        try:
            canonical = trusted_catalog.canonical_price_claim(product_id, int(asserted_price_credits))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return _verdict(
                claim=claim, verdict="unverified",
                reason="The asserted value is not a usable price; attestation cannot apply.",
                evidence_refs=refs, asserted_by=asserted_by,
            )
        if trusted_catalog.verify_attestation(canonical, attestation):
            refs.append("attestation:verified(hmac-sha256-fixture-v1)")
            return _verdict(
                claim=claim, verdict="cryptographically-attested",
                reason="The presented attestation verifies over the exact asserted price.",
                evidence_refs=refs, asserted_by=asserted_by,
            )
        return _verdict(
            claim=claim, verdict="unverified",
            reason="The presented attestation failed verification; the price is unverified.",
            evidence_refs=refs, asserted_by=asserted_by,
        )
    check = trusted_catalog.check_price(product_id, asserted_price_credits, catalog_entries)
    if check["outcome"] == "match":
        refs.append(f"trusted-catalog:price-match:{product_id}")
        return _verdict(
            claim=claim, verdict="independently-observed",
            reason="The asserted price matches the trusted catalog entry.",
            evidence_refs=refs, asserted_by=asserted_by,
        )
    if check["outcome"] == "conflict":
        refs.append(f"trusted-catalog:price-conflict:{product_id}")
        return _verdict(
            claim=claim, verdict="unverified",
            reason=(
                "The asserted price conflicts with the trusted catalog "
                f"(trusted: {check['trusted_price']} credits); unverified."
            ),
            evidence_refs=refs, asserted_by=asserted_by,
        )
    if check["outcome"] == "unknown-product":
        return _verdict(
            claim=claim, verdict="statement-made",
            reason="The product is not in the trusted catalog, so no independent check applies; recorded as stated.",
            evidence_refs=refs, asserted_by=asserted_by,
        )
    return _verdict(
        claim=claim, verdict="unverified",
        reason="The asserted value is not a usable price; unverified.",
        evidence_refs=refs, asserted_by=asserted_by,
    )


def verify_text_claim(
    *,
    text: str,
    product_id: str,
    asserted_by: str,
    statement_ref: str,
    attestation: dict[str, Any] | None = None,
    catalog_entries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify a merchant's text claim (warranty, shipping, returns, ...)."""
    claim = {"kind": "text", "product_id": product_id, "text": text}
    if not statement_ref or not str(statement_ref).strip():
        return _verdict(
            claim=claim, verdict="unverified",
            reason="No transcript statement reference was supplied; nothing to verify.",
            evidence_refs=[], asserted_by=asserted_by,
        )
    refs = [str(statement_ref).strip()]
    if attestation is not None:
        canonical = trusted_catalog.canonical_text_claim(product_id, text)
        if trusted_catalog.verify_attestation(canonical, attestation):
            refs.append("attestation:verified(hmac-sha256-fixture-v1)")
            return _verdict(
                claim=claim, verdict="cryptographically-attested",
                reason="The presented attestation verifies over the exact asserted text.",
                evidence_refs=refs, asserted_by=asserted_by,
            )
        return _verdict(
            claim=claim, verdict="unverified",
            reason="The presented attestation failed verification; the claim is unverified.",
            evidence_refs=refs, asserted_by=asserted_by,
        )
    if trusted_catalog.supports_claim(text, catalog_entries):
        refs.append("trusted-catalog:claim-supported")
        return _verdict(
            claim=claim, verdict="independently-observed",
            reason="The trusted catalog supports this claim.",
            evidence_refs=refs, asserted_by=asserted_by,
        )
    return _verdict(
        claim=claim, verdict="unverified",
        reason="The trusted catalog does not support this claim; unverified.",
        evidence_refs=refs, asserted_by=asserted_by,
    )


def verify_merchant_payload(
    *,
    payload: dict[str, Any],
    product_id: str,
    asserted_by: str,
    statement_ref: str,
    catalog_entries: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Verify every checkable claim in a merchant response payload.

    Extracts price claims (``price_credits`` / ``total_credits``) and text
    claims (``claims`` list). Payloads with no checkable claims yield no
    verdicts — the service reports nothing rather than inventing checks.
    """
    verdicts: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return verdicts
    for key in ("price_credits", "total_credits"):
        if key in payload:
            verdicts.append(verify_price_claim(
                product_id=product_id, asserted_price_credits=payload.get(key),
                asserted_by=asserted_by, statement_ref=f"{statement_ref}#{key}",
                catalog_entries=catalog_entries,
            ))
    claims = payload.get("claims")
    if isinstance(claims, list):
        for index, text in enumerate(claims):
            if isinstance(text, str) and text.strip():
                verdicts.append(verify_text_claim(
                    text=text, product_id=product_id, asserted_by=asserted_by,
                    statement_ref=f"{statement_ref}#claims[{index}]",
                    catalog_entries=catalog_entries,
                ))
    return verdicts
