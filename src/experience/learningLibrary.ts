import type { Evidence, Resource } from "../types";

export type LearningMediaKind = "video" | "document" | "file" | "article";
export type LearningPlayer = "youtube" | "vimeo" | "video-file" | "pdf" | "web" | "external";

export interface LearningMediaItem {
  id: string;
  goalId: string;
  title: string;
  kind: LearningMediaKind;
  player: LearningPlayer;
  provider: string;
  url: string;
  summary: string;
  quote?: string;
  createdBy?: string;
}

function describeUrl(raw: string): { kind: LearningMediaKind; player: LearningPlayer; provider: string; title: string } {
  let url: URL;
  try { url = new URL(raw); } catch { return { kind: "file", player: "external", provider: "File", title: "Shared file" }; }
  const host = url.hostname.toLowerCase().replace(/^www\./, "");
  const path = url.pathname.toLowerCase();
  if (host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtube-nocookie.com" || host.endsWith(".youtube-nocookie.com")) {
    return { kind: "video", player: "youtube", provider: "YouTube", title: "YouTube video" };
  }
  if (host === "vimeo.com" || host.endsWith(".vimeo.com")) {
    return { kind: "video", player: "vimeo", provider: "Vimeo", title: "Vimeo video" };
  }
  if (host === "netflix.com" || host.endsWith(".netflix.com")) {
    return { kind: "video", player: "external", provider: "Netflix", title: "Netflix title" };
  }
  if (/\.pdf$/.test(path)) return { kind: "document", player: "pdf", provider: "PDF", title: "PDF document" };
  if (/\.(mp4|webm|mov|m4v|ogv)$/.test(path)) return { kind: "video", player: "video-file", provider: "Video file", title: "Video" };
  if (/\.(docx?|pptx?|xlsx?|epub|zip|csv|txt)$/.test(path)) return { kind: "file", player: "external", provider: "File", title: "Shared file" };
  return { kind: "article", player: "web", provider: host || "Website", title: host || "Web resource" };
}

export function youtubeEmbedFromUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    if (!(host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtube-nocookie.com" || host.endsWith(".youtube-nocookie.com"))) return null;
    const id = host === "youtu.be"
      ? url.pathname.split("/").filter(Boolean)[0]
      : url.pathname.startsWith("/shorts/") || url.pathname.startsWith("/embed/")
        ? url.pathname.split("/").filter(Boolean)[1]
        : url.searchParams.get("v");
    if (!id || !/^[A-Za-z0-9_-]{11}$/.test(id)) return null;
    return `https://www.youtube-nocookie.com/embed/${id}?rel=0&enablejsapi=1`;
  } catch { return null; }
}

export function vimeoEmbedFromUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    if (!(host === "vimeo.com" || host.endsWith(".vimeo.com"))) return null;
    const id = host === "player.vimeo.com"
      ? url.pathname.split("/").filter(Boolean).at(-1)
      : url.pathname.split("/").filter(Boolean).find((part) => /^\d+$/.test(part));
    return (host === "vimeo.com" || host.endsWith("vimeo.com")) && id && /^\d+$/.test(id)
      ? `https://player.vimeo.com/video/${id}`
      : null;
  } catch { return null; }
}

export function buildLearningLibrary(goalId: string, resources: Resource[], evidence: Evidence[]): LearningMediaItem[] {
  const goalResources = resources.filter((resource) => resource.goalId === goalId);
  const goalEvidence = evidence.filter((item) => item.goalId === goalId && /^https?:\/\//i.test(item.sourceUrl));
  const byUrl = new Map<string, { resource?: Resource; evidence: Evidence[] }>();
  for (const resource of goalResources) byUrl.set(resource.url, { resource, evidence: [] });
  for (const item of goalEvidence) {
    const entry = byUrl.get(item.sourceUrl) ?? { evidence: [] };
    entry.evidence.push(item);
    byUrl.set(item.sourceUrl, entry);
  }
  return [...byUrl.entries()].map(([url, entry]) => {
    const media = describeUrl(url);
    const evidenceItem = entry.evidence[0];
    const label = entry.resource?.label;
    return {
      id: entry.resource?.id ?? evidenceItem?.id ?? url,
      goalId,
      title: evidenceItem?.finding || label || media.title,
      kind: media.kind,
      player: media.player,
      provider: media.provider,
      url,
      summary: evidenceItem?.finding || `Saved by ${media.provider} for this goal.`,
      ...(evidenceItem?.quote ? { quote: evidenceItem.quote } : {}),
      ...(evidenceItem?.agentId ? { createdBy: evidenceItem.agentId } : {}),
    };
  }).sort((a, b) => kindOrder(a.kind) - kindOrder(b.kind) || a.title.localeCompare(b.title));
}

function kindOrder(kind: LearningMediaKind): number {
  return kind === "video" ? 0 : kind === "document" ? 1 : kind === "file" ? 2 : 3;
}
