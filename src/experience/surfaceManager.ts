// Experience Surface Manager: one persistent Intent Space, one audible surface.
// Only one media surface may hold audio focus at a time. All state transitions
// are pure functions so the audio-focus policy is unit-testable; the Svelte
// shell applies the returned focus plan to real <video>/<iframe> elements.

import type { CapabilityResult, MediaCommand, MediaFocusState, MediaSurfaceId } from "../types";

export const MEDIA_SURFACES: MediaSurfaceId[] = ["local-video", "youtube", "netflix-fallback"];

export const NETFLIX_FALLBACK_LABEL = "Opens in system browser — embedded Netflix not supported";
export const NETFLIX_URL = "https://www.netflix.com/";
export const LOCAL_DEMO_VIDEO_URL = "/media/keplerv-demo.mp4";
export const YOUTUBE_VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;

export function createSurfaceState(): MediaFocusState {
  return {
    "local-video": { active: false, paused: true },
    youtube: { active: false, paused: true },
    "netflix-fallback": { active: false, paused: true },
  };
}

export function activeSurface(state: MediaFocusState): MediaSurfaceId | null {
  return MEDIA_SURFACES.find((id) => state[id].active) ?? null;
}

export interface FocusPlan {
  state: MediaFocusState;
  /** Surfaces that lost focus and must be paused/stopped, in deterministic order. */
  paused: MediaSurfaceId[];
  focused: MediaSurfaceId;
}

/** Grant audio focus to `id`, pausing every other surface (single-active policy). */
export function requestFocus(state: MediaFocusState, id: MediaSurfaceId): FocusPlan {
  const next = createSurfaceState();
  const paused: MediaSurfaceId[] = [];
  for (const surface of MEDIA_SURFACES) {
    if (surface === id) {
      next[surface] = { active: true, paused: false };
    } else {
      next[surface] = { active: false, paused: true };
      if (state[surface].active && !state[surface].paused) paused.push(surface);
    }
  }
  return { state: next, paused, focused: id };
}

export interface DuckPlan {
  state: MediaFocusState;
  /** Audible surfaces paused so voice output stays intelligible. */
  ducked: MediaSurfaceId[];
}

/** Pause all audible surfaces for voice coexistence (TTS capture/announce). */
export function duckForSpeech(state: MediaFocusState): DuckPlan {
  const next: MediaFocusState = structuredClone(state);
  const ducked: MediaSurfaceId[] = [];
  for (const surface of MEDIA_SURFACES) {
    if (next[surface].active && !next[surface].paused) {
      next[surface] = { ...next[surface], paused: true };
      ducked.push(surface);
    }
  }
  return { state: next, ducked };
}

export function pauseSurface(state: MediaFocusState, id: MediaSurfaceId): MediaFocusState {
  if (!state[id].active) return state;
  return { ...state, [id]: { ...state[id], paused: true } };
}

export function resumeSurface(state: MediaFocusState, id: MediaSurfaceId): FocusPlan {
  return requestFocus(state, id);
}

export function closeSurface(state: MediaFocusState, id: MediaSurfaceId): MediaFocusState {
  return { ...state, [id]: { active: false, paused: true } };
}

export function closeAllSurfaces(state: MediaFocusState): MediaFocusState {
  void state;
  return createSurfaceState();
}

export interface MediaCommandResult {
  state: MediaFocusState;
  /** Human-readable outcome for speech/notice; never claims unverified playback. */
  message: string;
  ok: boolean;
}

/** OPEN/PAUSE/RESUME/CLOSE_MEDIA plumbing shared by voice intents and UI buttons. */
export function applyMediaCommand(
  state: MediaFocusState,
  command: MediaCommand,
  target?: MediaSurfaceId | null,
): MediaCommandResult {
  switch (command) {
    case "OPEN": {
      if (!target) return { state, ok: false, message: "Which media should I open: local video, YouTube, or Netflix?" };
      const plan = requestFocus(state, target);
      return { state: plan.state, ok: true, message: mediaOpenedMessage(target) };
    }
    case "PAUSE": {
      const id = target ?? activeSurface(state);
      if (!id) return { state, ok: false, message: "No media is playing." };
      return { state: pauseSurface(state, id), ok: true, message: `${surfaceLabel(id)} paused.` };
    }
    case "RESUME": {
      const id = target ?? activeSurface(state);
      if (!id) return { state, ok: false, message: "No media is open to resume." };
      const plan = resumeSurface(state, id);
      return { state: plan.state, ok: true, message: `${surfaceLabel(id)} resumed.` };
    }
    case "CLOSE_MEDIA": {
      if (target) return { state: closeSurface(state, target), ok: true, message: `${surfaceLabel(target)} closed.` };
      return { state: closeAllSurfaces(state), ok: true, message: "Media closed." };
    }
  }
}

export function surfaceLabel(id: MediaSurfaceId): string {
  switch (id) {
    case "local-video": return "Local video";
    case "youtube": return "YouTube";
    case "netflix-fallback": return "Netflix";
  }
}

function mediaOpenedMessage(id: MediaSurfaceId): string {
  switch (id) {
    case "local-video": return "Opening the local demo video.";
    case "youtube": return "Opening YouTube in the media surface.";
    case "netflix-fallback": return "Netflix cannot play inside KeplerV. Opening the external-browser option.";
  }
}

/** Capability probe for the official YouTube iframe player. */
export function probeYouTubeEmbed(env: { iframeAllowed: boolean }): CapabilityResult {
  if (!env.iframeAllowed) {
    return { embeddable: false, reason: "iframe embeds are disabled in this context" };
  }
  return { embeddable: true, reason: "official youtube-nocookie iframe player" };
}

/**
 * Capability probe for Netflix. Embedded playback is never claimed: Widevine
 * DRM inside a Tauri webview is unsupported, so this probe always reports
 * non-embeddable and the UI must offer the labeled external-browser fallback.
 */
export function probeNetflix(): CapabilityResult {
  return {
    embeddable: false,
    reason: "DRM-protected provider: embedded playback inside KeplerV is unsupported and no workaround is attempted",
    fallbackLabel: NETFLIX_FALLBACK_LABEL,
  };
}

/**
 * Official YouTube embed URL (privacy-enhanced host + JS API so the surface
 * manager can pause it for audio focus). Returns null for bad IDs.
 */
export function youtubeEmbedUrl(videoId: string): string | null {
  const id = videoId.trim();
  if (!YOUTUBE_VIDEO_ID_PATTERN.test(id)) return null;
  return `https://www.youtube-nocookie.com/embed/${id}?rel=0&enablejsapi=1`;
}

/** Resolve a spoken/button target word to a surface id. */
export function resolveMediaTarget(word: string | undefined): MediaSurfaceId | null {
  if (!word) return null;
  const value = word.trim().toLowerCase();
  if (["local", "video", "local video", "local-video", "demo", "file"].includes(value)) return "local-video";
  if (["youtube", "you tube", "yt"].includes(value)) return "youtube";
  if (["netflix", "net flix"].includes(value)) return "netflix-fallback";
  return null;
}
