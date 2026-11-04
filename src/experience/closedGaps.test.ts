// Closed-gap UX surfaces: Guard auto-containment setting + undo rules,
// mail send-approval detail + provider status, federation signature display.
// Pure helpers plus mocked-invoke boundary tests (no live calls, no secrets).

import { describe, expect, it, vi } from "vitest";
import {
  AUTO_CONTAINMENT_DEFAULT,
  describeAutoContainmentMode,
  fetchAutoContainmentMode,
  isAutoContainmentMode,
  saveAutoContainmentMode,
  undoActionFor,
} from "./autoContainment";
import {
  accountCanSend,
  describeMailSendApproval,
  GMAIL_SEND_SCOPE,
  isMailSendTool,
  OUTLOOK_SEND_SCOPE,
  providerMailStatuses,
} from "./mailSendApproval";
import {
  describeClassifierLabel,
  FEDERATION_SIGNATURE_ALGORITHM,
  isAttestedVerdict,
  signatureStatusFromVerdicts,
} from "./federationStatus";
import type { ConnectedAccount, VerificationResultInfo } from "../types";

function verdict(overrides: Partial<VerificationResultInfo> = {}): VerificationResultInfo {
  return {
    claim: { id: "claim-1", kind: "price", summary: "Acme Widget costs $9", message_id: "m-1", statement_ref: "s-1" },
    verdict: "unverified",
    reason: "no evidence",
    evidence_refs: [],
    ...overrides,
  };
}

describe("auto-containment mode setting", () => {
  it("defaults to pause-only and recognizes the three modes", () => {
    expect(AUTO_CONTAINMENT_DEFAULT).toBe("pause-only");
    expect(isAutoContainmentMode("off")).toBe(true);
    expect(isAutoContainmentMode("pause-only")).toBe(true);
    expect(isAutoContainmentMode("full")).toBe(true);
    expect(isAutoContainmentMode("panic")).toBe(false);
    expect(isAutoContainmentMode("")).toBe(false);
  });

  it("explains each mode without claiming policy changes", () => {
    for (const mode of ["off", "pause-only", "full"]) {
      const copy = describeAutoContainmentMode(mode);
      expect(copy.label.length).toBeGreaterThan(0);
      expect(copy.detail.length).toBeGreaterThan(0);
      expect(`${copy.label} ${copy.detail}`.toLowerCase()).not.toMatch(/aip policy (changed|modified|updated)/);
    }
    expect(describeAutoContainmentMode("off").detail).toMatch(/never acts on its own/i);
    expect(describeAutoContainmentMode("pause-only").detail).toMatch(/pauses the agent/i);
    expect(describeAutoContainmentMode("full").detail).toMatch(/revoke capabilities/i);
  });

  it("fetches the mode through the Tauri boundary", async () => {
    const invokeFn = vi.fn().mockResolvedValue({ mode: "pause-only", modes: ["off", "pause-only", "full"], default: "pause-only" });
    const status = await fetchAutoContainmentMode(invokeFn);
    expect(invokeFn).toHaveBeenCalledWith("runtime_guard_auto_containment");
    expect(status.mode).toBe("pause-only");
  });

  it("rejects a malformed mode payload without touching the bridge twice", async () => {
    const invokeFn = vi.fn().mockResolvedValue({ nope: true });
    await expect(fetchAutoContainmentMode(invokeFn)).rejects.toThrow(/unavailable/i);
    expect(invokeFn).toHaveBeenCalledTimes(1);
  });

  it("saves one explicit mode and notifies exactly once", async () => {
    const invokeFn = vi.fn().mockResolvedValue({ mode: "full" });
    const notify = vi.fn();
    const saved = await saveAutoContainmentMode(invokeFn, "full", notify);
    expect(saved).toBe("full");
    expect(invokeFn).toHaveBeenCalledWith("runtime_guard_auto_containment_set", { mode: "full" });
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][0]).toMatch(/Full/i);
  });

  it("refuses an invalid mode without invoking anything", async () => {
    const invokeFn = vi.fn();
    const notify = vi.fn();
    await expect(saveAutoContainmentMode(invokeFn, "panic", notify)).rejects.toThrow();
    expect(invokeFn).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it("surfaces bridge failures through the notice, not a false claim", async () => {
    const invokeFn = vi.fn().mockRejectedValue(new Error("Runtime is not running."));
    const notify = vi.fn();
    await expect(saveAutoContainmentMode(invokeFn, "off", notify)).rejects.toThrow(/not running/);
    expect(notify).toHaveBeenCalledWith("Runtime is not running.");
  });
});

describe("auto-containment undo rules", () => {
  it("inverts pause/deny/isolate through the existing user-authorized actions", () => {
    expect(undoActionFor("pause_agent")).toEqual({
      action: "resume_agent",
      label: "Resume agent",
      note: expect.stringMatching(/pause/i),
    });
    const deny = undoActionFor("deny_pending");
    expect(deny?.action).toBe("resume_agent");
    expect(deny?.note).toMatch(/stay denied/i);
    expect(undoActionFor("isolate_browser")?.action).toBe("restore_browser");
  });

  it("has no silent undo for revocation or unknown actions", () => {
    expect(undoActionFor("revoke_capability")).toBeNull();
    expect(undoActionFor("restore_browser")).toBeNull();
    expect(undoActionFor("")).toBeNull();
  });
});

describe("mail send-approval surfacing", () => {
  const gmail: ConnectedAccount = {
    provider: "gmail",
    email: "demo.person@example.test",
    scopes: ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly", GMAIL_SEND_SCOPE],
    expires_at: 0,
    status: "connected",
  };
  const readOnlyGmail: ConnectedAccount = { ...gmail, scopes: ["openid", "email"] };
  const fake: ConnectedAccount = { provider: "fake-mail", email: "fixture@example.test", scopes: [], expires_at: 0, status: "connected" };

  it("recognizes both send tool names", () => {
    expect(isMailSendTool("gmail.send")).toBe(true);
    expect(isMailSendTool("mail.send")).toBe(true);
    expect(isMailSendTool("mail.read")).toBe(false);
    expect(isMailSendTool("browser.click")).toBe(false);
  });

  it("detects the send scope per provider without touching secrets", () => {
    expect(accountCanSend("gmail", [GMAIL_SEND_SCOPE])).toBe(true);
    expect(accountCanSend("gmail", ["openid"])).toBe(false);
    expect(accountCanSend("outlook", [OUTLOOK_SEND_SCOPE])).toBe(true);
    expect(accountCanSend("outlook", ["Mail.Read"])).toBe(false);
    expect(accountCanSend("fake-mail", [])).toBe(false);
  });

  it("summarizes a live send with recipient, subject, account, and preview", () => {
    const summary = describeMailSendApproval(
      { provider: "gmail", email: gmail.email, to: "registrar@example.edu", subject: "Transcript request", body: "Hello, …" },
      [gmail, fake],
    );
    expect(summary).toMatchObject({ to: "registrar@example.edu", subject: "Transcript request", live: true, canSend: true });
    expect(summary.via).toContain(gmail.email);
    expect(summary.bodyPreview).toContain("Hello");
  });

  it("labels the fake fixture as not-live even with the same shape", () => {
    const summary = describeMailSendApproval(
      { provider: "fake-mail", to: "a@example.test", subject: "s", body: "b" },
      [gmail, fake],
    );
    expect(summary.live).toBe(false);
    expect(summary.canSend).toBe(false);
  });

  it("stays unknown when no account resolves, and truncates long bodies", () => {
    const summary = describeMailSendApproval({ to: "a@example.test", subject: "s", body: "x".repeat(500) }, []);
    expect(summary.live).toBeNull();
    expect(summary.via).toBe("default account");
    expect(summary.bodyPreview.length).toBeLessThan(500);
    expect(summary.bodyPreview.endsWith("…")).toBe(true);
  });

  it("marks read-only accounts as unable to send", () => {
    const summary = describeMailSendApproval(
      { provider: "gmail", email: gmail.email, to: "a@example.test", subject: "s", body: "b" },
      [readOnlyGmail],
    );
    expect(summary.live).toBe(true);
    expect(summary.canSend).toBe(false);
  });

  it("reports one status row per provider, including not-connected ones", () => {
    const rows = providerMailStatuses([gmail]);
    expect(rows.map((row) => row.provider)).toEqual(["gmail", "outlook", "fake-mail"]);
    expect(rows[0]).toMatchObject({ connected: true, fake: false, canSend: true, email: gmail.email });
    expect(rows[1]).toMatchObject({ connected: false, fake: false, canSend: false });
    expect(rows[2]).toMatchObject({ connected: false, fake: true });
  });
});

describe("federation identity/verification display", () => {
  it("pins the Ed25519 algorithm label shared with the backend", () => {
    expect(FEDERATION_SIGNATURE_ALGORITHM).toBe("ed25519-v1");
  });

  it("treats attested verdicts as signed, everything else as unsigned", () => {
    expect(isAttestedVerdict(verdict({ verdict: "cryptographically-attested" }))).toBe(true);
    expect(isAttestedVerdict(verdict({ verdict: "supported", evidence_refs: ["attestation:verified(ed25519-v1)"] }))).toBe(true);
    expect(isAttestedVerdict(verdict({ verdict: "supported", evidence_refs: ["catalog:text-match"] }))).toBe(false);
    expect(isAttestedVerdict(verdict())).toBe(false);
  });

  it("aggregates signature status and keeps the algorithm explicit", () => {
    expect(signatureStatusFromVerdicts([])).toEqual({ state: "none", evidenceRefs: [] });
    expect(signatureStatusFromVerdicts([verdict()]).state).toBe("none");
    const status = signatureStatusFromVerdicts([
      verdict({ verdict: "cryptographically-attested", evidence_refs: ["attestation:verified(ed25519-v1)"] }),
    ]);
    expect(status).toEqual({
      state: "verified",
      algorithm: "ed25519-v1",
      evidenceRefs: ["attestation:verified(ed25519-v1)"],
    });
  });

  it("labels fake-rules output as not-live and live output as live", () => {
    const fake = describeClassifierLabel("fake-rules-v1 — deterministic checks, not a live-model judgment");
    expect(fake.live).toBe(false);
    expect(fake.short).toMatch(/fake/i);
    expect(fake.detail).toMatch(/not a live-model judgment/i);
    const live = describeClassifierLabel("live-muse-v1 — model judgment via the configured provider (env-gated)");
    expect(live.live).toBe(true);
    expect(live.short).toMatch(/live/i);
  });

  it("fails closed on unknown or missing labels", () => {
    expect(describeClassifierLabel("mystery-v9").live).toBe(false);
    expect(describeClassifierLabel("").live).toBe(false);
    expect(describeClassifierLabel("").detail).toMatch(/unchecked/i);
  });
});
