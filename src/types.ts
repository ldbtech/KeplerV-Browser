export type AgentStatus = "working" | "waiting" | "paused" | "completed" | "investigating" | "stopped" | "failed";
export type GoalStatus = "active" | "waiting" | "completed";
export type EventKind = "goal_created" | "agent_created" | "task_started" | "task_completed" | "agent_paused" | "agent_resumed" | "agent_stopped" | "agent_failed" | "agent_spawned" | "resource_opened" | "policy_allowed" | "policy_denied" | "approval_requested" | "approval_granted" | "approval_denied" | "investigation_started" | "interview_completed" | "goal_task_added" | "simulation_clock_advanced" | "offline_mode_enabled" | "offline_mode_disabled";

export interface Goal { id: string; title: string; description: string; progress: number; status: GoalStatus; tasks: string[]; criteria: string[]; constraints: string[]; createdAt: string; }
export interface Agent { id: string; name: string; provider: string; model: string; parentAgentId?: string; goalId: string; status: AgentStatus; permissions: string[]; capabilities: string[]; currentTask?: string; createdAt: string; steps: number; error?: string | null; runtime?: boolean; runId?: string; browserIsolated?: boolean; }
export interface KeplerEvent { id: string; timestamp: string; agentId?: string; goalId?: string; parentEventId?: string; type: EventKind; payload: Record<string, unknown>; }
export interface Approval { id: string; goalId: string; agentId: string; action: string; amount: number; status: "pending" | "approved" | "denied"; }
export interface Resource { id: string; goalId: string; agentId: string; label: string; url: string; }
export interface Evidence { id: string; goalId: string; agentId: string; runId: string; finding: string; sourceUrl: string; quote: string; createdAt: string; }
export interface AppState { goals: Goal[]; agents: Agent[]; events: KeplerEvent[]; approval: Approval | null; resources: Resource[]; evidence: Evidence[]; theme: "light" | "dark" | "system"; simulationTime: number; offlineMode: boolean; }

export type ServiceProviderId = "gmail" | "outlook" | "fake-mail";
export type ServiceAccountStatus = "connected" | "expired" | "revoked";
export interface ConnectedAccount { provider: ServiceProviderId; email: string; scopes: string[]; expires_at: number; status: ServiceAccountStatus; }

export interface MailMessage { id: string; thread_id?: string; provider?: string; from?: string; to?: string[]; subject: string; date?: string; snippet?: string; body?: string; fake?: boolean; label?: string; }
export interface MailAccountRef { provider?: string; email?: string; }
export interface EmailDraft { to: string; subject: string; body: string; accountEmail?: string; }

export type MediaSurfaceId = "local-video" | "youtube" | "netflix-fallback";
export type MediaCommand = "OPEN" | "PAUSE" | "RESUME" | "CLOSE_MEDIA";
export interface CapabilityResult { embeddable: boolean; reason: string; fallbackLabel?: string; }
export interface MediaSurfaceState { active: boolean; paused: boolean; }
export type MediaFocusState = Record<MediaSurfaceId, MediaSurfaceState>;
