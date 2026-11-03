// Phase 6 M8: pure helpers for external-agent graph nodes, session detail,
// and voice inspection answers. Everything here reads records fetched from
// the runtime (gateway overview / transcript / verification); nothing is
// invented, and secret-shaped values are never surfaced — only field names.
import type {
  ClaimRecord,
  ExternalAgentInfo,
  GatewayMessageInfo,
  GatewaySessionInfo,
  SessionVerification,
} from "../types";

export type TierTone = "trusted" | "declared" | "unknown" | "suspect";

export interface TierBadge {
  label: string;
  tone: TierTone;
  explanation: string;
}

const TIER_BADGES: Record<string, TierBadge> = {
  verified: {
    label: "VERIFIED",
    tone: "trusted",
    explanation: "Identity verified against a trusted record.",
  },
  self_declared: {
    label: "SELF-DECLARED",
    tone: "declared",
    explanation: "The agent asserts this identity itself; nothing independently confirms it.",
  },
  unknown: {
    label: "UNKNOWN",
    tone: "unknown",
    explanation: "No identity information is available for this agent.",
  },
  suspected: {
    label: "SUSPECTED",
    tone: "suspect",
    explanation: "Past exchanges gave reason to distrust this agent.",
  },
};

export function tierBadge(status: string): TierBadge {
  return TIER_BADGES[status] ?? TIER_BADGES.unknown;
}

export interface ExternalNode {
  agentId: string;
  displayName: string;
  sessionIds: string[];
  openSessions: number;
  badge: TierBadge;
  operatorHost: string;
  position: number;
}

/** Operator organization, derived from the endpoint host only (no lookups). */
export function operatorHost(endpoint: string): string {
  try {
    return new URL(endpoint).hostname || "unlisted operator";
  } catch {
    return "unlisted operator";
  }
}

export function shortAgentName(agentId: string): string {
  return agentId
    .replace(/^merchant-/, "")
    .split("-")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

/** One restrained graph node per external agent with sessions (cap keeps the graph calm). */
export function buildExternalNodes(
  sessions: GatewaySessionInfo[],
  agents: ExternalAgentInfo[],
  maxNodes = 3,
): ExternalNode[] {
  const byId = new Map(agents.map((agent) => [agent.agent_id, agent]));
  const grouped = new Map<string, GatewaySessionInfo[]>();
  for (const session of sessions) {
    const list = grouped.get(session.external_agent_id) ?? [];
    list.push(session);
    grouped.set(session.external_agent_id, list);
  }
  // Most recently active agents first.
  const ordered = [...grouped.entries()].sort((a, b) => {
    const aLatest = Math.max(...a[1].map((item) => Date.parse(item.updated_at) || 0));
    const bLatest = Math.max(...b[1].map((item) => Date.parse(item.updated_at) || 0));
    return bLatest - aLatest;
  });
  return ordered.slice(0, maxNodes).map(([agentId, items], index) => {
    const record = byId.get(agentId);
    return {
      agentId,
      displayName: shortAgentName(agentId),
      sessionIds: items.map((item) => item.id),
      openSessions: items.filter((item) => item.status === "open").length,
      badge: tierBadge(record?.identity_status ?? "unknown"),
      operatorHost: operatorHost(record?.endpoint ?? items[0]?.endpoint ?? ""),
      position: index,
    };
  });
}

export function graphExternalY(index: number, total: number): number {
  if (total <= 1) return 30;
  return 16 + (index / Math.max(total - 1, 1)) * 28;
}

const SECRET_KEY_PARTS = [
  "password",
  "secret",
  "token",
  "credential",
  "api_key",
  "apikey",
  "keychain",
  "cookie",
  "session",
];

function isSecretKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SECRET_KEY_PARTS.some((part) => lowered.includes(part));
}

function walkKeys(value: unknown, trail: string, found: string[]): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkKeys(item, `${trail}${index}.`, found));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (isSecretKey(key)) found.push(`${trail}${key}`);
      else walkKeys(item, `${trail}${key}.`, found);
    }
  }
}

/** Credential-shaped field names in a transcript (names only — values never leave the record). */
export function redactedCredentialFields(transcript: GatewayMessageInfo[]): string[] {
  const found: string[] = [];
  for (const message of transcript) {
    const names: string[] = [];
    walkKeys(message.payload ?? {}, "", names);
    for (const name of names) found.push(`${message.message_id}:${name}`);
  }
  return [...new Set(found)];
}

/** Claims asserted by inbound messages: priced offers plus text claims. */
export function extractClaims(transcript: GatewayMessageInfo[]): ClaimRecord[] {
  const claims: ClaimRecord[] = [];
  for (const message of transcript) {
    if (message.direction !== "inbound" && message.direction !== "received") continue;
    const payload = (message.payload ?? {}) as Record<string, unknown>;
    const productId = typeof payload.product_id === "string" ? payload.product_id : undefined;
    for (const key of ["price_credits", "total_credits"]) {
      const price = payload[key];
      if (typeof price === "number" && Number.isInteger(price)) {
        claims.push({
          id: `${message.message_id}#${key}`,
          kind: "price",
          summary: `${price} fictional credits${productId ? ` for ${productId}` : ""}`,
          product_id: productId,
          message_id: message.message_id,
          statement_ref: `transcript:${message.message_id}`,
        });
      }
    }
    if (Array.isArray(payload.claims)) {
      payload.claims.forEach((text, index) => {
        if (typeof text === "string" && text.trim()) {
          claims.push({
            id: `${message.message_id}#claims[${index}]`,
            kind: "text",
            summary: text.slice(0, 160),
            product_id: productId,
            message_id: message.message_id,
            statement_ref: `transcript:${message.message_id}`,
          });
        }
      });
    }
  }
  return claims;
}

export interface PricedOffer {
  sessionId: string;
  agentId: string;
  productId: string;
  price: number;
  messageId: string;
}

export function latestPricedOffer(
  session: GatewaySessionInfo,
  transcript: GatewayMessageInfo[],
): PricedOffer | null {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const message = transcript[index];
    if (message.direction !== "inbound" && message.direction !== "received") continue;
    const payload = (message.payload ?? {}) as Record<string, unknown>;
    const price = payload.price_credits ?? payload.total_credits;
    const productId = payload.product_id;
    if (typeof price === "number" && Number.isInteger(price) && typeof productId === "string") {
      return {
        sessionId: session.id,
        agentId: session.external_agent_id,
        productId,
        price,
        messageId: message.message_id,
      };
    }
  }
  return null;
}

export function resolveSessionTarget(
  id: string | null | undefined,
  sessions: GatewaySessionInfo[],
  agents: ExternalAgentInfo[],
): { session?: GatewaySessionInfo; agent?: ExternalAgentInfo } {
  if (!id) return {};
  const session = sessions.find((item) => item.id === id);
  if (session) {
    return { session, agent: agents.find((item) => item.agent_id === session.external_agent_id) };
  }
  const agent = agents.find((item) => item.agent_id === id);
  if (agent) {
    const latest = [...sessions]
      .filter((item) => item.external_agent_id === id)
      .sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0))[0];
    return { session: latest, agent };
  }
  return {};
}

export function summarizeSessionForSpeech(
  session: GatewaySessionInfo,
  agent: ExternalAgentInfo | undefined,
  verification: SessionVerification | null,
): string {
  const name = shortAgentName(session.external_agent_id);
  const badge = tierBadge(agent?.identity_status ?? "unknown").label.toLowerCase().replaceAll("_", " ").replaceAll("-", " ");
  const rounds = Math.max(session.messages_sent, session.messages_received);
  const state = session.status === "open" ? "open" : session.status;
  let text = `${name}, identity ${badge}, session ${state} with ${rounds} message${rounds === 1 ? "" : "s"}.`;
  if (verification) {
    const verdicts = verification.verdicts.map((item) => item.verdict);
    const unverified = verdicts.filter((item) => item === "unverified").length;
    const attested = verdicts.filter(
      (item) => item === "cryptographically-attested" || item === "independently-observed",
    ).length;
    text += ` ${verification.verdicts.length} claims checked: ${attested} confirmed, ${unverified} unverified.`;
    if (verification.classification && !verification.abstained) {
      text += ` Classified as ${verification.classification.classification.replaceAll("-", " ")}.`;
    } else if (verification.abstained) {
      text += " Evidence is too thin to classify.";
    }
  }
  return text;
}

export function describeVerificationForSpeech(verification: SessionVerification): string {
  if (!verification.verdicts.length) return "No checkable claims arrived in this session, so there is nothing to verify.";
  const parts = verification.verdicts.map((item) => {
    const what = item.claim.kind === "price" ? `the price ${item.claim.summary}` : `the claim ${item.claim.summary}`;
    const verdict = item.verdict.replaceAll("-", " ");
    return `${what}: ${verdict}`;
  });
  return parts.join(" ") + ` Evidence: ${verification.verdicts[0].evidence_refs.join(", ") || "none recorded"}.`;
}

export function whyFlaggedForSpeech(
  verification: SessionVerification,
  transcript: GatewayMessageInfo[],
): string {
  const bits: string[] = [];
  for (const item of verification.contradictions) bits.push(item.detail);
  for (const item of verification.inbound_findings.slice(0, 3)) {
    bits.push(`flagged ${item.path} (${item.kind})`);
  }
  const secrets = redactedCredentialFields(transcript);
  if (secrets.length) bits.push(`${secrets.length} credential-shaped fields were redacted, never shared`);
  if (!bits.length) {
    if (verification.abstained) return "This session was not flagged. Evidence is too thin to judge it.";
    return "This session was not flagged. Its claims match the records so far.";
  }
  return `Flagged because: ${bits.join("; ")}.`;
}

export function compareOffersForSpeech(offers: PricedOffer[]): string {
  if (!offers.length) return "None of the sessions carry a priced offer to compare.";
  const ranked = [...offers].sort((a, b) => a.price - b.price);
  const parts = ranked.map(
    (item) => `${shortAgentName(item.agentId)} asks ${item.price} fictional credits for ${item.productId}`,
  );
  const cheapest = ranked[0];
  return `${parts.join("; ")}. Cheapest is ${shortAgentName(cheapest.agentId)} at ${cheapest.price}.`;
}
