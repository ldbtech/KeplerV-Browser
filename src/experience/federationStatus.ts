// Federation identity/verification display helpers: signature status derived
// from verification verdicts, and fake-vs-live labeling for classifier copy.
// The backend remains the source of truth; these only format its labels.

import type { VerificationResultInfo } from "../types";

/** Mirrors runtime/merchants/trusted_catalog.py ATTESTATION_ALGORITHM. */
export const FEDERATION_SIGNATURE_ALGORITHM = "ed25519-v1";

export interface SignatureStatus {
  state: "verified" | "none";
  algorithm?: string;
  evidenceRefs: string[];
}

/** A verdict counts as signed when an attestation ref verified. */
export function isAttestedVerdict(verdict: VerificationResultInfo): boolean {
  if (verdict.verdict === "cryptographically-attested") return true;
  return (verdict.evidence_refs ?? []).some((ref) => ref.startsWith("attestation:verified("));
}

/** Aggregate signature status across every verdict's evidence refs. */
export function signatureStatusFromVerdicts(verdicts: VerificationResultInfo[]): SignatureStatus {
  const refs: string[] = [];
  let algorithm: string | undefined;
  for (const verdict of verdicts ?? []) {
    for (const ref of verdict.evidence_refs ?? []) {
      if (!ref.startsWith("attestation:verified(")) continue;
      refs.push(ref);
      const match = /^attestation:verified\(([^)]+)\)$/.exec(ref.trim());
      if (match) algorithm = match[1];
    }
  }
  if (!refs.length) return { state: "none", evidenceRefs: [] };
  return { state: "verified", algorithm: algorithm ?? FEDERATION_SIGNATURE_ALGORITHM, evidenceRefs: refs };
}

export interface ClassifierDescription {
  live: boolean;
  short: string;
  detail: string;
}

/**
 * Fake-vs-live reading of a backend classifier/analysis label. Backend labels
 * carry a `fake-*` or `live-*` prefix (deterministic rules, test adapters, or
 * the env-gated live path); unknown labels fail closed to not-live.
 */
export function describeClassifierLabel(label: string): ClassifierDescription {
  const text = (label ?? "").trim();
  if (text.startsWith("live-")) {
    return {
      live: true,
      short: "LIVE MODEL",
      detail: `${text}. A live model judgment — still advisory, never proof on its own.`,
    };
  }
  const short = text.startsWith("fake-") ? "FAKE / RULES" : "NOT LIVE";
  return {
    live: false,
    short,
    detail: text
      ? `${text}. Checks run over the recorded transcript — not a live-model judgment.`
      : "No classifier label was reported; treat this as unchecked.",
  };
}
