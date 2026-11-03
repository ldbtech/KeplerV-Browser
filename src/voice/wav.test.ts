import { describe, expect, it } from "vitest";
import { arrayBufferToBase64, downsample, encodeWav, measureLevel } from "./wav";

describe("voice wav helpers", () => {
  it("downsamples by nearest selection", () => {
    expect(Array.from(downsample(new Float32Array([0, 1, 2, 3]), 48000, 24000))).toEqual([0, 2]);
    expect(() => downsample(new Float32Array([0]), 16000, 48000)).toThrow();
  });

  it("encodes a valid 16-bit mono WAV file", () => {
    const buffer = encodeWav(new Float32Array([0, 0.5, -0.5, 1]), 16000);
    const view = new DataView(buffer);
    const ascii = (offset: number, length: number) =>
      Array.from({ length }, (_, index) => String.fromCharCode(view.getUint8(offset + index))).join("");
    expect(buffer.byteLength).toBe(44 + 8);
    expect(ascii(0, 4)).toBe("RIFF");
    expect(ascii(8, 4)).toBe("WAVE");
    expect(ascii(12, 4)).toBe("fmt ");
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getInt16(44, true)).toBe(0);
    expect(view.getInt16(46, true)).toBe(Math.floor(0.5 * 0x7fff));
    expect(view.getInt16(48, true)).toBe(-0.5 * 0x8000);
  });

  it("measures silence near zero and loud signals near one", () => {
    expect(measureLevel(new Float32Array(128))).toBe(0);
    expect(measureLevel(new Float32Array(128).fill(0.5))).toBeGreaterThan(0.9);
  });

  it("base64-encodes without corrupting bytes", () => {
    const buffer = new Uint8Array([0, 1, 2, 250, 255]).buffer;
    expect(arrayBufferToBase64(buffer)).toBe("AAEC+v8=");
  });
});
