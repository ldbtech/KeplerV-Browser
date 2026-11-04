import { describe, expect, it, vi } from "vitest";
import {
  INTENT_SPACE_RETURN_NOTE,
  NETFLIX_FALLBACK_EXPLANATION,
  NETFLIX_FALLBACK_LABEL,
  openMediaExternally,
  planExternalOpen,
  probeNetflix,
  SYSTEM_BROWSER_HOSTS,
} from "./surfaceManager";

const NETFLIX_ITEM = {
  provider: "Netflix",
  player: "external",
  url: "https://www.netflix.com/title/81234567",
  title: "Demo Documentary",
};

describe("Netflix capability detect -> explanation -> fallback", () => {
  it("detects non-embeddable and labels the system-browser fallback", () => {
    const probe = probeNetflix();
    expect(probe.embeddable).toBe(false);
    expect(probe.fallbackLabel).toBe(NETFLIX_FALLBACK_LABEL);
    expect(NETFLIX_FALLBACK_LABEL).toMatch(/system browser/i);
  });

  it("explains DRM without claiming embedded playback or a workaround", () => {
    expect(NETFLIX_FALLBACK_EXPLANATION).toMatch(/DRM/i);
    expect(NETFLIX_FALLBACK_EXPLANATION).toMatch(/cannot play inside/i);
    expect(NETFLIX_FALLBACK_EXPLANATION).toMatch(/never attempts a workaround/i);
    expect(NETFLIX_FALLBACK_EXPLANATION).not.toMatch(/playing in KeplerV|plays inside KeplerV|watch here/i);
  });

  it("keeps the return-to-Intent-Space context in the success copy", () => {
    expect(INTENT_SPACE_RETURN_NOTE).toMatch(/intent space/i);
    expect(INTENT_SPACE_RETURN_NOTE).toMatch(/return/i);
    expect(planExternalOpen(NETFLIX_ITEM).successNotice).toContain(INTENT_SPACE_RETURN_NOTE);
  });

  it("covers both Netflix hosts on the UI allowlist", () => {
    expect(SYSTEM_BROWSER_HOSTS).toContain("netflix.com");
    expect(SYSTEM_BROWSER_HOSTS).toContain("www.netflix.com");
  });
});

describe("external-open routing", () => {
  it("routes Netflix titles to the validated system-browser command", () => {
    const plan = planExternalOpen(NETFLIX_ITEM);
    expect(plan.target).toEqual({ command: "open_system_browser", args: { url: NETFLIX_ITEM.url } });
    expect(plan.blockedNotice).toBeNull();
    expect(plan.successNotice).toMatch(/system browser/i);
  });

  it("routes Netflix URLs to the fallback even without the provider tag", () => {
    const plan = planExternalOpen({ ...NETFLIX_ITEM, provider: "Video" });
    expect(plan.target?.command).toBe("open_system_browser");
  });

  it("routes ordinary https sources to the separate in-app window (not the system browser)", () => {
    const plan = planExternalOpen({
      provider: "NPS",
      player: "web",
      url: "https://www.nps.gov/subjects/climatechange/index.htm",
      title: "Field guide",
    });
    expect(plan.target).toEqual({
      command: "open_public_resource",
      args: { url: "https://www.nps.gov/subjects/climatechange/index.htm", label: "Field guide" },
    });
    expect(plan.successNotice).toMatch(/separate window/i);
    expect(plan.successNotice).not.toMatch(/system browser/i);
  });

  it("blocks non-https documents without invoking anything", () => {
    const plan = planExternalOpen({
      provider: "PDF",
      player: "pdf",
      url: "/artifacts/notes.pdf",
      title: "Notes",
    });
    expect(plan.target).toBeNull();
    expect(plan.blockedNotice).toMatch(/download or save a copy/i);
  });
});

describe("fallback flow up to the invoke boundary (headless)", () => {
  it("invokes open_system_browser once and only then claims the system browser", async () => {
    const invokeFn = vi.fn(async () => undefined);
    const notices: string[] = [];
    await openMediaExternally(NETFLIX_ITEM, invokeFn, (message) => notices.push(message));
    expect(invokeFn).toHaveBeenCalledTimes(1);
    expect(invokeFn).toHaveBeenCalledWith("open_system_browser", { url: NETFLIX_ITEM.url });
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatch(/system browser/i);
    expect(notices[0]).toContain(INTENT_SPACE_RETURN_NOTE);
  });

  it("surfaces backend rejection without claiming success", async () => {
    const invokeFn = vi.fn(async () => {
      throw new Error("This address is not in the system-browser allowlist.");
    });
    const notices: string[] = [];
    await openMediaExternally(NETFLIX_ITEM, invokeFn, (message) => notices.push(message));
    expect(invokeFn).toHaveBeenCalledTimes(1);
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatch(/allowlist/);
    expect(notices[0]).not.toMatch(/opened .* in the system browser/i);
  });

  it("never invokes for blocked documents", async () => {
    const invokeFn = vi.fn(async () => undefined);
    const notices: string[] = [];
    await openMediaExternally(
      { provider: "PDF", player: "pdf", url: "/artifacts/notes.pdf", title: "Notes" },
      invokeFn,
      (message) => notices.push(message),
    );
    expect(invokeFn).not.toHaveBeenCalled();
    expect(notices).toHaveLength(1);
  });
});
