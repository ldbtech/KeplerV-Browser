// System-native speech output behind a provider-independent interface.
// Uses the platform speech synthesizer (macOS voices in the desktop shell).

export type SpeakingState = "idle" | "speaking";

export interface SpeechOptions {
  volume?: number;
  rate?: number;
  onState?: (state: SpeakingState) => void;
}

const STORE_KEY = "keplerv.speechEnabled";

export class VoiceSpeech {
  private queue: string[] = [];
  private busy = false;
  private volume = 1;
  private enabled = true;
  private state: SpeakingState = "idle";

  constructor(private options: SpeechOptions = {}) {
    try {
      const stored = localStorage.getItem(STORE_KEY);
      this.enabled = stored === null ? true : stored === "1";
    } catch {
      this.enabled = true;
    }
    if (typeof options.volume === "number") this.volume = options.volume;
  }

  get available(): boolean {
    return typeof window !== "undefined" && "speechSynthesis" in window;
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    try {
      localStorage.setItem(STORE_KEY, enabled ? "1" : "0");
    } catch {
      /* private mode: keep in memory only */
    }
    if (!enabled) this.stop();
  }

  setVolume(volume: number): void {
    this.volume = Math.max(0, Math.min(1, volume));
  }

  private setState(state: SpeakingState): void {
    this.state = state;
    this.options.onState?.(state);
  }

  speak(text: string): boolean {
    const message = text.trim().replace(/\s+/g, " ");
    if (!message) return false;
    this.stop();
    return this.enqueue(message);
  }

  announce(text: string): boolean {
    const message = text.trim().replace(/\s+/g, " ");
    if (!message) return false;
    return this.enqueue(message);
  }

  private enqueue(message: string): boolean {
    if (!this.enabled || !this.available) return false;
    this.queue.push(message.slice(0, 600));
    void this.drain();
    return true;
  }

  private async drain(): Promise<void> {
    if (this.busy) return;
    const next = this.queue.shift();
    if (next === undefined) return;
    this.busy = true;
    this.setState("speaking");
    try {
      await this.utter(next);
    } finally {
      this.busy = false;
      if (this.queue.length > 0) {
        void this.drain();
      } else {
        this.setState("idle");
      }
    }
  }

  private utter(text: string): Promise<void> {
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.volume = this.volume;
      utterance.rate = this.options.rate ?? 1;
      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();
      window.speechSynthesis.speak(utterance);
    });
  }

  interrupt(): void {
    this.stop();
  }

  stop(): void {
    this.queue = [];
    if (this.available) window.speechSynthesis.cancel();
    this.busy = false;
    this.setState("idle");
  }

  cancelQueue(): void {
    this.queue = [];
    if (!this.busy) this.setState("idle");
  }
}
