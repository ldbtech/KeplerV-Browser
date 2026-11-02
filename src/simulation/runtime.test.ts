import { describe, expect, it } from "vitest";
import { evaluateAction, isSubset } from "./aipPolicy";
import { addTask, advanceSimulationClock, createAgent, createGoal, spawnSubagent, transitionAgent } from "./runtime";
import { initialState, MAX_CHILDREN, MAX_DEPTH } from "./seed";
import type { AppState } from "../types";

const fresh = (): AppState => structuredClone(initialState);

describe("mock AIP", () => {
  it("allows only a declared capability within the assigned goal", () => {
    const agent = fresh().agents[0];
    expect(evaluateAction(agent, "read_public", { goalId: agent.goalId }).outcome).toBe("allow");
    expect(evaluateAction(agent, "read_public", { goalId: "another-goal" }).outcome).toBe("deny");
    expect(evaluateAction(agent, "read_private", { goalId: agent.goalId }).outcome).toBe("deny");
  });

  it("requires human approval for fictional financial actions", () => {
    const agent = fresh().agents[0];
    expect(evaluateAction(agent, "transfer_funds", { goalId: agent.goalId, amount: 750 }).outcome).toBe("require_approval");
  });
});

describe("goal and agent runtime", () => {
  it("creates goals and records a task addition in the event ledger", () => {
    let state = createGoal(fresh(), "Plan a field visit", "Compare sites and travel times.");
    const goal = state.goals[0];
    state = addTask(state, goal.id, "Confirm public transit access");
    expect(state.goals[0].tasks).toContain("Confirm public transit access");
    expect(state.events[0].type).toBe("goal_task_added");
    expect(state.events[1].type).toBe("goal_created");
  });

  it("records lifecycle policy decisions and updates agent state", () => {
    const state = transitionAgent(fresh(), "agent-research", "pause");
    expect(state.agents.find((agent) => agent.id === "agent-research")?.status).toBe("paused");
    expect(state.events[0].type).toBe("agent_paused");
    expect(state.events[1].type).toBe("policy_allowed");
  });

  it("caps delegation breadth and depth, with child permissions as a subset", () => {
    let state = fresh();
    state = spawnSubagent(state, "agent-research");
    const child = state.agents[0];
    expect(child.parentAgentId).toBe("agent-research");
    expect(isSubset(child.permissions, state.agents.find((agent) => agent.id === child.parentAgentId)!.permissions)).toBe(true);
    state = spawnSubagent(state, "agent-research");
    expect(state.agents.filter((agent) => agent.parentAgentId === "agent-research")).toHaveLength(MAX_CHILDREN);
    const blockedChild = spawnSubagent(state, "agent-research");
    expect(blockedChild.agents).toHaveLength(state.agents.length);
    const firstChild = state.agents.find((agent) => agent.parentAgentId === "agent-research")!;
    state = spawnSubagent(state, firstChild.id);
    const grandchild = state.agents.find((agent) => agent.parentAgentId === firstChild.id)!;
    expect(grandchild).toBeDefined();
    const blockedGreatGrandchild = spawnSubagent(state, grandchild.id);
    expect(blockedGreatGrandchild.agents).toHaveLength(state.agents.length);
    expect(MAX_DEPTH).toBe(2);
  });

  it("persists simulation data as a JSON-compatible local state", () => {
    const state = createAgent(fresh(), "goal-research");
    const restored = JSON.parse(JSON.stringify(state)) as AppState;
    expect(restored.goals[0].id).toBe(state.goals[0].id);
    expect(restored.agents[0].permissions).toEqual(state.agents[0].permissions);
    expect(restored.events[0].type).toBe("agent_created");
  });

  it("advances the mock clock only when explicitly stepped", () => {
    const state = fresh();
    const advanced = advanceSimulationClock(state);
    expect(advanced.simulationTime - state.simulationTime).toBe(60_000);
    expect(advanced.events[0].type).toBe("simulation_clock_advanced");
    expect(advanced.events[0].timestamp).toBe(new Date(advanced.simulationTime).toISOString());
  });
});
