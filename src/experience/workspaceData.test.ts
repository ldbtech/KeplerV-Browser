import { describe, expect, it } from "vitest";
import type { AppState } from "../types";
import { createWorkspaceBackup, mergeBookmarks, parseBookmarksHtml, parseWorkspaceBackup, removeGoalFromState, serializeBookmarksHtml } from "./workspaceData";

describe("browser and workspace data transfer", () => {
  it("imports Chrome and Safari Netscape bookmarks, including folders, entities, and fragments", () => {
    const html = `<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p><DT><H3 ADD_DATE="1">Research &amp; Notes</H3><DL><p><DT><A HREF="https://example.org/guide#section">Guide &amp; notes</A><DT><A HREF="javascript:alert(1)">Unsafe</A></DL><p></DL><p>`;
    expect(parseBookmarksHtml(html, "2026-11-04T00:00:00Z")).toEqual([
      { id: "bookmark-1", title: "Guide & notes", url: "https://example.org/guide#section", folder: "Research & Notes", createdAt: "2026-11-04T00:00:00Z" },
    ]);
  });

  it("exports only safe web bookmarks in standard Netscape HTML", () => {
    const html = serializeBookmarksHtml([
      { id: "1", title: "Example & notes", url: "https://example.org/path#section", folder: "Study / Statistics", createdAt: "now" },
      { id: "2", title: "Unsafe", url: "javascript:alert(1)", createdAt: "now" },
    ]);
    expect(html).toContain("NETSCAPE-Bookmark-file-1");
    expect(html).toContain("Example &amp; notes");
    expect(html).not.toContain("javascript:");
    expect(parseBookmarksHtml(html)[0]).toMatchObject({ url: "https://example.org/path#section", folder: "Study / Statistics" });
  });

  it("deduplicates bookmarks by URL while preserving the saved title", () => {
    const merged = mergeBookmarks(
      [{ id: "old", title: "Saved title", url: "https://example.org/", createdAt: "then" }],
      [{ id: "new", title: "Imported title", url: "https://example.org/", createdAt: "now" }],
      () => "generated",
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe("Saved title");
  });

  it("round-trips the local workspace and rejects unsafe imported resource URLs", () => {
    const state = {
      goals: [], agents: [], events: [], approval: null, resources: [], bookmarks: [], evidence: [],
      theme: "system", simulationTime: 0, offlineMode: false,
    } as unknown as AppState;
    expect(parseWorkspaceBackup(createWorkspaceBackup(state))).toEqual({ ...state, bookmarks: [] });
    const unsafe = JSON.parse(createWorkspaceBackup({ ...state, resources: [{ id: "r", goalId: "g", agentId: "a", label: "Bad link", url: "javascript:alert(1)" }] as unknown as AppState["resources"] }));
    expect(() => parseWorkspaceBackup(JSON.stringify(unsafe))).toThrow(/safe web address/i);
  });

  it("keeps runtime-only agent records out of the portable workspace copy", () => {
    const state = {
      goals: [], agents: [
        { id: "local", name: "Local helper", provider: "On device", model: "Researcher", goalId: "g", status: "waiting", permissions: [], capabilities: [], createdAt: "now", steps: 0 },
        { id: "live", name: "Muse researcher", provider: "Meta Muse", model: "muse-spark-1.3", goalId: "g", status: "completed", permissions: [], capabilities: [], createdAt: "now", steps: 3, runtime: true, runId: "run-1" },
      ], events: [
        { id: "local-event", timestamp: "now", type: "goal_created", payload: { summary: "Goal created" } },
        { id: "runtime-event", timestamp: "now", type: "aip_decision", payload: { runId: "run-1", reason: "Denied" } },
      ], approval: null, resources: [], bookmarks: [], evidence: [], theme: "system", simulationTime: 0, offlineMode: false,
    } as unknown as AppState;
    const backup = JSON.parse(createWorkspaceBackup(state));
    expect(backup.state.agents.map((item: { id: string }) => item.id)).toEqual(["local"]);
    expect(backup.state.events.map((item: { id: string }) => item.id)).toEqual(["local-event"]);
    const restored = parseWorkspaceBackup(JSON.stringify({ ...backup, state: { ...backup.state, agents: [...backup.state.agents, { id: "runtime-import", runtime: true }], events: [...backup.state.events, { id: "runtime-event", payload: { runId: "missing" } }] } }));
    expect(restored.agents.map((item) => item.id)).toEqual(["local"]);
    expect(restored.events.map((item) => item.id)).toEqual(["local-event"]);
  });

  it("removes one goal with its agents, events, approval, resources, and evidence", () => {
    const state = {
      goals: [
        { id: "keep", title: "Keep", description: "", progress: 0, status: "active", tasks: [], criteria: [], constraints: [], createdAt: "now" },
        { id: "drop", title: "Drop", description: "", progress: 0, status: "active", tasks: [], criteria: [], constraints: [], createdAt: "now" },
      ],
      agents: [
        { id: "a-keep", name: "Keep", provider: "p", model: "m", goalId: "keep", status: "waiting", permissions: [], capabilities: [], createdAt: "now", steps: 0 },
        { id: "a-drop", name: "Drop", provider: "p", model: "m", goalId: "drop", status: "waiting", permissions: [], capabilities: [], createdAt: "now", steps: 0 },
      ],
      events: [
        { id: "e-keep", timestamp: "now", goalId: "keep", type: "goal_created", payload: {} },
        { id: "e-drop", timestamp: "now", goalId: "drop", type: "goal_created", payload: {} },
      ],
      approval: { id: "ap-1", goalId: "drop", agentId: "a-drop", action: "gmail.send", amount: 0, status: "pending" },
      resources: [{ id: "r-drop", goalId: "drop", agentId: "a-drop", label: "R", url: "https://example.org/" }],
      bookmarks: [{ id: "b-1", title: "Saved", url: "https://example.org/", createdAt: "now" }],
      evidence: [{ id: "v-drop", goalId: "drop", agentId: "a-drop", runId: "run-1", finding: "F", sourceUrl: "https://example.org/", quote: "Q", createdAt: "now" }],
      theme: "system", simulationTime: 0, offlineMode: false,
    } as unknown as AppState;
    const next = removeGoalFromState(state, "drop");
    expect(next.goals.map((item) => item.id)).toEqual(["keep"]);
    expect(next.agents.map((item) => item.id)).toEqual(["a-keep"]);
    expect(next.events.map((item) => item.id)).toEqual(["e-keep"]);
    expect(next.approval).toBeNull();
    expect(next.resources).toEqual([]);
    expect(next.evidence).toEqual([]);
    expect(next.bookmarks?.map((item) => item.id)).toEqual(["b-1"]);
    expect(state.goals).toHaveLength(2);
  });
});
