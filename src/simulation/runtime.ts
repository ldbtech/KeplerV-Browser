import type { Agent, AppState, Goal, KeplerEvent } from "../types";
import { evaluateAction, isSubset } from "./aipPolicy";
import { MAX_CHILDREN, MAX_DEPTH, MAX_STEPS, makeId } from "./seed";

export type Dispatch = (next: AppState) => void;
export function appendEvent(state: AppState, type: KeplerEvent["type"], payload: Record<string, unknown>, agentId?: string, goalId?: string, parentEventId?: string): AppState {
  const event: KeplerEvent = { id: makeId("event"), timestamp: new Date(state.simulationTime).toISOString(), type, payload, ...(agentId ? { agentId } : {}), ...(goalId ? { goalId } : {}), ...(parentEventId ? { parentEventId } : {}) };
  return { ...state, events: [event, ...state.events] };
}

export function createGoal(state: AppState, title: string, description: string): AppState {
  const goal: Goal = { id: makeId("goal"), title, description, progress: 0, status: "active", tasks: ["Clarify the next step", "Collect useful evidence", "Review the outcome"], criteria: ["Outcome reviewed against the request"], constraints: [], createdAt: new Date(state.simulationTime).toISOString() };
  return appendEvent({ ...state, goals: [goal, ...state.goals] }, "goal_created", { summary: title }, undefined, goal.id);
}

export function modifyGoal(state: AppState, goalId: string, patch: { title?: string; description?: string; addTask?: string; addCriterion?: string; addConstraint?: string }): AppState {
  const goal = state.goals.find((item) => item.id === goalId);
  if (!goal) return state;
  const next: Goal = {
    ...goal,
    ...(patch.title ? { title: patch.title.slice(0, 160) } : {}),
    ...(patch.description ? { description: patch.description.slice(0, 2000) } : {}),
    ...(patch.addTask ? { tasks: [...goal.tasks, patch.addTask.slice(0, 200)] } : {}),
    ...(patch.addCriterion ? { criteria: [...goal.criteria, patch.addCriterion.slice(0, 200)] } : {}),
    ...(patch.addConstraint ? { constraints: [...goal.constraints, patch.addConstraint.slice(0, 200)] } : {}),
  };
  return appendEvent({ ...state, goals: state.goals.map((item) => item.id === goalId ? next : item) }, "goal_task_added", { summary: `Updated ${goal.title}` }, undefined, goalId);
}

export function createAgent(state: AppState, goalId: string, parent?: Agent): AppState {
  const goal = state.goals.find((item) => item.id === goalId);
  if (!goal) return state;
  const agent: Agent = { id: makeId("agent"), name: parent ? "Evidence subagent" : "Intent agent", provider: parent ? parent.provider : "On device", model: parent ? parent.model : "Researcher", ...(parent ? { parentAgentId: parent.id } : {}), goalId, status: "working", permissions: parent ? parent.permissions.filter((permission) => permission !== "read_private" && permission !== "transfer_funds") : ["read_public", "summarize", "create_evidence", "delegate", "execute"], capabilities: ["public research", "evidence review"], currentTask: parent ? "Verify one supporting source" : goal.tasks[0], createdAt: new Date(state.simulationTime).toISOString(), steps: 0 };
  if (parent && !isSubset(agent.permissions, parent.permissions)) return state;
  const withAgent = { ...state, agents: [agent, ...state.agents] };
  return appendEvent(withAgent, parent ? "agent_spawned" : "agent_created", { summary: `${agent.name} assigned to ${goal.title}`, inheritedPermissions: agent.permissions }, agent.id, goalId);
}

export function transitionAgent(state: AppState, agentId: string, action: "pause" | "resume" | "stop" | "complete" | "fail"): AppState {
  const agent = state.agents.find((item) => item.id === agentId);
  if (!agent || agent.steps >= MAX_STEPS && action === "resume") return state;
  const transitionAllowed = action === "pause" ? agent.status === "working" : action === "resume" ? agent.status === "paused" || agent.status === "waiting" || agent.status === "investigating" : action === "stop" ? agent.status !== "stopped" && agent.status !== "completed" : agent.status === "working";
  if (!transitionAllowed) return state;
  const decision = evaluateAction(agent, "execute", { goalId: agent.goalId, parentAgentId: agent.parentAgentId });
  if (decision.outcome !== "allow") return appendEvent(state, "policy_denied", { capability: "execute", reason: decision.reason }, agentId, agent.goalId);
  const status = action === "pause" ? "paused" : action === "resume" ? "working" : action === "stop" ? "stopped" : action === "complete" ? "completed" : "paused";
  const agents = state.agents.map((item) => item.id === agentId ? { ...item, status: status as Agent["status"], steps: action === "resume" ? item.steps + 1 : item.steps } : item);
  const goalId = agent.goalId;
  const type: KeplerEvent["type"] = action === "pause" ? "agent_paused" : action === "resume" ? "agent_resumed" : action === "stop" ? "agent_stopped" : action === "complete" ? "task_completed" : "agent_failed";
  const authorized = appendEvent(state, "policy_allowed", { capability: "execute", reason: decision.reason, requestedTransition: action }, agentId, goalId);
  let next = { ...authorized, agents };
  if (action === "complete") {
    next = { ...next, goals: next.goals.map((goal) => goal.id === goalId ? { ...goal, progress: Math.min(100, goal.progress + Math.ceil(100 / Math.max(goal.tasks.length, 1))), status: goal.progress + Math.ceil(100 / Math.max(goal.tasks.length, 1)) >= 100 ? "completed" : goal.status } : goal) };
  }
  return appendEvent(next, type, { summary: `${agent.name} ${action}d`, action }, agentId, goalId);
}

export function setOfflineMode(state: AppState, enabled: boolean): AppState {
  if (state.offlineMode === enabled) return state;
  return appendEvent({ ...state, offlineMode: enabled }, enabled ? "offline_mode_enabled" : "offline_mode_disabled", { summary: enabled ? "Offline workspace selected. Local goals and helpers remain available; Muse research needs a connection." : "Online research re-enabled." });
}

export function advanceSimulationClock(state: AppState): AppState {
  const next = { ...state, simulationTime: state.simulationTime + 60_000 };
  return appendEvent(next, "simulation_clock_advanced", { summary: "Workspace clock advanced by one minute" });
}

export function spawnSubagent(state: AppState, parentId: string): AppState {
  const parent = state.agents.find((item) => item.id === parentId);
  if (!parent) return state;
  const children = state.agents.filter((item) => item.parentAgentId === parent.id);
  let depth = 0;
  let current: Agent | undefined = parent;
  while (current?.parentAgentId) { depth += 1; current = state.agents.find((item) => item.id === current?.parentAgentId); }
  const decision = evaluateAction(parent, "delegate", { goalId: parent.goalId, parentAgentId: parent.parentAgentId });
  const withinLimits = children.length < MAX_CHILDREN && depth < MAX_DEPTH && parent.status === "working";
  if (decision.outcome !== "allow" || !withinLimits) {
    const reason = !withinLimits ? "Delegation limit or agent lifecycle state prevents another child." : decision.reason;
    return appendEvent(state, "policy_denied", { capability: "delegate", reason }, parent.id, parent.goalId);
  }
  const authorized = appendEvent(state, "policy_allowed", { capability: "delegate", reason: decision.reason }, parent.id, parent.goalId);
  return createAgent(authorized, parent.goalId, parent);
}

export function addTask(state: AppState, goalId: string, task: string): AppState {
  const goals = state.goals.map((goal) => goal.id === goalId ? { ...goal, tasks: [...goal.tasks, task] } : goal);
  return appendEvent({ ...state, goals }, "goal_task_added", { summary: task }, undefined, goalId);
}

export function auditAction(state: AppState, agentId: string, capability: string, amount?: number): { state: AppState; outcome: "allow" | "deny" | "require_approval"; reason: string } {
  const agent = state.agents.find((item) => item.id === agentId);
  if (!agent) return { state, outcome: "deny", reason: "Agent identity could not be resolved." };
  const decision = evaluateAction(agent, capability, { goalId: agent.goalId, parentAgentId: agent.parentAgentId, amount });
  const type = decision.outcome === "allow" ? "policy_allowed" : decision.outcome === "deny" ? "policy_denied" : "approval_requested";
  return { state: appendEvent(state, type, { capability, outcome: decision.outcome, reason: decision.reason, amount }, agentId, agent.goalId), ...decision };
}

export function canSpawn(state: AppState, parent: Agent): boolean {
  const children = state.agents.filter((item) => item.parentAgentId === parent.id).length;
  let depth = 0; let current: Agent | undefined = parent;
  while (current?.parentAgentId) { depth++; current = state.agents.find((item) => item.id === current?.parentAgentId); }
  return children < MAX_CHILDREN && depth < MAX_DEPTH && parent.status === "working" && parent.permissions.includes("delegate");
}
