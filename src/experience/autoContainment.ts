// Guard automatic-containment UX helpers: mode setting copy, undo mapping,
// and the thin Tauri invoke drivers (backend boundary stays in Rust).
// Pure functions so the policy copy and undo rules are unit-testable.

export const AUTO_CONTAINMENT_MODES = ["off", "pause-only", "full"] as const;
export type AutoContainmentMode = (typeof AUTO_CONTAINMENT_MODES)[number];
export const AUTO_CONTAINMENT_DEFAULT: AutoContainmentMode = "pause-only";

export interface AutoContainmentStatus {
  mode: string;
  modes: string[];
  default: string;
}

export function isAutoContainmentMode(value: string): value is AutoContainmentMode {
  return (AUTO_CONTAINMENT_MODES as readonly string[]).includes(value);
}

export function describeAutoContainmentMode(mode: string): { label: string; detail: string } {
  switch (mode) {
    case "off":
      return {
        label: "Off — every action needs your approval",
        detail: "Guard never acts on its own. Each containment step waits for your explicit approval.",
      };
    case "full":
      return {
        label: "Full — pause, isolate, and narrow capabilities",
        detail:
          "On high-risk signals Guard may automatically deny pending actions, isolate the browser, " +
          "and revoke capabilities. Every automatic step stays visible below and can be overridden.",
      };
    case "pause-only":
    default:
      return {
        label: "Pause only — stop first, ask before more",
        detail:
          "On high-risk signals Guard automatically denies pending actions and pauses the agent. " +
          "Isolating the browser or revoking capabilities still needs your approval.",
      };
  }
}

export interface UndoPlan {
  action: "resume_agent" | "restore_browser";
  label: string;
  note: string;
}

/**
 * Inverse of one automatic containment action, driven through the existing
 * user-authorized containment flow (still confirmed + recorded). Returns null
 * when the action cannot be undone — revoked capabilities need a fresh run.
 */
export function undoActionFor(action: string): UndoPlan | null {
  switch (action) {
    case "pause_agent":
      return { action: "resume_agent", label: "Resume agent", note: "Undoes the automatic pause." };
    case "deny_pending":
      return {
        action: "resume_agent",
        label: "Resume agent",
        note: "Resumes the agent. Denied approvals stay denied.",
      };
    case "isolate_browser":
      return {
        action: "restore_browser",
        label: "Restore browser session",
        note: "Re-opens the agent's isolated browser session.",
      };
    default:
      return null;
  }
}

/** Read the current mode through the existing Tauri bridge (metadata only). */
export async function fetchAutoContainmentMode(
  invokeFn: (command: string, args?: Record<string, unknown>) => Promise<unknown>,
): Promise<AutoContainmentStatus> {
  const result = (await invokeFn("runtime_guard_auto_containment")) as AutoContainmentStatus;
  if (!result || typeof result.mode !== "string") {
    throw new Error("Guard auto-containment status was unavailable.");
  }
  return result;
}

/** Persist one explicit mode choice; the notice callback fires exactly once. */
export async function saveAutoContainmentMode(
  invokeFn: (command: string, args?: Record<string, unknown>) => Promise<unknown>,
  mode: string,
  notify: (message: string) => void,
): Promise<string> {
  if (!isAutoContainmentMode(mode)) {
    const message = "Choose off, pause-only, or full.";
    notify(message);
    throw new Error(message);
  }
  try {
    const result = (await invokeFn("runtime_guard_auto_containment_set", { mode })) as { mode: string };
    const saved = result?.mode ?? mode;
    notify(`Automatic containment: ${describeAutoContainmentMode(saved).label}.`);
    return saved;
  } catch (error) {
    notify(error instanceof Error ? error.message : String(error));
    throw error;
  }
}
