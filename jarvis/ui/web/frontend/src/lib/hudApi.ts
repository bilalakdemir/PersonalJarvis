/**
 * The HUD can answer a card only through the owning domain's exact route.
 * There is no "approve whatever is pending" fallback.
 */
import { approveMissionToolCall, denyMissionToolCall } from "@/components/missions/api";
import { canDecide } from "@/lib/hudSemantics";
import type { HudApproval } from "@/types/hud";

export type HudDecision = "approve" | "deny";

export class HudApprovalReadOnlyError extends Error {
  constructor(card: HudApproval) {
    super(`approval ${card.approval_id} cannot be decided from the HUD`);
    this.name = "HudApprovalReadOnlyError";
  }
}

async function postGovernanceDecision(url: string): Promise<void> {
  const response = await fetch(url, { method: "POST" });
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
}

export async function decideHudApproval(
  card: HudApproval,
  decision: HudDecision,
): Promise<void> {
  if (!canDecide(card)) throw new HudApprovalReadOnlyError(card);

  if (card.decision_channel === "mission_tool_api") {
    const missionId = card.mission_id as string;
    if (decision === "approve") {
      await approveMissionToolCall(missionId, card.trace_id);
    } else {
      await denyMissionToolCall(missionId, card.trace_id);
    }
    return;
  }

  if (card.decision_channel === "project_state_api") {
    const action = decision === "approve" ? "approve" : "reject";
    await postGovernanceDecision(
      `/api/memory/governance/project-state/${encodeURIComponent(String(card.queue_item_id))}/${encodeURIComponent(card.transaction_id as string)}/${encodeURIComponent(card.proposal_digest as string)}/${action}`,
    );
    return;
  }

  if (card.decision_channel === "memory_promotion_api") {
    const action = decision === "approve" ? "approve" : "reject";
    await postGovernanceDecision(
      `/api/memory/governance/persistent/${encodeURIComponent(String(card.candidate_id))}/${encodeURIComponent(card.proposal_digest as string)}/${action}`,
    );
    return;
  }

  // canDecide() is fail-closed, but keep this final guard if channels evolve.
  throw new HudApprovalReadOnlyError(card);
}
