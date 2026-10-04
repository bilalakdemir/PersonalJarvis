/**
 * Pure HUD client logic (N-17): parsing, ordering, expiry, presentation.
 *
 * Deliberately React-free and import-light so it is unit-testable in
 * isolation. The semantic decisions (which primary state wins, which
 * approval is which) were made by the backend reducer; this module only
 * decides whether an incoming snapshot is newer than the one on screen and
 * how a state is PRESENTED — it never re-derives what Jarvis is doing.
 */
import {
  HUD_APPROVAL_KINDS,
  HUD_CONNECTION_STATES,
  HUD_DECISION_CHANNELS,
  HUD_PRIMARY_STATES,
  type HudApproval,
  type HudConnectionState,
  type HudPrimaryState,
  type HudSnapshot,
} from "@/types/hud";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function includes<T extends string>(list: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (list as readonly string[]).includes(value);
}

/**
 * Structural check of a wire snapshot. Anything malformed is rejected whole
 * (fail closed): a half-understood approval card is worse than none.
 */
export function parseHudSnapshot(raw: unknown): HudSnapshot | null {
  if (!isRecord(raw)) return null;
  if (!includes(HUD_PRIMARY_STATES, raw.primary_state)) return null;
  if (!includes(HUD_CONNECTION_STATES, raw.connection_state)) return null;
  if (typeof raw.epoch !== "string" || typeof raw.revision !== "number") return null;
  for (const key of [
    "attention",
    "active_operations",
    "approval_requests",
    "agent_activity",
    "memory_activity",
  ]) {
    if (!Array.isArray(raw[key])) return null;
  }
  if (!isRecord(raw.computer_activity)) return null;
  for (const card of raw.approval_requests as unknown[]) {
    if (!isRecord(card)) return null;
    if (typeof card.approval_id !== "string" || card.approval_id.length === 0) return null;
    if (!includes(HUD_APPROVAL_KINDS, card.kind)) return null;
    if (!includes(HUD_DECISION_CHANNELS, card.decision_channel)) return null;
  }
  return raw as unknown as HudSnapshot;
}

/**
 * Ordering across the REST resync and the pushed frames. A new `epoch` is a
 * backend restart (revisions start over) and always wins; within an epoch an
 * older revision is a late arrival and is dropped.
 */
export function shouldAcceptSnapshot(
  current: HudSnapshot | null,
  incoming: HudSnapshot,
): boolean {
  if (current === null) return true;
  if (incoming.epoch !== current.epoch) return true;
  return incoming.revision >= current.revision;
}

/**
 * The transport state the WORKSPACE should show. The backend reports itself
 * as connected by definition; only the client knows its socket is down.
 */
export function effectiveConnection(
  connected: boolean,
  warming: boolean,
  hadConnection: boolean,
): HudConnectionState {
  if (connected) return "CONNECTED";
  if (warming || hadConnection) return "RECONNECTING";
  return "DISCONNECTED";
}

/** Approvals whose advertised window has not closed yet (display only). */
export function liveApprovals(snapshot: HudSnapshot, nowMs: number): HudApproval[] {
  const nowNs = nowMs * 1_000_000;
  return snapshot.approval_requests.filter(
    (card) => !(card.expires_at_ns > 0 && card.expires_at_ns <= nowNs),
  );
}

/**
 * Can THIS card be answered from the HUD? Only through the owning domain's
 * existing route, and only with the exact identity that route needs.
 */
export function canDecide(card: HudApproval): boolean {
  if (card.decision_channel === "mission_tool_api") {
    return (
      typeof card.mission_id === "string" &&
      card.mission_id.length > 0 &&
      typeof card.trace_id === "string" &&
      card.trace_id.length > 0
    );
  }
  if (card.decision_channel === "project_state_api") {
    return (
      typeof card.queue_item_id === "number" &&
      card.queue_item_id > 0 &&
      typeof card.transaction_id === "string" &&
      card.transaction_id.length > 0 &&
      typeof card.proposal_digest === "string" &&
      card.proposal_digest.length > 0
    );
  }
  if (card.decision_channel === "memory_promotion_api") {
    return (
      typeof card.candidate_id === "number" &&
      card.candidate_id > 0 &&
      typeof card.proposal_digest === "string" &&
      card.proposal_digest.length > 0
    );
  }
  return false;
}

export interface HudPanels {
  approvals: boolean;
  project: boolean;
  activity: boolean;
  computer: boolean;
  memory: boolean;
  error: boolean;
}

/** Contextual panels: a panel exists only while it has something to say. */
export function visiblePanels(snapshot: HudSnapshot, nowMs: number): HudPanels {
  const computer = snapshot.computer_activity;
  return {
    approvals: liveApprovals(snapshot, nowMs).length > 0,
    project: snapshot.active_project !== null,
    activity: snapshot.active_operations.length > 0 || snapshot.agent_activity.length > 0,
    computer: Boolean(computer?.active || computer?.screen_capture_active),
    memory: snapshot.memory_activity.length > 0,
    error: snapshot.last_error !== null,
  };
}

/**
 * How each primary state is presented. Every state differs in THREE ways that
 * survive reduced motion: its label, its tone token, and its ring pattern —
 * so no state relies on animation alone to be told apart.
 */
export interface ReactorStyle {
  labelKey: string;
  tone: "muted" | "accent" | "info" | "warning" | "success" | "destructive";
  pattern: "solid" | "dashed" | "dotted" | "double" | "segmented" | "broken";
}


export type AerionVisualState =
  | "OFF"
  | "STANDBY"
  | "LISTENING"
  | "THINKING"
  | "WORKING"
  | "WAITING_FOR_APPROVAL"
  | "SPEAKING"
  | "ERROR"
  | "COMPLETED";

const AERION_COMPLETION_WINDOW_MS = 1500;

/**
 * AERION's cinematic state is presentation only. It is derived from the
 * canonical HUD snapshot plus the client connection state and never becomes a
 * second operational state machine.
 */
export function aerionVisualState(
  snapshot: HudSnapshot,
  connection: HudConnectionState,
  nowMs: number,
): AerionVisualState {
  if (connection === "DISCONNECTED") return "OFF";

  // Safety/attention states outrank a cosmetic completion flare.
  if (snapshot.primary_state === "ERROR") return "ERROR";
  if (snapshot.primary_state === "WAITING_FOR_APPROVAL") return "WAITING_FOR_APPROVAL";

  const newestCompletedNs = [...snapshot.active_operations, ...snapshot.agent_activity]
    .filter((item) => item.status === "completed")
    .reduce((latest, item) => Math.max(latest, item.updated_at_ns || item.started_at_ns || 0), 0);
  if (newestCompletedNs > 0) {
    const ageMs = nowMs - newestCompletedNs / 1_000_000;
    if (ageMs >= 0 && ageMs <= AERION_COMPLETION_WINDOW_MS) return "COMPLETED";
  }

  if (snapshot.primary_state === "IDLE") return "STANDBY";
  return snapshot.primary_state;
}

export const REACTOR_STYLES: Record<HudPrimaryState, ReactorStyle> = {
  IDLE: { labelKey: "hud.state.idle", tone: "muted", pattern: "solid" },
  LISTENING: { labelKey: "hud.state.listening", tone: "accent", pattern: "double" },
  THINKING: { labelKey: "hud.state.thinking", tone: "accent", pattern: "dotted" },
  WORKING: { labelKey: "hud.state.working", tone: "info", pattern: "segmented" },
  WAITING_FOR_APPROVAL: {
    labelKey: "hud.state.waiting_for_approval",
    tone: "warning",
    pattern: "dashed",
  },
  SPEAKING: { labelKey: "hud.state.speaking", tone: "success", pattern: "double" },
  ERROR: { labelKey: "hud.state.error", tone: "destructive", pattern: "broken" },
};

/** Short, stable id fragment for display (never the full opaque id). */
export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return "";
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

/** Seconds left on an approval window, or null when none was advertised. */
export function secondsLeft(card: HudApproval, nowMs: number): number | null {
  if (card.expires_at_ns <= 0) return null;
  return Math.max(0, Math.round((card.expires_at_ns / 1_000_000 - nowMs) / 1000));
}
