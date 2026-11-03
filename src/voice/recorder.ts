// Real microphone capture: AudioWorklet PCM tap with energy endpointing.
// Produces 16kHz mono WAV for Meta transcription. No audio leaves the device
// except the final recording the user explicitly submits for transcription.

import { downsample, encodeWav, measureLevel } from "./wav";

export interface RecorderOptions {
  targetRate?: number;
  silenceMs?: number;
  maxMs?: number;
  speechThreshold?: number;
  onLevel?: (level: number) => void;
}

export class VoiceRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private chunks: Float32Array[] = [];
  private sourceRate = 48000;
  private stopTimer: number | null = null;
  private silenceTimer: number | null = null;
  private heardSpeech = false;
  private settled = false;
  private finish!: (wav: ArrayBuffer | null) => void;

  constructor(private options: RecorderOptions = {}) {}

  get targetRate(): number {
    return this.options.targetRate ?? 16000;
  }

  async start(): Promise<MediaStream> {
    if (typeof AudioContext === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not available in this environment.");
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        throw new Error("Microphone permission was denied. Allow microphone access to speak to KeplerV.");
      }
      throw new Error("Microphone device error. Check that a working microphone is connected.");
    }
    const context = new AudioContext();
    if (!context.audioWorklet) {
      stream.getTracks().forEach((track) => track.stop());
      await context.close();
      throw new Error("Audio capture modules are unavailable in this environment.");
    }
    this.stream = stream;
    this.context = context;
    this.sourceRate = context.sampleRate;
    const source = context.createMediaStreamSource(stream);
    await context.audioWorklet.addModule(new URL("./capture-processor.js", import.meta.url));
    const tap = new AudioWorkletNode(context, "keplerv-capture", { numberOfInputs: 1, numberOfOutputs: 0 });
    tap.port.onmessage = (event: MessageEvent<Float32Array>) => this.ingest(event.data);
    source.connect(tap);
    const maxMs = this.options.maxMs ?? 60000;
    this.stopTimer = window.setTimeout(() => this.resolve(), maxMs);
    return stream;
  }

  run(): Promise<ArrayBuffer | null> {
    return new Promise((resolve) => {
      this.finish = resolve;
    });
  }

  private ingest(chunk: Float32Array): void {
    if (this.settled) return;
    this.chunks.push(chunk);
    const level = measureLevel(chunk);
    this.options.onLevel?.(level);
    const threshold = this.options.speechThreshold ?? 0.08;
    if (level >= threshold) {
      this.heardSpeech = true;
      if (this.silenceTimer !== null) {
        window.clearTimeout(this.silenceTimer);
        this.silenceTimer = null;
      }
    } else if (this.heardSpeech && this.silenceTimer === null) {
      this.silenceTimer = window.setTimeout(() => this.resolve(), this.options.silenceMs ?? 1400);
    }
  }

  stop(): void {
    this.resolve();
  }

  cancel(): void {
    this.resolve(null);
  }

  private resolve(cancelled: null | undefined = undefined): void {
    if (this.settled) return;
    this.settled = true;
    if (this.stopTimer !== null) window.clearTimeout(this.stopTimer);
    if (this.silenceTimer !== null) window.clearTimeout(this.silenceTimer);
    const chunks = cancelled === null ? [] : this.chunks;
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const flat = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      flat.set(chunk, offset);
      offset += chunk.length;
    }
    this.cleanup();
    if (cancelled === null || total === 0) {
      this.finish(null);
      return;
    }
    this.finish(encodeWav(downsample(flat, this.sourceRate, this.targetRate), this.targetRate));
  }

  private cleanup(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.context) {
      void this.context.close();
      this.context = null;
    }
    this.chunks = [];
  }
}
