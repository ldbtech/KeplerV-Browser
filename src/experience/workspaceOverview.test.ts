import { describe, expect, it } from "vitest";
import type { Goal } from "../types";
import { buildDashboardGoalList } from "./workspaceOverview";

function goal(id: string, createdAt: string, title = `Goal ${id}`, description = "") : Goal {
  return {
    id,
    title,
    description,
    progress: 0,
    status: "active",
    tasks: [],
    criteria: [],
    constraints: [],
    createdAt,
  };
}

describe("dashboard goal list", () => {
  const goals = [
    goal("old", "2026-01-01T00:00:00Z", "Old planning goal"),
    goal("new", "2026-03-01T00:00:00Z", "New research goal", "Study coastal adaptation"),
    goal("middle", "2026-02-01T00:00:00Z", "Middle goal"),
  ];

  it("shows newest goals first without mutating workspace order", () => {
    const result = buildDashboardGoalList(goals, "", false, 2);
    expect(result.items.map((item) => item.id)).toEqual(["new", "middle"]);
    expect(result).toMatchObject({ total: 3, hasMore: true });
    expect(goals.map((item) => item.id)).toEqual(["old", "new", "middle"]);
  });

  it("filters by goal title and description and reveals all matches", () => {
    const result = buildDashboardGoalList(goals, "COASTAL", false);
    expect(result.items.map((item) => item.id)).toEqual(["new"]);
    expect(result).toMatchObject({ total: 1, hasMore: false });
  });

  it("expands the recent list without dropping older goals", () => {
    const result = buildDashboardGoalList(goals, "", true, 1);
    expect(result.items.map((item) => item.id)).toEqual(["new", "middle", "old"]);
    expect(result.hasMore).toBe(false);
  });
});
