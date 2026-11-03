import type { ConnectedAccount, MailAccountRef } from "../types";

export type AccountResolution =
  | { kind: "resolved"; account: ConnectedAccount }
  | { kind: "none" }
  | { kind: "ambiguous"; candidates: ConnectedAccount[] };

/** Mirror of the runtime `resolve_account` rules: exact email wins, then
 * provider/email-substring filters; one candidate wins; several stay ambiguous
 * (never silently auto-picked); zero matches resolve to none. */
export function resolveAccount(
  accounts: ConnectedAccount[],
  ref: MailAccountRef,
): AccountResolution {
  const email = (ref.email ?? "").trim().toLowerCase();
  const provider = (ref.provider ?? "").trim().toLowerCase();
  if (email) {
    const exact = accounts.find(
      (item) =>
        item.email.toLowerCase() === email &&
        (!provider || item.provider.toLowerCase() === provider),
    );
    if (exact) return { kind: "resolved", account: exact };
  }
  const candidates = accounts.filter(
    (item) =>
      (!provider || item.provider.toLowerCase() === provider) &&
      (!email || item.email.toLowerCase().includes(email)),
  );
  if (candidates.length === 0) return { kind: "none" };
  if (candidates.length === 1) return { kind: "resolved", account: candidates[0] };
  return { kind: "ambiguous", candidates };
}

export function accountKey(account: ConnectedAccount): string {
  return `${account.provider}:${account.email.toLowerCase()}`;
}

export function parseAccountKey(key: string): MailAccountRef {
  const separator = key.indexOf(":");
  if (separator < 0) return { email: key };
  return { provider: key.slice(0, separator), email: key.slice(separator + 1) };
}
