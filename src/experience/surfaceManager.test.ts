import { describe, expect, it } from "vitest";
import {
  activeSurface,
  applyMediaCommand,
  closeAllSurfaces,
  createSurfaceState,
  duckForSpeech,
  LOCAL_DEMO_VIDEO_URL,
  NETFLIX_FALLBACK_LABEL,
  probeNetflix,
  probeYouTubeEmbed,
  requestFocus,
  resolveMediaTarget,
  youtubeEmbedUrl,
} from "./surfaceManager";

describe("surface manager single-active policy", () => {
  it("starts with no active surface", () => {
    expect(activeSurface(createSurfaceState())).toBeNull();
  });

  it("grants focus to one surface and pauses the previously audible one", () => {
    let state = createSurfaceState();
    const first = requestFocus(state, "local-video");
    expect(first.focused).toBe("local-video");
    expect(first.paused).toEqual([]);
    expect(activeSurface(first.state)).toBe("local-video");

    const second = requestFocus(first.state, "youtube");
    expect(second.focused).toBe("youtube");
    expect(second.paused).toEqual(["local-video"]);
    expect(second.state["local-video"]).toEqual({ active: false, paused: true });
    expect(second.state.youtube).toEqual({ active: true, paused: false });
    expect(activeSurface(second.state)).toBe("youtube");
  });

  it("reports pause order deterministically across surfaces", () => {
    const state = {
      "local-video": { active: true, paused: false },
      youtube: { active: false, paused: true },
      "netflix-fallback": { active: false, paused: true },
    };
    const plan = requestFocus(state, "netflix-fallback");
    expect(plan.paused).toEqual(["local-video"]);
    expect(activeSurface(plan.state)).toBe("netflix-fallback");
  });

  it("ducks all audible surfaces for voice coexistence", () => {
    const focused = requestFocus(createSurfaceState(), "youtube").state;
    const { state, ducked } = duckForSpeech(focused);
    expect(ducked).toEqual(["youtube"]);
    expect(state.youtube).toEqual({ active: true, paused: true });
    expect(duckForSpeech(state).ducked).toEqual([]);
  });

  it("closes every surface", () => {
    const focused = requestFocus(createSurfaceState(), "local-video").state;
    expect(activeSurface(closeAllSurfaces(focused))).toBeNull();
  });
});

describe("OPEN/PAUSE/RESUME/CLOSE_MEDIA plumbing", () => {
  it("opens, pauses, resumes, and closes media", () => {
    let state = createSurfaceState();
    let result = applyMediaCommand(state, "OPEN", "local-video");
    expect(result.ok).toBe(true);
    state = result.state;
    expect(activeSurface(state)).toBe("local-video");

    result = applyMediaCommand(state, "PAUSE");
    expect(result.ok).toBe(true);
    state = result.state;
    expect(state["local-video"].paused).toBe(true);

    result = applyMediaCommand(state, "RESUME");
    expect(result.ok).toBe(true);
    state = result.state;
    expect(state["local-video"]).toEqual({ active: true, paused: false });

    result = applyMediaCommand(state, "CLOSE_MEDIA");
    expect(result.ok).toBe(true);
    expect(activeSurface(result.state)).toBeNull();
  });

  it("asks for clarification when OPEN has no target", () => {
    const result = applyMediaCommand(createSurfaceState(), "OPEN");
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/which media/i);
  });

  it("reports honestly when nothing is playing", () => {
    expect(applyMediaCommand(createSurfaceState(), "PAUSE").ok).toBe(false);
    expect(applyMediaCommand(createSurfaceState(), "RESUME").ok).toBe(false);
  });

  it("never claims embedded Netflix playback on open", () => {
    const result = applyMediaCommand(createSurfaceState(), "OPEN", "netflix-fallback");
    expect(result.ok).toBe(true);
    expect(result.message).toMatch(/cannot play inside/i);
  });
});

describe("provider capability detection", () => {
  it("detects YouTube embed capability via the official player", () => {
    expect(probeYouTubeEmbed({ iframeAllowed: true })).toEqual({
      embeddable: true,
      reason: "official youtube-nocookie iframe player",
    });
    const blocked = probeYouTubeEmbed({ iframeAllowed: false });
    expect(blocked.embeddable).toBe(false);
    expect(blocked.reason).toMatch(/iframe/i);
  });

  it("always reports Netflix as non-embeddable with the fallback label", () => {
    const probe = probeNetflix();
    expect(probe.embeddable).toBe(false);
    expect(probe.reason).toMatch(/DRM/i);
    expect(probe.reason).toMatch(/no workaround/i);
    expect(probe.fallbackLabel).toBe(NETFLIX_FALLBACK_LABEL);
    expect(NETFLIX_FALLBACK_LABEL).toMatch(/system browser/i);
  });

  it("builds official YouTube embed URLs and rejects malformed ids", () => {
    expect(youtubeEmbedUrl("AbC123_XyZ0")).toBe("https://www.youtube-nocookie.com/embed/AbC123_XyZ0?rel=0&enablejsapi=1");
    expect(youtubeEmbedUrl("not a video id!!")).toBeNull();
    expect(youtubeEmbedUrl("")).toBeNull();
    expect(youtubeEmbedUrl("https://youtube.com/watch?v=AbC123_XyZ0")).toBeNull();
  });

  it("serves the local demo from a bundled path", () => {
    expect(LOCAL_DEMO_VIDEO_URL).toBe("/media/keplerv-demo.mp4");
  });
});

describe("media target resolution", () => {
  it("maps spoken target words to surfaces", () => {
    expect(resolveMediaTarget("local video")).toBe("local-video");
    expect(resolveMediaTarget("YouTube")).toBe("youtube");
    expect(resolveMediaTarget("netflix")).toBe("netflix-fallback");
    expect(resolveMediaTarget(undefined)).toBeNull();
    expect(resolveMediaTarget("hulu")).toBeNull();
  });
});
