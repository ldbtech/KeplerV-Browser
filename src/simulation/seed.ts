import type { AppState } from "../types";

export const STORAGE_KEY = "keplerv_intent_state_v1";
export const MAX_CHILDREN = 2;
export const MAX_DEPTH = 2;
export const MAX_STEPS = 6;

export function makeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.floor(Math.random() * 0xffffff).toString(36)}`;
}

export const initialState: AppState = {
  goals: [
    { id: "goal-research", title: "Understand coastal resilience", description: "Compare public research and identify practical adaptation strategies for the North Shore.", progress: 42, status: "active", tasks: ["Collect regional evidence", "Compare adaptation approaches", "Prepare a decision brief"], createdAt: "2026-11-02T14:00:00.000Z" },
    { id: "goal-enrollment", title: "Prepare for spring enrollment", description: "Keep application requirements, deadlines, and next steps in one place.", progress: 68, status: "active", tasks: ["Confirm prerequisite credits", "Gather application materials", "Review key deadlines"], createdAt: "2026-11-01T14:00:00.000Z" },
  ],
  agents: [
    { id: "agent-research", name: "Researcher", provider: "On device", model: "Researcher", goalId: "goal-research", status: "working", permissions: ["read_public", "summarize", "create_evidence", "delegate", "execute"], capabilities: ["public research", "evidence review"], currentTask: "Comparing shoreline adaptation studies", createdAt: "2026-11-02T14:02:00.000Z", steps: 1 },
    { id: "agent-planner", name: "Planner", provider: "On device", model: "Course Planner", goalId: "goal-enrollment", status: "waiting", permissions: ["read_public", "organize", "execute"], capabilities: ["deadline tracking", "checklists"], currentTask: "Waiting for prerequisite confirmation", createdAt: "2026-11-01T14:03:00.000Z", steps: 0 },
  ],
  events: [
    { id: "event-2", timestamp: "2026-11-02T14:04:00.000Z", goalId: "goal-research", agentId: "agent-research", type: "task_started", payload: { summary: "Comparing public shoreline studies" } },
    { id: "event-1", timestamp: "2026-11-02T14:02:00.000Z", goalId: "goal-research", agentId: "agent-research", type: "agent_created", payload: { summary: "Researcher assigned to coastal resilience" } },
    { id: "event-3", timestamp: "2026-11-01T14:03:00.000Z", goalId: "goal-enrollment", agentId: "agent-planner", type: "agent_created", payload: { summary: "Planner assigned to spring enrollment" } },
  ],
  incident: null,
  approval: null,
  resources: [],
  evidence: [],
  theme: "system",
  simulationTime: Date.parse("2026-11-02T14:00:00.000Z"),
};
