// Phase 5 M9 — Scenario A: local video end-to-end.
//
// Runnable journey through the real surface manager plus the bundled fixture
// on disk: asset presence -> OPEN -> voice ducking -> resume -> focus switch ->
// close. Asserts the shell's wiring contract (bundled URLs, captions) without
// claiming to verify actual <video> decoding — that still needs a running app.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  activeSurface,
  applyMediaCommand,
  createSurfaceState,
  duckForSpeech,
  LOCAL_DEMO_CAPTIONS_URL,
  LOCAL_DEMO_VIDEO_URL,
  NETFLIX_FALLBACK_LABEL,
  probeNetflix,
} from "./surfaceManager";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

describe("Scenario A: local video end-to-end", () => {
  it("ships the bundled fixture and captions the overlay references", () => {
    expect(LOCAL_DEMO_VIDEO_URL).toBe("/media/keplerv-demo.mp4");
    expect(LOCAL_DEMO_CAPTIONS_URL).toBe("/media/keplerv-demo.en.vtt");

    const videoPath = join(ROOT, "public", "media", "keplerv-demo.mp4");
    const header = readFileSync(videoPath);
    expect(header.length).toBeGreaterThan(10_000);
    // ISO Base Media magic: bytes 4..8 are the "ftyp" box type.
    expect(header.subarray(4, 8).toString("ascii")).toBe("ftyp");

    const captionsPath = join(ROOT, "public", "media", "keplerv-demo.en.vtt");
    const captions = readFileSync(captionsPath, "utf-8");
    expect(captions.startsWith("WEBVTT")).toBe(true);
    expect(captions).toMatch(/-->/);
  });

  it("opens the local video, ducks for speech, resumes, and closes", () => {
    let state = createSurfaceState();

    const opened = applyMediaCommand(state, "OPEN", "local-video");
    expect(opened.ok).toBe(true);
    expect(opened.message).toMatch(/local demo video/i);
    expect(activeSurface(opened.state)).toBe("local-video");
    expect(opened.state["local-video"]).toEqual({ active: true, paused: false });

    // Voice output must stay intelligible: speech pauses the audible surface.
    const ducked = duckForSpeech(opened.state);
    expect(ducked.ducked).toEqual(["local-video"]);
    expect(ducked.state["local-video"].paused).toBe(true);

    const resumed = applyMediaCommand(ducked.state, "RESUME");
    expect(resumed.ok).toBe(true);
    expect(resumed.state["local-video"]).toEqual({ active: true, paused: false });

    const paused = applyMediaCommand(resumed.state, "PAUSE");
    expect(paused.ok).toBe(true);
    expect(paused.state["local-video"].paused).toBe(true);

    const closed = applyMediaCommand(paused.state, "CLOSE_MEDIA", "local-video");
    expect(closed.ok).toBe(true);
    expect(activeSurface(closed.state)).toBeNull();
  });

  it("keeps single-audio-focus when switching to YouTube and back", () => {
    let state = applyMediaCommand(createSurfaceState(), "OPEN", "local-video").state;

    const toYouTube = applyMediaCommand(state, "OPEN", "youtube");
    expect(toYouTube.ok).toBe(true);
    expect(activeSurface(toYouTube.state)).toBe("youtube");
    expect(toYouTube.state["local-video"]).toEqual({ active: false, paused: true });

    const backToLocal = applyMediaCommand(toYouTube.state, "OPEN", "local-video");
    expect(activeSurface(backToLocal.state)).toBe("local-video");
    expect(backToLocal.state.youtube).toEqual({ active: false, paused: true });

    expect(activeSurface(applyMediaCommand(backToLocal.state, "CLOSE_MEDIA").state)).toBeNull();
  });

  it("asks which media to open instead of guessing, and never claims Netflix inside the app", () => {
    const noTarget = applyMediaCommand(createSurfaceState(), "OPEN");
    expect(noTarget.ok).toBe(false);
    expect(noTarget.message).toMatch(/which media/i);

    const probe = probeNetflix();
    expect(probe.embeddable).toBe(false);
    expect(probe.fallbackLabel).toBe(NETFLIX_FALLBACK_LABEL);

    const netflix = applyMediaCommand(createSurfaceState(), "OPEN", "netflix-fallback");
    expect(netflix.ok).toBe(true);
    expect(netflix.message).toMatch(/cannot play inside/i);
  });
});
