import type { Agent } from "../types";

export type AIPDecision = { outcome: "allow" | "deny" | "require_approval"; reason: string };

export function evaluateAction(agent: Agent, capability: string, scope: { goalId: string; parentAgentId?: string; amount?: number }): AIPDecision {
  if (scope.goalId !== agent.goalId) return { outcome: "deny", reason: "Agent is not assigned to the requested goal." };
  if (scope.parentAgentId !== agent.parentAgentId) return { outcome: "deny", reason: "Parent identity does not match the delegation record." };
  if (capability === "transfer_funds") return { outcome: "require_approval", reason: "Financial actions always require explicit human approval." };
  if (!agent.permissions.includes(capability)) return { outcome: "deny", reason: `Capability “${capability}” is outside this agent's permission scope.` };
  return { outcome: "allow", reason: "Identity, goal, and permission scope match." };
}

export function isSubset(childPermissions: string[], parentPermissions: string[]): boolean {
  return childPermissions.every((permission) => parentPermissions.includes(permission));
}
