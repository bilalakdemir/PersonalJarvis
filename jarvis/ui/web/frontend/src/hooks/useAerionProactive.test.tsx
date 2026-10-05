import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAerionProactive } from "@/hooks/useAerionProactive";
import { useEventStore } from "@/store/events";
import { useHudStore } from "@/store/hud";
import type { HudApproval, HudSnapshot } from "@/types/hud";

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

function approval(id: string): HudApproval {
  return {
    approval_id: id,
    kind: "tool_call",
    action: "Write file",
    decision_channel: "mission_tool_api",
    reason: "risk_tier",
    risk_tier: "ask",
    target_preview: "",
    trace_id: "trace",
    project_id: null,
    mission_id: "mission",
    worker_id: null,
    transaction_id: null,
    proposal_digest: null,
    queue_item_id: null,
    candidate_id: null,
    files_affected: [],
    requested_at_ns: 1,
    expires_at_ns: 0,
    read_only_reason: "",
  };
}

function Probe({ enabled = true }: { enabled?: boolean }) {
  useAerionProactive(enabled);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  useHudStore.getState().reset();
  useEventStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

describe("useAerionProactive", () => {
  it("does not replay the initial HUD snapshot as fresh notifications", () => {
    useHudStore.getState().applySnapshot(
      snapshot({ approval_requests: [approval("existing")] }),
    );

    render(<Probe />);

    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("pushes a new canonical approval into the existing global toast layer", () => {
    useHudStore.getState().applySnapshot(snapshot());
    render(<Probe />);

    act(() => {
      useHudStore.getState().applySnapshot(
        snapshot({
          revision: 2,
          approval_requests: [approval("new-approval")],
        }),
      );
    });

    const toasts = useEventStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.kind).toBe("warning");
    expect(toasts[0]?.message).toContain("Approval required");
    expect(toasts[0]?.message).toContain("Write file");
  });

  it("keeps a disabled detached window silent and does not replay on re-enable", () => {
    useHudStore.getState().applySnapshot(snapshot());
    const view = render(<Probe enabled={false} />);

    act(() => {
      useHudStore.getState().applySnapshot(
        snapshot({
          revision: 2,
          approval_requests: [approval("while-detached")],
        }),
      );
    });
    expect(useEventStore.getState().toasts).toEqual([]);

    view.rerender(<Probe enabled />);
    expect(useEventStore.getState().toasts).toEqual([]);

    act(() => {
      useHudStore.getState().applySnapshot(
        snapshot({
          revision: 3,
          approval_requests: [
            approval("while-detached"),
            approval("after-enabled"),
          ],
        }),
      );
    });

    expect(useEventStore.getState().toasts).toHaveLength(1);
    expect(useEventStore.getState().toasts[0]?.message).toContain("Write file");
  });
});
