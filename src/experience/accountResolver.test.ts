import { describe, expect, it } from "vitest";
import type { ConnectedAccount } from "../types";
import { accountKey, parseAccountKey, resolveAccount } from "./accountResolver";

const gmailAda: ConnectedAccount = { provider: "gmail", email: "ada@example.test", scopes: ["gmail.readonly"], expires_at: 9999999999, status: "connected" };
const gmailGrace: ConnectedAccount = { provider: "gmail", email: "grace@example.test", scopes: ["gmail.readonly"], expires_at: 9999999999, status: "connected" };
const fakeDemo: ConnectedAccount = { provider: "fake-mail", email: "demo.person@example.test", scopes: ["fake-mail.read"], expires_at: 9999999999, status: "connected" };

describe("resolveAccount", () => {
  it("resolves an exact email match", () => {
    const result = resolveAccount([gmailAda, gmailGrace], { email: "Ada@Example.Test" });
    expect(result.kind).toBe("resolved");
    if (result.kind === "resolved") expect(result.account.email).toBe("ada@example.test");
  });

  it("resolves the single account when no reference is given", () => {
    const result = resolveAccount([fakeDemo], {});
    expect(result).toEqual({ kind: "resolved", account: fakeDemo });
  });

  it("resolves by provider when only one account uses it", () => {
    const result = resolveAccount([gmailAda, fakeDemo], { provider: "fake-mail" });
    expect(result).toEqual({ kind: "resolved", account: fakeDemo });
  });

  it("stays ambiguous across several matching accounts instead of auto-picking", () => {
    const result = resolveAccount([gmailAda, gmailGrace], { provider: "gmail" });
    expect(result.kind).toBe("ambiguous");
    if (result.kind === "ambiguous") {
      expect(result.candidates.map((item) => item.email).sort()).toEqual(["ada@example.test", "grace@example.test"]);
    }
  });

  it("stays ambiguous with no reference when several accounts exist", () => {
    expect(resolveAccount([gmailAda, fakeDemo], {}).kind).toBe("ambiguous");
  });

  it("resolves nothing when no account matches", () => {
    expect(resolveAccount([gmailAda], { email: "nobody@example.test" })).toEqual({ kind: "none" });
  });

  it("resolves nothing when no account is connected", () => {
    expect(resolveAccount([], {})).toEqual({ kind: "none" });
  });

  it("matches an email substring to a single account", () => {
    const result = resolveAccount([gmailAda, fakeDemo], { email: "demo.person" });
    expect(result).toEqual({ kind: "resolved", account: fakeDemo });
  });
});

describe("accountKey", () => {
  it("round-trips through parseAccountKey", () => {
    expect(parseAccountKey(accountKey(gmailAda))).toEqual({ provider: "gmail", email: "ada@example.test" });
  });
});
