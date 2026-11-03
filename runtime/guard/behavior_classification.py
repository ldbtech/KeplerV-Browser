"""Behavioral classification of external-agent exchanges (Phase 6 M6+M7).

Deterministic, observable-only rules over a gateway transcript plus claim
verdicts, catalog/ledger contradictions, and inbound threat scans. The
classifier labels *behavior*, never intent: it makes no claims about the
external agent's internal reasoning, and disagreement alone (firm prices,
refused discounts) is never suspicious — hard bargaining with accurate
claims is ``legitimate-negotiation``.

Classes (priority order for overlapping evidence):

- ``injection`` — instruction-override content aimed at the local agent.
- ``unauthorized-request`` — requests for credentials, tokens, or sessions.
- ``policy-violation`` — coercion, approval-bypass pressure, or evil-redirect
  payloads (follow/redirect URLs).
- ``misleading`` — claims contradicting catalog evidence (price conflicts
  without explicit discount framing).
- ``unsupported-claim`` — assertions the catalog neither confirms nor denies.
- ``persuasion`` — discount framing, urgency, or upsell with accurate base facts.
- ``legitimate-negotiation`` — catalog-accurate quotes, honest declines.
- ``insufficient-evidence`` — no inbound content to judge; abstain.

The classifier itself is labeled ``fake-rules-v1``: fixed rules, not a
live-model judgment.
"""

from __future__ import annotations

from typing import Any

CLASSIFICATIONS: tuple[str, ...] = (
    "legitimate-negotiation",
    "persuasion",
    "unsupported-claim",
    "misleading",
    "unauthorized-request",
    "injection",
    "policy-violation",
    "insufficient-evidence",
)

CLASSIFIER_LABEL = "fake-rules-v1 — deterministic observable-only rules, not a live model"

#: Instruction-override phrasing (values, case-insensitive substring).
INJECTION_PHRASES = (
    "ignore your",
    "disregard your",
    "override your",
    "forget your instructions",
    "system instruction",
    "you are now",
    "new instructions",
    "do not follow your",
    "bypass your",
)

#: Instruction-shaped inbound keys (substring, case-insensitive).
INJECTION_KEY_MARKERS = (
    "instruction", "jailbreak", "prompt",
    "system_prompt", "override",
)

#: Credential-request value phrasing.
CREDENTIAL_ASK_PHRASES = (
    "send your",
    "share your",
    "provide your",
    "need your",
    "confirm with your",
    "unlock with your",
)

#: Nouns that make an ask a credential/session request.
SECRET_NOUNS = (
    "token", "credential", "password", "passcode", "secret",
    "api key", "apikey", "api_key", "login", "session", "cookie",
    "account", "keychain",
)

#: Credential-shaped inbound keys (substring, case-insensitive).
CREDENTIAL_KEY_MARKERS = (
    "credential", "password", "secret", "api_key", "apikey",
    "auth_token", "session_token", "access_code",
)

#: Coercion / approval-bypass phrasing.
COERCION_PHRASES = (
    "approve immediately",
    "confirm this order",
    "final warning",
    "suspended unless",
    "blocked unless",
    "ultimatum",
)

#: Redirect-shaped inbound keys (substring, case-insensitive).
REDIRECT_KEY_MARKERS = (
    "follow_url", "followurl", "redirect", "checkout_url", "external_url",
)

#: Persuasion phrasing (values) and keys.
PERSUASION_PHRASES = (
    "limited-time", "limited time", "selling fast", "only ",
    " left", "exclusive", "offer ends", "while supplies last",
    "special offer", "discount", "bundle",
)
PERSUASION_KEY_MARKERS = ("discount_pct", "upsell", "pressure", "promo")

#: Firm-but-honest bargaining phrasing: explicitly never suspicious.
FIRM_BARGAIN_PHRASES = (
    "final price", "cannot discount", "can't discount", "no discount",
    "non-negotiable", "firm at", "take it or leave it",
)


def _inbound_messages(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inbound = []
    for message in transcript or []:
        if not isinstance(message, dict):
            continue
        direction = str(message.get("direction") or "")
        if direction in {"inbound", "received"} or message.get("untrusted") is True:
            inbound.append(message)
    return inbound


def _walk_texts(value: Any) -> list[tuple[str, str]]:
    """Return (path, text) pairs for every string in a payload."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = str(key)
            if isinstance(item, str):
                found.append((path, item))
            else:
                found.extend((f"{path}.{sub}", text) for sub, text in _walk_texts(item))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                found.append((str(index), item))
            else:
                found.extend((f"{index}.{sub}", text) for sub, text in _walk_texts(item))
    return found


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def _has_marker(text: str, markers: tuple[str, ...]) -> str | None:
    lowered = text.casefold()
    for marker in markers:
        if marker in lowered:
            return marker
    return None


def _credential_ask(text: str) -> str | None:
    lowered = text.casefold()
    ask = next((phrase for phrase in CREDENTIAL_ASK_PHRASES if phrase in lowered), None)
    noun = next((word for word in SECRET_NOUNS if word in lowered), None)
    if ask and noun:
        return f"{ask}+{noun}"
    return None


def _scan_payload(payload: Any) -> dict[str, list[str]]:
    """Observable marker hits in one inbound payload (paths + matched markers)."""
    hits: dict[str, list[str]] = {
        "injection": [], "credential": [], "coercion": [],
        "redirect": [], "persuasion": [], "firm": [],
    }
    for path, text in _walk_texts(payload):
        if _has_marker(text, INJECTION_PHRASES):
            hits["injection"].append(f"{path}:{_has_marker(text, INJECTION_PHRASES)}")
        if _credential_ask(text):
            hits["credential"].append(f"{path}:{_credential_ask(text)}")
        if _has_marker(text, COERCION_PHRASES):
            hits["coercion"].append(f"{path}:{_has_marker(text, COERCION_PHRASES)}")
        firm_hit = _has_marker(text, FIRM_BARGAIN_PHRASES)
        if firm_hit:
            # Firm refusals ("cannot discount") are disagreement, not salesmanship.
            hits["firm"].append(f"{path}:{firm_hit}")
        elif _has_marker(text, PERSUASION_PHRASES):
            hits["persuasion"].append(f"{path}:{_has_marker(text, PERSUASION_PHRASES)}")
    for key in _walk_keys(payload):
        lowered = key.casefold()
        if any(marker in lowered for marker in INJECTION_KEY_MARKERS):
            # "system" alone is too broad (e.g. "ecosystem"); instruction-shaped
            # keys need an override family marker.
            hits["injection"].append(f"key:{key}")
        if any(marker in lowered for marker in CREDENTIAL_KEY_MARKERS):
            hits["credential"].append(f"key:{key}")
        if any(marker in lowered for marker in REDIRECT_KEY_MARKERS):
            hits["redirect"].append(f"key:{key}")
        if any(marker in lowered for marker in PERSUASION_KEY_MARKERS):
            hits["persuasion"].append(f"key:{key}")
    return hits


def _finding_hits(inbound_findings: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Map generic threat-scan findings onto marker families by kind substring."""
    hits: dict[str, list[str]] = {"injection": [], "credential": [], "coercion": [], "redirect": []}
    for finding in inbound_findings or []:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "").casefold()
        path = str(finding.get("path") or finding.get("message_id") or "?")
        if not kind.startswith("injection-marker:") and kind != "threat-shaped-key":
            continue
        detail = kind.split(":", 1)[1] if ":" in kind else path.casefold()
        if any(marker in detail for marker in ("ignore your", "system instruction")):
            hits["injection"].append(f"{path}:{detail}")
        elif "send your" in detail or "credential" in detail or "token" in detail or "password" in detail:
            hits["credential"].append(f"{path}:{detail}")
        elif any(word in detail for word in ("approve immediately", "confirm this order")):
            hits["coercion"].append(f"{path}:{detail}")
        elif kind == "threat-shaped-key":
            lowered_path = path.casefold()
            if any(marker in lowered_path for marker in ("instruction", "override", "jailbreak", "prompt")):
                hits["injection"].append(f"{path}:threat-shaped-key")
            elif any(marker in lowered_path for marker in ("credential", "token", "password", "secret")):
                hits["credential"].append(f"{path}:threat-shaped-key")
            elif any(marker in lowered_path for marker in ("follow_url", "followurl", "redirect", "location", "checkout")):
                hits["redirect"].append(f"{path}:threat-shaped-key")
            elif any(marker in lowered_path for marker in ("escalat", "approve", "authorize")):
                hits["coercion"].append(f"{path}:threat-shaped-key")
    return hits


def classify_exchange(
    transcript: list[dict[str, Any]] | None,
    *,
    verdicts: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    inbound_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify one external exchange; return class + rationale + evidence refs."""
    inbound = _inbound_messages(transcript or [])
    if not inbound:
        return {
            "classification": "insufficient-evidence",
            "rationale": "No inbound external content is available; abstaining rather than judging.",
            "evidence_refs": [],
            "observed_markers": {},
            "classifier_label": CLASSIFIER_LABEL,
            "abstained": True,
        }
    payload_hits: dict[str, list[str]] = {
        "injection": [], "credential": [], "coercion": [],
        "redirect": [], "persuasion": [], "firm": [],
    }
    message_refs = []
    for message in inbound:
        message_refs.append(str(message.get("message_id") or message.get("id") or "?"))
        for family, paths in _scan_payload(message.get("payload")).items():
            payload_hits[family].extend(f"{message_refs[-1]}:{path}" for path in paths)
    scan_hits = _finding_hits(inbound_findings)
    for family in ("injection", "credential", "coercion", "redirect"):
        payload_hits[family].extend(scan_hits[family])

    contradiction_kinds = [str(item.get("kind") or "") for item in contradictions or []]
    has_price_conflict = "catalog-price-conflict" in contradiction_kinds
    has_unsupported = "catalog-claim-unsupported" in contradiction_kinds
    verdict_values = [str(item.get("verdict") or "") for item in verdicts or []]
    has_statement_only = "statement-made" in verdict_values
    has_observed = any(value in {"independently-observed", "cryptographically-attested"}
                       for value in verdict_values)

    observed = {family: sorted(set(paths)) for family, paths in payload_hits.items() if paths}
    refs = [f"transcript:{ref}" for ref in message_refs]
    refs.extend(f"contradiction:{item.get('id', '?')}" for item in contradictions or [])

    def _result(classification: str, rationale: str) -> dict[str, Any]:
        return {
            "classification": classification,
            "rationale": rationale,
            "evidence_refs": refs,
            "observed_markers": observed,
            "classifier_label": CLASSIFIER_LABEL,
            "abstained": False,
        }

    if payload_hits["injection"]:
        return _result("injection",
                       "Inbound content carries instruction-override markers aimed at the local agent.")
    if payload_hits["credential"]:
        return _result("unauthorized-request",
                       "The external agent requests credentials, tokens, or session material "
                       "the local agent must never share.")
    if payload_hits["redirect"] or payload_hits["coercion"]:
        return _result("policy-violation",
                       "Inbound content pushes coercion, approval bypass, or off-market redirect "
                       "payloads outside the negotiation authority.")
    if has_price_conflict:
        return _result("misleading",
                       "Asserted prices contradict trusted catalog evidence without explicit "
                       "discount framing.")
    if has_unsupported or has_statement_only:
        return _result("unsupported-claim",
                       "The exchange asserts claims the trusted catalog neither confirms nor "
                       "denies; recorded as stated, not as fact.")
    if payload_hits["persuasion"]:
        return _result("persuasion",
                       "Discount framing, urgency, or upsell with no false claims against the catalog.")
    if has_observed or payload_hits["firm"]:
        return _result("legitimate-negotiation",
                       "Catalog-accurate quotes or honest bargaining positions; firm prices and "
                       "refused discounts are disagreement, not misconduct.")
    # Inbound content exists but nothing checkable or marked: abstain.
    return {
        "classification": "insufficient-evidence",
        "rationale": ("Inbound content carries no checkable claims and no behavioral markers; "
                      "abstaining rather than judging."),
        "evidence_refs": refs,
        "observed_markers": observed,
        "classifier_label": CLASSIFIER_LABEL,
        "abstained": True,
    }
