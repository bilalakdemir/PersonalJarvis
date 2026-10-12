/**
 * The HUD can answer a card only through the owning domain's exact route.
 * There is no "approve whatever is pending" fallback.
 */
import { approveMissionToolCall, denyMissionToolCall } from "@/components/missions/api";
import { canDecide } from "@/lib/hudSemantics";
import {
  registerOwnerPasskey, verifyOwnerPasskey,
  type PasskeyCreationOptionsJSON, type PasskeyRequestOptionsJSON,
} from "@/lib/passkeyCeremony";
import type { HudApproval } from "@/types/hud";

export type HudDecision = "approve" | "deny";

export class HudApprovalReadOnlyError extends Error {
  constructor(card: HudApproval) {
    super(`approval ${card.approval_id} cannot be decided from the HUD`);
    this.name = "HudApprovalReadOnlyError";
  }
}

async function postGovernanceJson<T>(url: string, payload?: object): Promise<T> {
  const options: RequestInit = payload === undefined
    ? { method: "POST" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
  const response = await fetch(url, options);
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body as T;
}

async function postGovernanceDecision(url: string): Promise<void> {
  await postGovernanceJson(url);
}

/** Bootstrap is a one-time, separately provisioned secret; never persisted in the UI. */
export async function enrollProjectOwnerPasskey(bootstrapSecret: string): Promise<void> {
  const base = "/api/memory/governance/project-state/owner-passkey/enroll";
  const options = await postGovernanceJson<PasskeyCreationOptionsJSON>(
    `${base}/begin`, { bootstrap_secret: bootstrapSecret },
  );
  const credential = await registerOwnerPasskey(options);
  await postGovernanceJson(`${base}/finish`, {
    bootstrap_secret: bootstrapSecret, challenge: options.challenge, credential,
  });
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
    const base = `/api/memory/governance/project-state/${encodeURIComponent(String(card.queue_item_id))}/${encodeURIComponent(card.transaction_id as string)}/${encodeURIComponent(card.proposal_digest as string)}`;
    if (decision === "deny") {
      await postGovernanceDecision(`${base}/reject`);
    } else {
      // No plain approve request: an exact challenge and human WebAuthn gesture
      // are mandatory before the server can commit the owner decision.
      const options = await postGovernanceJson<PasskeyRequestOptionsJSON>(`${base}/passkey/begin`);
      const credential = await verifyOwnerPasskey(options);
      await postGovernanceJson(`${base}/approve`, {
        challenge: options.challenge, credential,
      });
    }
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
