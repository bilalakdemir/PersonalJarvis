import { describe, expect, it } from "vitest";

import {
  REACTOR_STYLES,
  canDecide,
  effectiveConnection,
  liveApprovals,
  parseHudSnapshot,
  secondsLeft,
  shouldAcceptSnapshot,
  visiblePanels,
} from "@/lib/hudSemantics";
import { HUD_PRIMARY_STATES, type HudApproval, type HudSnapshot } from "@/types/hud";

function snapshot(overrides: Partial<HudSnapshot> = {}): HudSnapshot {
  return {
    primary_state: "IDLE",
    connection_state: "CONNECTED",
    voice_state: "IDLE",
    attention: [],
    active_project: null,
    active_operations: [],
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
    approval_id: "tool_call:m1:t1:rm",
    kind: "tool_call",
    action: "rm",
    decision_channel: "mission_tool_api",
    reason: "risk_tier",
    risk_tier: "ask",
    target_preview: "build/",
    trace_id: "t1",
    project_id: null,
    mission_id: "m1",
    worker_id: null,
    transaction_id: null,
    proposal_digest: null,
    queue_item_id: null,
    candidate_id: null,
    files_affected: [],
    requested_at_ns: 0,
    expires_at_ns: 0,
    read_only_reason: "",
    ...overrides,
  };
}

describe("parseHudSnapshot", () => {
  it("accepts a well-formed snapshot", () => {
    expect(parseHudSnapshot(snapshot())).not.toBeNull();
  });

  it("rejects an unknown primary state (fail closed)", () => {
    expect(parseHudSnapshot({ ...snapshot(), primary_state: "DANCING" })).toBeNull();
  });

  it("rejects a malformed approval card instead of half-rendering it", () => {
    const bad = snapshot({
      approval_requests: [{ ...approval(), decision_channel: "yes_to_everything" as never }],
    });
    expect(parseHudSnapshot(bad)).toBeNull();
    expect(parseHudSnapshot(snapshot({ approval_requests: [approval({ approval_id: "" })] }))).toBeNull();
  });

  it("rejects non-objects and missing ordering keys", () => {
    expect(parseHudSnapshot(null)).toBeNull();
    expect(parseHudSnapshot([])).toBeNull();
    const noEpoch: Record<string, unknown> = { ...snapshot() };
    delete noEpoch.epoch;
    expect(parseHudSnapshot(noEpoch)).toBeNull();
  });
});

describe("shouldAcceptSnapshot (reconnect/resync ordering)", () => {
  it("accepts the first snapshot", () => {
    expect(shouldAcceptSnapshot(null, snapshot())).toBe(true);
  });

  it("drops a late, older revision within the same epoch", () => {
    expect(shouldAcceptSnapshot(snapshot({ revision: 5 }), snapshot({ revision: 4 }))).toBe(false);
    expect(shouldAcceptSnapshot(snapshot({ revision: 5 }), snapshot({ revision: 5 }))).toBe(true);
    expect(shouldAcceptSnapshot(snapshot({ revision: 5 }), snapshot({ revision: 6 }))).toBe(true);
  });

  it("always accepts a new epoch (backend restart resets revisions)", () => {
    expect(
      shouldAcceptSnapshot(snapshot({ revision: 50 }), snapshot({ epoch: "e2", revision: 0 })),
    ).toBe(true);
  });
});

describe("effectiveConnection", () => {
  it("is independent of the primary state and honest about the socket", () => {
    expect(effectiveConnection(true, false, true)).toBe("CONNECTED");
    expect(effectiveConnection(false, true, false)).toBe("RECONNECTING");
    expect(effectiveConnection(false, false, true)).toBe("RECONNECTING");
    expect(effectiveConnection(false, false, false)).toBe("DISCONNECTED");
  });
});

describe("approvals", () => {
  it("only a mission card with its exact identity can be decided here", () => {
    expect(canDecide(approval())).toBe(true);
    expect(canDecide(approval({ mission_id: null }))).toBe(false);
    expect(canDecide(approval({ trace_id: "" }))).toBe(false);
    expect(canDecide(approval({ decision_channel: "chat_card" }))).toBe(false);
    expect(
      canDecide(
        approval({
          kind: "project_state",
          decision_channel: "none",
          transaction_id: "tx",
          proposal_digest: "d",
        }),
      ),
    ).toBe(false);
  });

  it("hides cards whose advertised window has closed", () => {
    const nowMs = 1_000_000;
    const open = approval({ approval_id: "a", expires_at_ns: (nowMs + 5_000) * 1_000_000 });
    const closed = approval({ approval_id: "b", expires_at_ns: (nowMs - 1) * 1_000_000 });
    const forever = approval({ approval_id: "c", expires_at_ns: 0 });
    const live = liveApprovals(snapshot({ approval_requests: [open, closed, forever] }), nowMs);
    expect(live.map((card) => card.approval_id)).toEqual(["a", "c"]);
    expect(secondsLeft(open, nowMs)).toBe(5);
    expect(secondsLeft(forever, nowMs)).toBeNull();
  });
});

describe("visiblePanels (contextual, not permanent clutter)", () => {
  it("shows nothing for a quiet snapshot", () => {
    expect(Object.values(visiblePanels(snapshot(), 0)).some(Boolean)).toBe(false);
  });

  it("shows exactly the panels that have content", () => {
    const panels = visiblePanels(
      snapshot({
        approval_requests: [approval()],
        computer_activity: { ...snapshot().computer_activity, active: true },
      }),
      0,
    );
    expect(panels).toEqual({
      approvals: true,
      project: false,
      activity: false,
      computer: true,
      memory: false,
      error: false,
    });
  });
});

describe("REACTOR_STYLES", () => {
  it("distinguishes every state without motion (label, tone+pattern)", () => {
    const labels = new Set<string>();
    const looks = new Set<string>();
    for (const state of HUD_PRIMARY_STATES) {
      const style = REACTOR_STYLES[state];
      labels.add(style.labelKey);
      looks.add(`${style.tone}/${style.pattern}`);
    }
    expect(labels.size).toBe(HUD_PRIMARY_STATES.length);
    expect(looks.size).toBe(HUD_PRIMARY_STATES.length);
  });
});
