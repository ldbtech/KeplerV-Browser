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

export type GatewayIdentityStatus = "verified" | "self_declared" | "unknown" | "suspected";
export type GatewaySessionStatus = "open" | "closed" | "cancelled";
export interface ExternalAgentInfo { agent_id: string; endpoint: string; protocol?: string; display_name?: string; protocol_version?: string; identity_status: GatewayIdentityStatus; capabilities_declared: string[]; capabilities_observed: string[]; provenance: { asserted_by: string; asserted_at: string; evidence_refs: string[] }; reliability: Record<string, number>; }
export interface GatewaySessionInfo { id: string; run_id: string; agent_id: string; external_agent_id: string; endpoint: string; status: GatewaySessionStatus; label: string; messages_sent: number; messages_received: number; tokens_used: number; created_at: string; updated_at: string; }
export interface GatewayMessageInfo { id: string; message_id: string; reply_to?: string | null; direction: string; sender: string; recipient: string; kind: string; label: string; untrusted: boolean; payload: Record<string, unknown>; created_at: string; }
export interface ClaimRecord { id: string; kind: string; summary: string; product_id?: string; message_id: string; statement_ref: string; }
export interface VerificationResultInfo { claim: ClaimRecord; verdict: string; reason: string; evidence_refs: string[]; }
export interface SessionVerification { session_id: string; external_agent_id: string; analysis_label: string; classifier_label: string; classification: { classification: string; rationale: string; evidence_refs: string[]; abstained: boolean }; verdicts: VerificationResultInfo[]; contradictions: Array<{ id: string; kind: string; detail: string; evidence_refs: string[] }>; inbound_findings: Array<{ path: string; kind: string; message_id?: string }>; follow_up_questions: Array<{ issue_via: string; rationale: string }>; uncertainties: string[]; abstained: boolean; }

export type MediaSurfaceId = "local-video" | "youtube" | "netflix-fallback";
export type MediaCommand = "OPEN" | "PAUSE" | "RESUME" | "CLOSE_MEDIA";
export interface CapabilityResult { embeddable: boolean; reason: string; fallbackLabel?: string; }
export interface MediaSurfaceState { active: boolean; paused: boolean; }
export type MediaFocusState = Record<MediaSurfaceId, MediaSurfaceState>;
