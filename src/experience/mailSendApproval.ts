// Mail send-approval UX helpers: per-provider live status and the approval
// detail shown in the existing goal-panel approval flow. Secrets never enter
// here — only metadata (provider, address, scopes, draft fields).

import type { ConnectedAccount } from "../types";

/** Mirrors runtime/connectors/gmail.py GMAIL_SEND_SCOPE. */
export const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";
/** Mirrors runtime/connectors/outlook.py OUTLOOK_SEND_SCOPE. */
export const OUTLOOK_SEND_SCOPE = "Mail.Send";

export const MAIL_SEND_TOOLS = ["gmail.send", "mail.send"] as const;

export function isMailSendTool(tool: string): boolean {
  return (MAIL_SEND_TOOLS as readonly string[]).includes(tool);
}

/** Whether the account's granted scopes include the provider's send scope. */
export function accountCanSend(provider: string, scopes: string[]): boolean {
  if (provider === "gmail") return scopes.includes(GMAIL_SEND_SCOPE);
  if (provider === "outlook") return scopes.includes(OUTLOOK_SEND_SCOPE);
  return false;
}

export interface MailSendSummary {
  to: string;
  subject: string;
  bodyPreview: string;
  via: string;
  /** True for live providers, false for the fake fixture, null when unknown. */
  live: boolean | null;
  canSend: boolean;
}

/** Resolve an approval's send arguments against connected accounts for display. */
export function describeMailSendApproval(
  args: Record<string, unknown>,
  accounts: ConnectedAccount[],
): MailSendSummary {
  const to = typeof args.to === "string" && args.to.trim() ? args.to.trim().slice(0, 256) : "(no recipient)";
  const subject =
    typeof args.subject === "string" && args.subject.trim() ? args.subject.trim().slice(0, 300) : "(no subject)";
  const body = typeof args.body === "string" ? args.body : "";
  const bodyPreview = body.length > 280 ? `${body.slice(0, 280)}…` : body || "(empty body)";
  const provider = typeof args.provider === "string" ? args.provider : undefined;
  const email = typeof args.email === "string" ? args.email : undefined;
  const account =
    accounts.find(
      (item) =>
        (provider === undefined || item.provider === provider) && (email === undefined || item.email === email),
    ) ?? (accounts.length === 1 ? accounts[0] : undefined);
  if (!account) {
    return { to, subject, bodyPreview, via: "default account", live: null, canSend: false };
  }
  const live = account.provider !== "fake-mail";
  return {
    to,
    subject,
    bodyPreview,
    via: `${account.email} (${account.provider})`,
    live,
    canSend: live && accountCanSend(account.provider, account.scopes),
  };
}

export interface ProviderMailStatus {
  provider: string;
  label: string;
  connected: boolean;
  email?: string;
  status?: string;
  fake: boolean;
  canSend: boolean;
}

/** One status row per mail provider, including the not-connected ones. */
export function providerMailStatuses(accounts: ConnectedAccount[]): ProviderMailStatus[] {
  const labels: Record<string, string> = { gmail: "Gmail", outlook: "Outlook", "fake-mail": "Fake Mail (test fixture)" };
  return ["gmail", "outlook", "fake-mail"].map((provider) => {
    const account = accounts.find((item) => item.provider === provider);
    if (!account) {
      return { provider, label: labels[provider], connected: false, fake: provider === "fake-mail", canSend: false };
    }
    return {
      provider,
      label: labels[provider],
      connected: true,
      email: account.email,
      status: account.status,
      fake: provider === "fake-mail",
      canSend: accountCanSend(provider, account.scopes),
    };
  });
}
