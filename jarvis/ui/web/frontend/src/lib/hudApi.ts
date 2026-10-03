/**
 * The HUD's ONLY write path: answering an approval card through the route
 * the owning domain already exposes, for exactly that card's identity.
 *
 * There is no "approve whatever is pending" anywhere in the HUD. A card is
 * decided by `(mission_id, trace_id)` on the mission tool-approval route
 * (`MissionToolApprovalCoordinator`), the same call the mission deck's own
 * approval panel makes. Every other decision channel is read-only here: a
 * chat card is answered in its chat, and governed project-state proposals
 * have no safe route on this build yet (rendered read-only, documented in
 * HANDOFF.json). This module refuses — it never guesses.
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

export async function decideHudApproval(
  card: HudApproval,
  decision: HudDecision,
): Promise<void> {
  if (!canDecide(card)) throw new HudApprovalReadOnlyError(card);
  const missionId = card.mission_id as string;
  if (decision === "approve") {
    await approveMissionToolCall(missionId, card.trace_id);
  } else {
    await denyMissionToolCall(missionId, card.trace_id);
  }
}
