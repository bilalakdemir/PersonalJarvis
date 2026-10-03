/**
 * Wire shape of the canonical HUD snapshot (N-15/N-17).
 *
 * Mirrors `jarvis/ui/hud/models.py::HudSnapshot.to_dict()`. The vocabulary
 * arrays below are the TS side of a five-layer value (AP-4) and are pinned to
 * the Python tuples by `tests/unit/ui/hud/test_hud_wire_parity.py` — add a
 * state there and here in the same change, or the parity test fails.
 *
 * The HUD is a projection: nothing in this file is authoritative, and the UI
 * never writes any of it back.
 */

export const HUD_PRIMARY_STATES = [
  "IDLE",
  "LISTENING",
  "THINKING",
  "WORKING",
  "WAITING_FOR_APPROVAL",
  "SPEAKING",
  "ERROR",
] as const;
export type HudPrimaryState = (typeof HUD_PRIMARY_STATES)[number];

export const HUD_CONNECTION_STATES = ["CONNECTED", "RECONNECTING", "DISCONNECTED"] as const;
export type HudConnectionState = (typeof HUD_CONNECTION_STATES)[number];

export const HUD_ACTIVITY_STATUSES = ["running", "completed", "failed", "cancelled"] as const;
export type HudActivityStatus = (typeof HUD_ACTIVITY_STATUSES)[number];

export const HUD_ERROR_SCOPES = ["global", "operation", "agent", "project", "component"] as const;
export type HudErrorScope = (typeof HUD_ERROR_SCOPES)[number];

/**
 * Who owns the decision for an approval card. Only `mission_tool_api` has an
 * out-of-band route the HUD may call — for exactly the card's
 * `(mission_id, trace_id)`. Every other channel renders read-only.
 */
export const HUD_DECISION_CHANNELS = ["mission_tool_api", "chat_card", "none"] as const;
export type HudDecisionChannel = (typeof HUD_DECISION_CHANNELS)[number];

export const HUD_APPROVAL_KINDS = ["tool_call", "project_state"] as const;
export type HudApprovalKind = (typeof HUD_APPROVAL_KINDS)[number];

export interface HudProject {
  project_id: string;
  project_name: string;
  current_task: string | null;
  state_revision: string;
  state_valid: boolean;
  issue_codes: string[];
  updated_at_ns: number;
}

export interface HudActivity {
  activity_id: string;
  kind: string;
  label: string;
  status: HudActivityStatus;
  trace_id: string;
  project_id: string | null;
  mission_id: string | null;
  task_id: string | null;
  worker_id: string | null;
  run_id: string | null;
  detail: string;
  started_at_ns: number;
  updated_at_ns: number;
}

export interface HudApproval {
  approval_id: string;
  kind: HudApprovalKind;
  action: string;
  decision_channel: HudDecisionChannel;
  reason: string;
  risk_tier: string;
  target_preview: string;
  trace_id: string;
  project_id: string | null;
  mission_id: string | null;
  worker_id: string | null;
  transaction_id: string | null;
  proposal_digest: string | null;
  /** Durable project-state proposals (N-14H): promotion-queue row + candidate. */
  queue_item_id: number | null;
  candidate_id: number | null;
  files_affected: string[];
  requested_at_ns: number;
  expires_at_ns: number;
  read_only_reason: string;
}

export interface HudMemoryActivity {
  activity_id: string;
  kind: string;
  status: string;
  subject: string;
  project_id: string | null;
  candidate_id: number | null;
  at_ns: number;
}

export interface HudComputerActivity {
  active: boolean;
  mission_ids: string[];
  phase: string;
  last_action_kind: string;
  screen_capture_active: boolean;
  capture_target_kind: string;
  updated_at_ns: number;
}

export interface HudError {
  scope: HudErrorScope;
  code: string;
  message: string;
  layer: string;
  trace_id: string;
  related_id: string | null;
  recoverable: boolean;
  at_ns: number;
}

export interface HudSnapshot {
  primary_state: HudPrimaryState;
  connection_state: HudConnectionState;
  voice_state: string;
  attention: string[];
  active_project: HudProject | null;
  active_operations: HudActivity[];
  approval_requests: HudApproval[];
  agent_activity: HudActivity[];
  memory_activity: HudMemoryActivity[];
  computer_activity: HudComputerActivity;
  last_error: HudError | null;
  updated_at_ns: number;
  epoch: string;
  revision: number;
  schema_version: number;
}

/** The top-level keys of the wire shape, for the parity test. */
export const HUD_SNAPSHOT_KEYS = [
  "primary_state",
  "connection_state",
  "voice_state",
  "attention",
  "active_project",
  "active_operations",
  "approval_requests",
  "agent_activity",
  "memory_activity",
  "computer_activity",
  "last_error",
  "updated_at_ns",
  "epoch",
  "revision",
  "schema_version",
] as const satisfies readonly (keyof HudSnapshot)[];

export const HUD_SCHEMA_VERSION = 1;
