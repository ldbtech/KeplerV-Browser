export type AgentStatus = "working" | "waiting" | "paused" | "completed" | "investigating" | "stopped" | "failed";
export type GoalStatus = "active" | "waiting" | "completed";
export type EventKind = "goal_created" | "agent_created" | "task_started" | "task_completed" | "agent_paused" | "agent_resumed" | "agent_stopped" | "agent_failed" | "agent_spawned" | "resource_opened" | "policy_allowed" | "policy_denied" | "approval_requested" | "approval_granted" | "approval_denied" | "investigation_started" | "interview_completed" | "goal_task_added" | "simulation_clock_advanced";

export interface Goal { id: string; title: string; description: string; progress: number; status: GoalStatus; tasks: string[]; createdAt: string; }
export interface Agent { id: string; name: string; provider: string; model: string; parentAgentId?: string; goalId: string; status: AgentStatus; permissions: string[]; capabilities: string[]; currentTask?: string; createdAt: string; steps: number; error?: string | null; runtime?: boolean; runId?: string; }
export interface KeplerEvent { id: string; timestamp: string; agentId?: string; goalId?: string; parentEventId?: string; type: EventKind; payload: Record<string, unknown>; }
export interface Incident { id: string; goalId: string; agentId: string; status: "open" | "resumable" | "terminated"; evidence: string[]; interview: string[]; recommendation: string; }
export interface Approval { id: string; goalId: string; agentId: string; action: string; amount: number; status: "pending" | "approved" | "denied"; }
export interface Resource { id: string; goalId: string; agentId: string; label: string; url: string; }
export interface Evidence { id: string; goalId: string; agentId: string; runId: string; finding: string; sourceUrl: string; quote: string; createdAt: string; }
export interface AppState { goals: Goal[]; agents: Agent[]; events: KeplerEvent[]; incident: Incident | null; approval: Approval | null; resources: Resource[]; evidence: Evidence[]; theme: "light" | "dark" | "system"; simulationTime: number; }
