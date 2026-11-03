"""Guard investigation of external-agent exchanges (Phase 6 M6+M7).

Pure analysis over a gateway transcript plus ledger events and the trusted
catalog: a sanitized transcript for the Detective, contradiction detection,
claim verdicts, behavioral classification, and follow-up questions.

Structural guarantees:

- The investigator takes *data* (transcript rows, ledger events), never a
  gateway handle, so it cannot contact the external agent. Follow-up
  questions are returned as payloads labeled ``issue_via: gateway.send``
  for the local agent to ask through the Trust Gateway — and each one is
  pre-checked by the gateway data-minimization scrub.
- No claims about the external agent's internal reasoning: the report
  records observable behavior only, with explicit uncertainty and
  abstention when evidence is thin.

The analysis is labeled ``fake-rules-v1``: deterministic checks, not a
live-model judgment.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from runtime.guard.behavior_classification import CLASSIFIER_LABEL, classify_exchange
from runtime.merchants import trusted_catalog
from runtime.negotiation.driver import scan_inbound
from runtime.verification import claims as claim_service

ANALYSIS_LABEL = "fake-rules-v1 — deterministic checks, not a live-model judgment"

INVESTIGATION_EVENT = "gateway.external_investigation"
CONTRADICTION_EVENT = "gateway.external_contradiction"

#: Secret-shaped key substrings redacted from Detective-bound transcripts
#: (same redaction semantics as the Guard evidence bundle).
_SECRET_KEY_PARTS = (
    "password", "secret", "token", "authorization", "cookie",
    "credential", "api_key", "apikey", "keychain", "session",
)

_MAX_TEXT_CHARS = 1200
_MAX_FOLLOW_UPS = 3


def _is_secret_key(key: str) -> bool:
    lowered = str(key).casefold()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _sanitize_value(value: Any, key: str = "") -> Any:
    if _is_secret_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k)[:80]: _sanitize_value(v, str(k)) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [_sanitize_value(item, key) for item in value[:50]]
    if isinstance(value, str):
        return value[:_MAX_TEXT_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:300]


def _redacted_fields(value: Any, trail: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{trail}{key}"
            if _is_secret_key(key):
                found.append(path)
            else:
                found.extend(_redacted_fields(item, f"{path}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_redacted_fields(item, f"{trail}{index}."))
    return found


def sanitize_transcript_for_detective(
    transcript: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Sanitize gateway transcript rows for Detective consumption.

    Secrets are redacted (field names listed, values dropped); everything
    else is preserved with its untrusted marking so findings stay traceable
    to exact message ids.
    """
    sanitized: list[dict[str, Any]] = []
    for message in transcript or []:
        if not isinstance(message, dict):
            continue
        payload = message.get("payload", {})
        sanitized.append({
            "message_id": str(message.get("message_id") or message.get("id") or "?"),
            "reply_to": message.get("reply_to"),
            "direction": str(message.get("direction") or "?"),
            "sender_kind": ("external" if message.get("direction") in {"inbound", "received"}
                            or message.get("untrusted") is True else "local"),
            "kind": str(message.get("kind") or "?"),
            "label": str(message.get("label") or "?"),
            "untrusted": bool(message.get("untrusted", False)),
            "payload": _sanitize_value(payload),
            "redacted_fields": _redacted_fields(payload),
            "created_at": str(message.get("created_at") or ""),
        })
    return sanitized


def _payload_texts(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            texts.extend(_payload_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_payload_texts(item))
    elif isinstance(value, str):
        texts.append(value)
    return texts


def _has_discount_framing(payload: dict[str, Any], trusted_price: Any) -> bool:
    """Whether a below-catalog price is framed as an explicit discount offer.

    Competitive haggling (list price == catalog + explicit discount percent)
    is legitimate persuasion, not a false claim. A bare below-catalog price
    with no such framing contradicts the catalog.
    """
    list_price = payload.get("list_price_credits")
    if list_price is not None and list_price == trusted_price:
        return True
    discount = payload.get("discount_pct")
    if isinstance(discount, (int, float)) and not isinstance(discount, bool):
        if 0 <= discount <= 90 and trusted_price is not None:
            return True
    lowered = " ".join(_payload_texts(payload)).casefold()
    return "discount" in lowered and "list price" in lowered


def detect_contradictions(
    transcript: list[dict[str, Any]] | None,
    *,
    catalog_entries: dict[str, dict[str, Any]] | None = None,
    ledger_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Detect contradictions vs the trusted catalog and the event ledger.

    Catalog checks compare asserted prices/claims against trusted entries;
    ledger checks verify reply integrity (unknown reply targets, duplicate
    message ids with differing bytes, evidence refs to missing ledger
    events). Discount ladders are *not* self-contradictions: a falling offer
    sequence is legitimate negotiation, and flagging it would punish
    disagreement rather than deception.
    """
    contradictions: list[dict[str, Any]] = []
    rows = [m for m in transcript or [] if isinstance(m, dict)]
    known_ids = {str(m.get("message_id") or m.get("id") or "?") for m in rows}
    seen_payloads: dict[str, str] = {}
    ledger_ids = {str(event.get("id") or "") for event in ledger_events or []
                  if isinstance(event, dict)}

    def _add(kind: str, detail: str, refs: list[str]) -> None:
        contradictions.append({
            "id": f"contra-{uuid4().hex[:10]}",
            "kind": kind,
            "detail": detail,
            "evidence_refs": refs,
        })

    for message in rows:
        message_id = str(message.get("message_id") or message.get("id") or "?")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            continue
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        if message_id in seen_payloads and seen_payloads[message_id] != digest:
            _add("ledger-contradiction",
                 f"Message id '{message_id}' appears twice with differing bytes.",
                 [f"transcript:{message_id}"])
        seen_payloads.setdefault(message_id, digest)
        reply_to = message.get("reply_to")
        if reply_to and str(reply_to) not in known_ids:
            _add("ledger-contradiction",
                 f"Message '{message_id}' replies to unknown message '{reply_to}'.",
                 [f"transcript:{message_id}"])
        for ref_key in ("evidence_ref", "statement_ref", "ledger_ref"):
            ref_value = payload.get(ref_key)
            if isinstance(ref_value, str) and ref_value.startswith("evt-") \
                    and ref_value not in ledger_ids:
                _add("ledger-contradiction",
                     f"Message '{message_id}' cites missing ledger event '{ref_value}'.",
                     [f"transcript:{message_id}"])
        is_inbound = message.get("direction") in {"inbound", "received"} \
            or message.get("untrusted") is True
        if not is_inbound:
            continue
        product_id = payload.get("product_id")
        if isinstance(product_id, str) and product_id:
            for price_key in ("price_credits", "total_credits"):
                if price_key not in payload:
                    continue
                check = trusted_catalog.check_price(
                    product_id, payload[price_key], catalog_entries)
                if check["outcome"] == "conflict" and not _has_discount_framing(
                        payload, check["trusted_price"]):
                    _add("catalog-price-conflict",
                         f"Message '{message_id}' asserts {payload[price_key]} credits for "
                         f"'{product_id}' vs trusted {check['trusted_price']} (no discount framing).",
                         [f"transcript:{message_id}",
                          f"trusted-catalog:price-conflict:{product_id}"])
            raw_claims = payload.get("claims")
            if isinstance(raw_claims, list):
                for text in raw_claims:
                    if isinstance(text, str) and text.strip() \
                            and not trusted_catalog.supports_claim(text, catalog_entries):
                        _add("catalog-claim-unsupported",
                             f"Message '{message_id}' asserts a claim the trusted catalog "
                             "does not support.",
                             [f"transcript:{message_id}"])
    return contradictions


def investigate_external_exchange(
    *,
    session: dict[str, Any],
    transcript: list[dict[str, Any]] | None,
    ledger_events: list[dict[str, Any]] | None = None,
    catalog_entries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the full external-exchange investigation (pure; performs no I/O).

    No gateway handle is accepted, so follow-up questions can only be
    returned as data for the local agent to issue through the Trust
    Gateway. The report never describes the external agent's internal
    reasoning — that is explicitly marked unknown.
    """
    from runtime.gateway.tools import scrub_outbound_payload

    session_id = str(session.get("id") or session.get("session_id") or "?")
    external_agent_id = str(session.get("external_agent_id") or "?")
    rows = [m for m in transcript or [] if isinstance(m, dict)]

    sanitized = sanitize_transcript_for_detective(rows)
    contradictions = detect_contradictions(
        rows, catalog_entries=catalog_entries, ledger_events=ledger_events or [])

    verdicts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for message in rows:
        is_inbound = message.get("direction") in {"inbound", "received"} \
            or message.get("untrusted") is True
        if not is_inbound:
            continue
        message_id = str(message.get("message_id") or message.get("id") or "?")
        payload = message.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("product_id"), str):
            verdicts.extend(claim_service.verify_merchant_payload(
                payload=payload, product_id=payload["product_id"],
                asserted_by=external_agent_id,
                statement_ref=f"session:{session_id}:msg:{message_id}",
                catalog_entries=catalog_entries,
            ))
        for finding in scan_inbound(payload):
            findings.append({**finding, "message_id": message_id})

    classification = classify_exchange(
        rows, verdicts=verdicts, contradictions=contradictions,
        inbound_findings=findings)

    follow_ups: list[dict[str, Any]] = []
    for contradiction in contradictions[:_MAX_FOLLOW_UPS]:
        subject = ("price evidence" if contradiction["kind"] == "catalog-price-conflict"
                   else "supporting evidence" if contradiction["kind"] == "catalog-claim-unsupported"
                   else "message integrity")
        product_id = "unknown"
        for ref in contradiction["evidence_refs"]:
            if ref.startswith("trusted-catalog:price-conflict:"):
                product_id = ref.split(":", 2)[2]
        payload = scrub_outbound_payload({
            "action": "evidence",
            "product_id": product_id,
            "subject": subject,
            "question_ref": contradiction["id"],
        })
        follow_ups.append({
            "issue_via": "gateway.send",
            "payload": payload,
            "answers_contradiction": contradiction["id"],
            "rationale": ("Ask the external agent for supporting evidence through the "
                          "Trust Gateway; its reply is untrusted data and can only add a "
                          "statement-level record, never proof by itself."),
        })

    uncertainties = [
        "External-agent internal reasoning is unobservable from the transcript; "
        "no claims are made about intent, beliefs, or plans.",
    ]
    if any(v["verdict"] == "statement-made" for v in verdicts):
        uncertainties.append("Some claims have no independent check; they are recorded as stated.")
    if any(v["verdict"] == "unverified" for v in verdicts):
        uncertainties.append("Some claims failed verification; the report does not resolve why.")
    if classification["classification"] == "insufficient-evidence":
        uncertainties.append("Evidence is too thin to classify; this report abstains.")

    return {
        "session_id": session_id,
        "external_agent_id": external_agent_id,
        "analysis_label": ANALYSIS_LABEL,
        "classifier_label": CLASSIFIER_LABEL,
        "sanitized_transcript": sanitized,
        "contradictions": contradictions,
        "verdicts": verdicts,
        "inbound_findings": findings,
        "classification": classification,
        "follow_up_questions": follow_ups,
        "uncertainties": uncertainties,
        "internal_reasoning": "unknown — external agent internals are unobservable; no claims made.",
        "abstained": bool(classification.get("abstained", False)),
    }


def record_investigation(
    store,
    run: dict[str, Any],
    report: dict[str, Any],
    *,
    guard_observer=None,
) -> list[dict[str, Any]]:
    """Record an investigation report as ledger events; flag contradictions.

    Always writes ``gateway.external_investigation``; when contradictions
    were found, also writes ``gateway.external_contradiction`` and notifies
    Guard for review.
    """
    from runtime.gateway.events import record_gateway_event

    session_id = str(report.get("session_id") or "?")
    classification = report.get("classification") or {}
    contradictions = report.get("contradictions") or []
    events = [record_gateway_event(store, run, INVESTIGATION_EVENT, "success", {
        "summary": (f"Investigated external exchange {session_id}: "
                    f"{classification.get('classification', '?')} "
                    f"({len(contradictions)} contradiction(s))."),
        "session_id": session_id,
        "external_agent_id": report.get("external_agent_id"),
        "classification": classification.get("classification"),
        "contradiction_count": len(contradictions),
        "follow_up_count": len(report.get("follow_up_questions") or []),
        "abstained": bool(report.get("abstained", False)),
        "analysis_label": ANALYSIS_LABEL,
    })]
    if contradictions:
        events.append(record_gateway_event(store, run, CONTRADICTION_EVENT, "flagged", {
            "summary": (f"{len(contradictions)} contradiction(s) vs catalog/ledger "
                        f"in session {session_id}."),
            "session_id": session_id,
            "external_agent_id": report.get("external_agent_id"),
            "contradictions": [
                {"id": item.get("id"), "kind": item.get("kind"),
                 "detail": str(item.get("detail") or "")[:500]}
                for item in contradictions
            ],
            "content_is_untrusted": True,
        }, guard_observer=guard_observer))
    return events
