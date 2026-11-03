from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from runtime.guard.case_manager import GuardCaseManager
from runtime.guard.evidence import GuardEvidenceService, bundle_size, trim_bundle_to_fit
from runtime.guard.parsing import ModelOutputError, generate_valid_json
from runtime.guard.schemas import ObserverScreening
from runtime.providers import ModelProviderError, ProviderRegistry
from runtime.store import RuntimeStore


class GuardScreeningError(RuntimeError):
    pass


class GuardScreener:
    """One contextual Muse Spark screen per deterministic rule-triggered case."""

    MAX_INPUT_CHARS = 18_000
    CALL_TIMEOUT_SECONDS = 120

    def __init__(self, store: RuntimeStore, providers: ProviderRegistry,
                 cases: GuardCaseManager | None = None,
                 evidence: GuardEvidenceService | None = None):
        self.store = store
        self.providers = providers
        self.cases = cases or GuardCaseManager(store)
        self.evidence = evidence or GuardEvidenceService(store, self.cases)
        self._case_locks: dict[str, asyncio.Lock] = {}

    async def screen(self, case_id: str) -> dict[str, Any]:
        if not self.cases.can_read(case_id, "guard_detective"):
            raise PermissionError("Muse Observer does not have read permission for this case.")
        lock = self._case_locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            case = self.cases.get_case(case_id)
            if not case:
                raise KeyError("Guard case not found.")
            previous = next((item for item in case["evidence"] if item["source_type"] == "guard_screening"), None)
            if previous:
                return previous["payload"]
            provider = self.providers.active_provider
            model = "muse-spark-1.3" if provider == "meta" else self.providers.active_model
            bundle = trim_bundle_to_fit(self.evidence.reconstruct_case(case_id), self.MAX_INPUT_CHARS,
                                               trigger_event_id=case["trigger_event_id"])
            serialized = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
            if bundle_size(bundle) > self.MAX_INPUT_CHARS:
                raise GuardScreeningError("The sanitized evidence bundle exceeds the Observer input limit.")
            allowed_ids = ({item["id"] for item in bundle["runtime_events"]}
                           | {item["id"] for item in bundle["case_evidence"]}
                           | {item["id"] for item in bundle["research_evidence"]})
            request_id = f"guard-screening-{uuid4()}"
            try:
                adapter = self.providers.get(provider)
                try:
                    screening, usage = await generate_valid_json(adapter, model, [
                        {"role": "system", "content": (
                            "You are KeplerV Guard's Muse Spark Observer. Deterministic AIP rules have already recorded an event. "
                            "Assess contextual relevance only; do not decide guilt, infer hidden intent, or authorize containment. "
                            "A denied action establishes an attempt and denial, not successful access. Cite supplied evidence IDs. "
                            "Return JSON only: {risk_level:low|moderate|elevated,summary,evidence_ids,rationale,uncertainties}. "
                            "Risk labels are qualitative and uncalibrated, not probabilities."
                        )},
                        {"role": "user", "content": serialized},
                    ], ObserverScreening.model_validate, timeout=self.CALL_TIMEOUT_SECONDS)
                except ModelOutputError as exc:
                    raise GuardScreeningError("Muse Spark returned an invalid screening result.") from exc
                unknown = set(screening.evidence_ids) - allowed_ids
                if unknown:
                    raise GuardScreeningError(
                        f"Muse Spark cited evidence not present in the case ({', '.join(sorted(unknown)[:4])}); "
                        "screening was rejected.")
                payload = {"qualitative_only": True, "provider": provider, "model": model,
                           "usage": usage, "screening": screening.model_dump(mode="json"),
                           "source_evidence_ids": sorted(set(screening.evidence_ids))}
                self.store.append_guard_evidence(case_id, "guard_screening", request_id, case["updated_at"], payload)
                run = self.store.get_run(case["run_id"])
                if run:
                    self.store.add_event(run, "guard.screening.completed", "success", {
                        "case_id": case_id, "risk_level": screening.risk_level,
                        "summary": screening.summary[:400], "provider": provider, "model": model,
                    })
                return payload
            except (ModelProviderError, GuardScreeningError, asyncio.TimeoutError, ValueError) as exc:
                message = str(exc) if not isinstance(exc, asyncio.TimeoutError) else "Muse Spark screening timed out."
                latest = self.cases.get_case(case_id)
                self.store.append_guard_evidence(case_id, "guard_screening_error", request_id, latest["updated_at"], {
                    "provider": provider, "model": model, "error": message[:500],
                })
                raise GuardScreeningError(message) from exc
