import type { Toast } from "@/store/events";
import type { HudActivity, HudApproval, HudError, HudSnapshot } from "@/types/hud";

export type AerionProactiveCategory = "approval" | "completed" | "error";

export interface AerionProactiveNotice {
  key: string;
  category: AerionProactiveCategory;
  kind: Toast["kind"];
  subject: string;
  detail: string;
}

export const MAX_PROACTIVE_NOTICES_PER_TRANSITION = 3;
export const MAX_PROACTIVE_SEEN_KEYS = 128;

const COMPLETION_KINDS = new Set(["mission", "task", "workflow", "jarvis_agent"]);

function approvalKey(card: HudApproval): string {
  return `approval:${card.approval_id}`;
}

function outputKey(item: HudActivity): string {
  return `output:${item.activity_id}:${item.status}:${item.updated_at_ns || item.started_at_ns}`;
}

function errorKey(error: HudError): string {
  return `error:${error.scope}:${error.code}:${error.related_id ?? ""}:${error.at_ns}`;
}

function isLiveApproval(card: HudApproval, nowMs: number): boolean {
  return !(card.expires_at_ns > 0 && card.expires_at_ns <= nowMs * 1_000_000);
}

function completionSubject(item: HudActivity): string {
  if (item.kind === "mission") return item.mission_id ? `Mission ${item.mission_id.slice(0, 8)}` : "Mission";
  return item.label || item.kind;
}

function approvalSubject(card: HudApproval): string {
  return card.action || card.kind.replaceAll("_", " ");
}

function errorSubject(error: HudError): string {
  return error.message || error.code.replaceAll("_", " ");
}

/**
 * Derive high-signal notices from one accepted canonical HUD transition.
 *
 * This function does not own notification state and does not infer runtime
 * events. It compares two authoritative HudSnapshots. Initial/resync snapshots
 * and backend epoch changes are intentionally silent so a page load never
 * replays old work as fresh alerts.
 */
export function deriveAerionProactiveNotices(
  previous: HudSnapshot | null,
  current: HudSnapshot,
  nowMs: number,
): AerionProactiveNotice[] {
  if (previous === null || previous.epoch !== current.epoch) return [];

  const notices: AerionProactiveNotice[] = [];

  const previousErrorKey = previous.last_error ? errorKey(previous.last_error) : null;
  if (current.last_error) {
    const key = errorKey(current.last_error);
    if (key !== previousErrorKey) {
      notices.push({
        key,
        category: "error",
        kind:
          current.last_error.scope === "global" || !current.last_error.recoverable
            ? "error"
            : "warning",
        subject: errorSubject(current.last_error),
        detail: current.last_error.code,
      });
    }
  }

  const previousApprovals = new Set(previous.approval_requests.map(approvalKey));
  for (const card of current.approval_requests) {
    if (!isLiveApproval(card, nowMs)) continue;
    const key = approvalKey(card);
    if (previousApprovals.has(key)) continue;
    notices.push({
      key,
      category: "approval",
      kind: "warning",
      subject: approvalSubject(card),
      detail: card.reason || card.risk_tier,
    });
  }

  const previousOutputs = new Set(previous.recent_outputs.map(outputKey));
  for (const item of current.recent_outputs) {
    if (item.status !== "completed" || !COMPLETION_KINDS.has(item.kind)) continue;
    const key = outputKey(item);
    if (previousOutputs.has(key)) continue;
    notices.push({
      key,
      category: "completed",
      kind: "success",
      subject: completionSubject(item),
      detail: item.detail,
    });
  }

  return notices.slice(0, MAX_PROACTIVE_NOTICES_PER_TRANSITION);
}

/**
 * Tiny bounded idempotency ledger for repeated snapshots within one page life.
 * Snapshot comparison catches normal repeats; this additionally protects
 * against a logical item disappearing from a bounded list and later reappearing.
 */
export class AerionProactiveSeen {
  private readonly keys = new Set<string>();
  private readonly order: string[] = [];

  remember(key: string): boolean {
    if (this.keys.has(key)) return false;
    this.keys.add(key);
    this.order.push(key);
    while (this.order.length > MAX_PROACTIVE_SEEN_KEYS) {
      const oldest = this.order.shift();
      if (oldest) this.keys.delete(oldest);
    }
    return true;
  }

  clear(): void {
    this.keys.clear();
    this.order.length = 0;
  }

  get size(): number {
    return this.order.length;
  }
}
