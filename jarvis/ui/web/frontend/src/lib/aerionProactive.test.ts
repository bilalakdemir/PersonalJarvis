import { describe, expect, it } from "vitest";

import {
  AerionProactiveSeen,
  MAX_PROACTIVE_NOTICES_PER_TRANSITION,
  MAX_PROACTIVE_SEEN_KEYS,
  deriveAerionProactiveNotices,
} from "@/lib/aerionProactive";
import type {
  HudActivity,
  HudApproval,
  HudError,
  HudSnapshot,
} from "@/types/hud";

function snapshot(overrides: Partial<HudSnapshot> = {}): HudSnapshot {
  return {
    primary_state: "IDLE",
    connection_state: "CONNECTED",
    voice_state: "IDLE",
    attention: [],
    project_context: {
      status: "NO_PROJECT",
      project_id: null,
      matched_by: "",
      detail: "",
      updated_at_ns: 0,
    },
    active_project: null,
    active_operations: [],
    recent_outputs: [],
    approval_requests: [],
    agent_activity: [],
    memory_activity: [],
    computer_activity: {
      active: false,
      mission_ids: [],
      phase: "",
      last_action_kind: "",
      screen_capture_active: false,
      capture_target_kind: "",
      updated_at_ns: 0,
    },
    last_error: null,
    updated_at_ns: 0,
    epoch: "e1",
    revision: 1,
    schema_version: 1,
    ...overrides,
  };
}

function approval(overrides: Partial<HudApproval> = {}): HudApproval {
  return {
    approval_id: "approval-1",
    kind: "tool_call",
    action: "Write file",
    decision_channel: "mission_tool_api",
    reason: "risk_tier",
    risk_tier: "ask",
    target_preview: "",
    trace_id: "trace-1",
    project_id: null,
    mission_id: "mission-1",
    worker_id: null,
    transaction_id: null,
    proposal_digest: null,
    queue_item_id: null,
    candidate_id: null,
    files_affected: [],
    requested_at_ns: 1,
    expires_at_ns: 0,
    read_only_reason: "",
    ...overrides,
  };
}

function activity(overrides: Partial<HudActivity> = {}): HudActivity {
  return {
    activity_id: "mission:mission-1",
    kind: "mission",
    label: "Mission",
    status: "completed",
    trace_id: "trace-1",
    project_id: null,
    mission_id: "mission-1",
    task_id: null,
    worker_id: null,
    run_id: null,
    detail: "",
    started_at_ns: 1,
    updated_at_ns: 2,
    ...overrides,
  };
}

function error(overrides: Partial<HudError> = {}): HudError {
  return {
    scope: "operation",
    code: "task_failed",
    message: "Task could not complete",
    layer: "tasks",
    trace_id: "trace-2",
    related_id: "task:t1",
    recoverable: true,
    at_ns: 3,
    ...overrides,
  };
}

describe("deriveAerionProactiveNotices", () => {
  it("treats initial and new-epoch snapshots as silent baselines", () => {
    const current = snapshot({
      approval_requests: [approval()],
      recent_outputs: [activity()],
      last_error: error(),
    });

    expect(deriveAerionProactiveNotices(null, current, Date.now())).toEqual([]);
    expect(
      deriveAerionProactiveNotices(snapshot({ epoch: "old" }), current, Date.now()),
    ).toEqual([]);
  });

  it("emits errors first, then new approvals, then high-signal completions", () => {
    const previous = snapshot({ revision: 1 });
    const current = snapshot({
      revision: 2,
      approval_requests: [approval()],
      recent_outputs: [activity()],
      last_error: error(),
    });

    const notices = deriveAerionProactiveNotices(previous, current, Date.now());

    expect(notices.map((item) => item.category)).toEqual([
      "error",
      "approval",
      "completed",
    ]);
    expect(notices[0]?.kind).toBe("warning");
    expect(notices[1]?.subject).toBe("Write file");
    expect(notices[2]?.subject).toContain("Mission");
  });

  it("does not invent notices for expired approvals, tool completions, failures, or repeats", () => {
    const nowMs = 10_000;
    const repeated = approval({ approval_id: "same" });
    const previous = snapshot({
      approval_requests: [repeated],
      recent_outputs: [activity({ activity_id: "mission:old" })],
      last_error: error({ at_ns: 4 }),
    });
    const current = snapshot({
      revision: 2,
      approval_requests: [
        repeated,
        approval({
          approval_id: "expired",
          expires_at_ns: (nowMs - 1) * 1_000_000,
        }),
      ],
      recent_outputs: [
        activity({ activity_id: "mission:old" }),
        activity({ activity_id: "tool:new", kind: "tool", label: "read_file" }),
        activity({
          activity_id: "task:failed",
          kind: "task",
          status: "failed",
          label: "Failed task",
        }),
      ],
      last_error: error({ at_ns: 4 }),
    });

    expect(deriveAerionProactiveNotices(previous, current, nowMs)).toEqual([]);
  });

  it("caps a burst so one snapshot cannot flood the existing toast layer", () => {
    const current = snapshot({
      revision: 2,
      approval_requests: Array.from({ length: 6 }, (_, index) =>
        approval({
          approval_id: `a-${index}`,
          action: `Action ${index}`,
        }),
      ),
    });

    expect(
      deriveAerionProactiveNotices(snapshot(), current, Date.now()),
    ).toHaveLength(MAX_PROACTIVE_NOTICES_PER_TRANSITION);
  });
});

describe("AerionProactiveSeen", () => {
  it("deduplicates logical events and remains bounded", () => {
    const seen = new AerionProactiveSeen();
    expect(seen.remember("x")).toBe(true);
    expect(seen.remember("x")).toBe(false);

    for (let index = 0; index < MAX_PROACTIVE_SEEN_KEYS + 10; index += 1) {
      seen.remember(`key-${index}`);
    }
    expect(seen.size).toBe(MAX_PROACTIVE_SEEN_KEYS);
  });
});
