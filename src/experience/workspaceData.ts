import type { AppState, Bookmark } from "../types";

export const WORKSPACE_BACKUP_FORMAT = "keplerv-workspace-backup";
export const WORKSPACE_BACKUP_VERSION = 1;

function decodeEntities(text: string): string {
  return text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (entity, value: string) => {
    const key = value.toLowerCase();
    if (key === "amp") return "&";
    if (key === "lt") return "<";
    if (key === "gt") return ">";
    if (key === "quot") return '"';
    if (key === "apos" || key === "#39") return "'";
    if (key === "nbsp") return " ";
    const numeric = key.startsWith("#x") ? Number.parseInt(key.slice(2), 16) : Number.parseInt(key.slice(1), 10);
    try { return Number.isFinite(numeric) ? String.fromCodePoint(numeric) : entity; } catch { return entity; }
  });
}

function safeWebUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.toString();
  } catch { return null; }
}

export function parseBookmarksHtml(html: string, now = new Date().toISOString()): Bookmark[] {
  const found: Bookmark[] = [];
  const seen = new Set<string>();
  const folders: string[] = [];
  const tokens = html.slice(0, 10_000_000).match(/<[^>]*>|[^<]+/g) ?? [];
  let pendingFolder = "";
  let folderHeading: string | null = null;
  let anchor: { attrs: string; text: string } | null = null;
  for (const token of tokens) {
    if (anchor) {
      if (/^<\/a\b/i.test(token)) {
        const href = anchor.attrs.match(/\bhref\s*=\s*(["'])(.*?)\1/i)?.[2];
        const url = href ? safeWebUrl(decodeEntities(href)) : null;
        const title = decodeEntities(anchor.text.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
        if (url && title && !seen.has(url)) {
          seen.add(url);
          found.push({ id: `bookmark-${found.length + 1}`, title: title.slice(0, 300), url, ...(folders.filter(Boolean).length ? { folder: folders.filter(Boolean).join(" / ").slice(0, 120) } : {}), createdAt: now });
        }
        anchor = null;
      } else anchor.text += token;
      continue;
    }
    if (folderHeading !== null) {
      if (/^<\/h3\b/i.test(token)) { pendingFolder = decodeEntities(folderHeading.replace(/<[^>]*>/g, " ")).trim(); folderHeading = null; }
      else folderHeading += token;
      continue;
    }
    if (/^<h3\b/i.test(token)) { folderHeading = ""; continue; }
    if (/^<dl\b/i.test(token)) { folders.push(pendingFolder); pendingFolder = ""; continue; }
    if (/^<\/dl\b/i.test(token)) { folders.pop(); continue; }
    const start = token.match(/^<a\b([^>]*)>$/i);
    if (start) anchor = { attrs: start[1], text: "" };
  }
  return found;
}

function escapeHtml(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

export function serializeBookmarksHtml(bookmarks: Bookmark[]): string {
  type Folder = { bookmarks: Bookmark[]; children: Map<string, Folder> };
  const root: Folder = { bookmarks: [], children: new Map() };
  for (const item of bookmarks) {
    const url = safeWebUrl(item.url);
    if (!url || !item.title.trim()) continue;
    const path = (item.folder ?? "").split("/").map((part) => part.trim().slice(0, 120)).filter(Boolean);
    let folder = root;
    for (const name of path) {
      if (!folder.children.has(name)) folder.children.set(name, { bookmarks: [], children: new Map() });
      folder = folder.children.get(name)!;
    }
    folder.bookmarks.push({ ...item, url });
  }
  const renderFolder = (folder: Folder, depth: number): string => {
    const indent = "  ".repeat(depth);
    const saved = folder.bookmarks.map((item) => `${indent}<DT><A HREF="${escapeHtml(item.url)}">${escapeHtml(item.title.trim())}</A>`);
    const nested = [...folder.children.entries()].map(([name, child]) => `${indent}<DT><H3>${escapeHtml(name)}</H3>\n${indent}<DL><p>\n${renderFolder(child, depth + 1)}\n${indent}</DL><p>`);
    return [...saved, ...nested].join("\n");
  };
  return `<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n<TITLE>KeplerV Bookmarks</TITLE>\n<H1>KeplerV Bookmarks</H1>\n<DL><p>\n${renderFolder(root, 1)}\n</DL><p>\n`;
}

export function createWorkspaceBackup(state: AppState, exportedAt = new Date().toISOString()): string {
  const portableState: AppState = {
    ...state,
    agents: state.agents.filter((agent) => !agent.runtime),
    events: state.events.filter((event) => typeof event.payload?.runId !== "string"),
    bookmarks: state.bookmarks ?? [],
  };
  return JSON.stringify({ format: WORKSPACE_BACKUP_FORMAT, version: WORKSPACE_BACKUP_VERSION, exportedAt, state: portableState }, null, 2);
}

export function parseWorkspaceBackup(text: string): AppState {
  if (text.length > 25_000_000) throw new Error("This workspace backup is too large to import (25 MB maximum).");
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { throw new Error("This file is not a valid KeplerV workspace backup."); }
  if (!parsed || typeof parsed !== "object") throw new Error("This file is not a valid KeplerV workspace backup.");
  const envelope = parsed as Record<string, unknown>;
  if (envelope.format !== WORKSPACE_BACKUP_FORMAT || envelope.version !== WORKSPACE_BACKUP_VERSION) {
    throw new Error("This backup was created by an unsupported KeplerV version.");
  }
  if (!envelope.state || typeof envelope.state !== "object") throw new Error("The workspace backup has no saved workspace.");
  const state = envelope.state as Record<string, unknown>;
  for (const collection of ["goals", "agents", "events", "resources", "evidence"] as const) {
    if (!Array.isArray(state[collection])) throw new Error(`The workspace backup has invalid ${collection} data.`);
    if ((state[collection] as unknown[]).some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
      throw new Error(`The workspace backup has invalid ${collection} data.`);
    }
  }
  const resources = state.resources as Array<Record<string, unknown>>;
  if (resources.some((item) => typeof item.url !== "string" || !safeWebUrl(item.url))) {
    throw new Error("The workspace backup contains a link that is not a safe web address.");
  }
  const evidence = state.evidence as Array<Record<string, unknown>>;
  if (evidence.some((item) => typeof item.sourceUrl !== "string" || !safeWebUrl(item.sourceUrl))) {
    throw new Error("The workspace backup contains a citation that is not a safe web address.");
  }
  if (!Array.isArray(state.bookmarks ?? [])) throw new Error("The workspace backup has invalid bookmarks.");
  const bookmarks = (state.bookmarks as Bookmark[] | undefined) ?? [];
  if (bookmarks.some((item) => !item || typeof item.title !== "string" || typeof item.url !== "string" || !safeWebUrl(item.url))) {
    throw new Error("The workspace backup contains a bookmark with an invalid web address.");
  }
  const agents = (state.agents as Array<Record<string, unknown>>).filter((agent) => agent.runtime !== true);
  const events = (state.events as Array<Record<string, unknown>>).filter((event) => {
    const payload = event.payload;
    return !payload || typeof payload !== "object" || typeof (payload as Record<string, unknown>).runId !== "string";
  });
  return { ...state, agents, events, bookmarks } as unknown as AppState;
}

export function removeGoalFromState(state: AppState, goalId: string): AppState {
  return {
    ...state,
    goals: state.goals.filter((goal) => goal.id !== goalId),
    agents: state.agents.filter((agent) => agent.goalId !== goalId),
    events: state.events.filter((event) => event.goalId !== goalId),
    approval: state.approval?.goalId === goalId ? null : state.approval,
    resources: state.resources.filter((resource) => resource.goalId !== goalId),
    evidence: state.evidence.filter((item) => item.goalId !== goalId),
  };
}

export function mergeBookmarks(existing: Bookmark[], imported: Bookmark[], makeId: () => string, now = new Date().toISOString()): Bookmark[] {
  const byUrl = new Map<string, Bookmark>();
  for (const item of [...existing, ...imported]) {
    const url = safeWebUrl(item.url);
    if (!url || !item.title.trim()) continue;
    const existingItem = byUrl.get(url);
    if (existingItem) continue;
    byUrl.set(url, { id: makeId(), title: item.title.trim().slice(0, 300), url, ...(item.folder ? { folder: item.folder.slice(0, 120) } : {}), createdAt: item.createdAt || now });
  }
  return [...byUrl.values()];
}
