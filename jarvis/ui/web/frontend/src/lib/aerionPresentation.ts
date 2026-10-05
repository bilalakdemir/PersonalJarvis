/**
 * Pure presentation helpers for the AERION HUD.
 *
 * React-free and side-effect-free. They only format and arrange what the
 * canonical HudSnapshot / event store already contain — no state is derived
 * that the backend reducer did not decide.
 */
import type { HudActivity, HudApproval, HudSnapshot } from "@/types/hud";

const NS_PER_MS = 1_000_000;

export type Tone = "cyan" | "gold" | "red" | "green" | "muted" | "violet";

export function nsToMs(ns: number | null | undefined): number | null {
  if (typeof ns !== "number" || !Number.isFinite(ns) || ns <= 0) return null;
  return ns / NS_PER_MS;
}

export function messageTsToMs(ts: number | null | undefined): number | null {
  if (typeof ts !== "number" || !Number.isFinite(ts) || ts <= 0) return null;
  if (ts < 1e11) return ts * 1000;
  if (ts > 1e14) return ts / NS_PER_MS;
  return ts;
}

export function clockFormatter(locale: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

export function formatHeaderDate(ms: number, locale: string): string {
  const parts = new Intl.DateTimeFormat(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).formatToParts(ms);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")}, ${get("day")} ${get("month")}`.replace(/\.(?=,)/, "");
}

export function sameLocalDay(a: number, b: number): boolean {
  const x = new Date(a);
  const y = new Date(b);
  return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate();
}

export function formatElapsed(fromMs: number | null, toMs: number): string {
  if (fromMs === null) return "";
  const s = Math.max(0, Math.floor((toMs - fromMs) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

export type AgeUnit = "now" | "min" | "hour" | "day";

export function ageOf(fromMs: number | null, nowMs: number): { unit: AgeUnit; n: number } | null {
  if (fromMs === null) return null;
  const s = Math.max(0, (nowMs - fromMs) / 1000);
  if (s < 60) return { unit: "now", n: 0 };
  if (s < 3600) return { unit: "min", n: Math.floor(s / 60) };
  if (s < 86400) return { unit: "hour", n: Math.floor(s / 3600) };
  return { unit: "day", n: Math.floor(s / 86400) };
}

export function riskTone(risk: string): Tone {
  const r = risk.toLowerCase();
  if (/(critical|high|danger|destructive|severe)/.test(r)) return "red";
  if (/(medium|moderate|elevated|warn)/.test(r)) return "gold";
  return "cyan";
}

export function statusTone(status: HudActivity["status"]): Tone {
  if (status === "running") return "cyan";
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  return "muted";
}

export type ApprovalFilter = "all" | "tools" | "proposals";

export function approvalMatches(card: HudApproval, filter: ApprovalFilter): boolean {
  if (filter === "all") return true;
  if (filter === "tools") return card.kind === "tool_call";
  return card.kind === "project_state" || card.kind === "memory_promotion";
}

export function orderActivities(items: readonly HudActivity[]): HudActivity[] {
  const rank = (s: HudActivity["status"]) => (s === "running" ? 0 : s === "failed" ? 1 : 2);
  return [...items].sort(
    (a, b) =>
      rank(a.status) - rank(b.status) ||
      (b.updated_at_ns || b.started_at_ns) - (a.updated_at_ns || a.started_at_ns),
  );
}

export interface TimelineEntry {
  id: string;
  atMs: number;
  label: string;
  detail: string;
  tone: Tone;
  source: "approval" | "operation" | "agent" | "memory" | "error";
}

export function todayTimeline(
  snapshot: HudSnapshot,
  nowMs: number,
  labels: { approval: string; error: string; memoryKind: (kind: string) => string },
  limit = 8,
): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  const push = (entry: Omit<TimelineEntry, "atMs"> & { atNs: number }) => {
    const atMs = nsToMs(entry.atNs);
    if (atMs === null || !sameLocalDay(atMs, nowMs)) return;
    const { atNs: _ignored, ...rest } = entry;
    out.push({ ...rest, atMs });
  };
  for (const card of snapshot.approval_requests) {
    push({
      id: `a:${card.approval_id}`,
      atNs: card.requested_at_ns,
      label: card.action,
      detail: labels.approval,
      tone: "gold",
      source: "approval",
    });
  }
  for (const op of snapshot.active_operations) {
    push({
      id: `o:${op.activity_id}`,
      atNs: op.started_at_ns,
      label: op.label,
      detail: op.detail,
      tone: statusTone(op.status),
      source: "operation",
    });
  }
  for (const agent of snapshot.agent_activity) {
    push({
      id: `g:${agent.activity_id}`,
      atNs: agent.started_at_ns,
      label: agent.label,
      detail: agent.detail,
      tone: statusTone(agent.status),
      source: "agent",
    });
  }
  for (const item of snapshot.memory_activity) {
    push({
      id: `m:${item.activity_id}`,
      atNs: item.at_ns,
      label: labels.memoryKind(item.kind),
      detail: item.subject,
      tone: "violet",
      source: "memory",
    });
  }
  if (snapshot.last_error) {
    const e = snapshot.last_error;
    push({
      id: `e:${e.trace_id}:${e.at_ns}`,
      atNs: e.at_ns,
      label: `${labels.error} · ${e.code}`,
      detail: e.message,
      tone: "red",
      source: "error",
    });
  }
  out.sort((a, b) => a.atMs - b.atMs);
  return out.slice(-limit);
}
