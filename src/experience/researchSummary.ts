import type { Evidence } from "../types";

export function sourceHostname(rawUrl: string): string {
  try { return new URL(rawUrl).hostname.toLowerCase().replace(/^www\./, "") || "Web source"; }
  catch { return "Web source"; }
}

/** Counts unique hostnames, not proven independent publishers. */
export function summarizeSourceCoverage(evidence: Evidence[]): { hostnames: string[]; label: string } {
  const hostnames = [...new Set(evidence.map((item) => sourceHostname(item.sourceUrl)).filter((host) => host !== "Web source"))].sort();
  const count = hostnames.length;
  return {
    hostnames,
    label: count === 0 ? "No source links saved" : `${count} source hostname${count === 1 ? "" : "s"}`,
  };
}
