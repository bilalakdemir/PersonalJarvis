/**
 * HudView (N-17): a projection of the canonical snapshot. Readable status,
 * contextual panels, exact approval identity, read-only cards for channels the
 * HUD cannot answer, and nothing that sends a generic "yes".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { setUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHudStore } from "@/store/hud";
import type { HudApproval, HudSnapshot } from "@/types/hud";
import { HudView } from "@/views/HudView";

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

function card(overrides: Partial<HudApproval>): HudApproval {
  return {
    approval_id: "x",
    kind: "tool_call",
    action: "tool",
    decision_channel: "mission_tool_api",
    reason: "risk_tier",
    risk_tier: "ask",
    target_preview: "",
    trace_id: "",
    project_id: null,
    mission_id: null,
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

const originalFetch = globalThis.fetch;
let calls: { url: string; method: string }[] = [];

beforeEach(() => {
  setUiLanguage("en");
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: (init?.method ?? "GET").toUpperCase() });
    // The mount-time resync answers with the snapshot already in the store.
    const body =
      String(input) === "/api/hud/snapshot" ? useHudStore.getState().snapshot : { ok: true };
    return { ok: true, status: 200, json: async () => body } as Response;
  }) as unknown as typeof fetch;
  useEventStore.setState({ connected: true, wsWarming: false, messages: [] });
  useHudStore.getState().reset();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("HudView", () => {
  it("renders the approved AERION command-core shell without changing HUD semantics", () => {
    useHudStore.getState().applySnapshot(snapshot({ primary_state: "IDLE" }));
    render(<HudView />);

    expect(screen.getByTestId("aerion-wordmark").textContent).toBe("AERION");
    expect(screen.getByTestId("aerion-command-grid")).toBeTruthy();
    expect(screen.getByTestId("aerion-zone-left")).toBeTruthy();
    expect(screen.getByTestId("aerion-zone-center")).toBeTruthy();
    expect(screen.getByTestId("aerion-zone-right")).toBeTruthy();
    expect(screen.getByTestId("hud-reactor").getAttribute("data-state")).toBe("IDLE");
    expect(screen.getByTestId("hud-reactor").getAttribute("data-visual-state")).toBe("STANDBY");
  });

  it("renders a readable status for the primary state, independent of motion", () => {
    useHudStore.getState().applySnapshot(snapshot({ primary_state: "WORKING" }));
    render(<HudView />);
    expect(screen.getByTestId("hud-state-label").textContent).toBe("Working");
    expect(screen.getByTestId("hud-reactor").getAttribute("data-pattern")).toBe("segmented");
    expect(screen.getByTestId("hud-status-line").textContent).toContain("Connected");
  });

  it("is quiet when nothing is going on — panels are contextual", () => {
    useHudStore.getState().applySnapshot(snapshot());
    render(<HudView />);
    expect(screen.getByTestId("hud-quiet")).toBeTruthy();
    expect(screen.queryByTestId("hud-panel-approvals")).toBeNull();
    expect(screen.queryByTestId("hud-panel-activity")).toBeNull();
    expect(screen.queryByTestId("hud-panel-computer")).toBeNull();
  });

  it("approves exactly the clicked card through its mission route", async () => {
    useHudStore.getState().applySnapshot(
      snapshot({
        primary_state: "WAITING_FOR_APPROVAL",
        attention: ["approval"],
        approval_requests: [
          card({
            approval_id: "tool_call:m-a:trace-a:rm",
            action: "rm",
            mission_id: "m-a",
            trace_id: "trace-a",
          }),
          card({
            approval_id: "tool_call:m-b:trace-b:rm",
            action: "rm",
            mission_id: "m-b",
            trace_id: "trace-b",
          }),
        ],
      }),
    );
    render(<HudView />);
    const second = screen
      .getAllByTestId("hud-approval-card")
      .find((el: HTMLElement) => el.getAttribute("data-approval-id") === "tool_call:m-b:trace-b:rm")!;
    fireEvent.click(within(second).getByTestId("hud-approve"));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/tool-approvals/"))).toBe(true),
    );
    const decisions = calls.filter((c) => c.url.includes("/tool-approvals/"));
    expect(decisions).toEqual([
      { url: "/api/missions/m-b/tool-approvals/trace-b/approve", method: "POST" },
    ]);
  });

  it("renders chat and project-state cards read-only, with no buttons", () => {
    useHudStore.getState().applySnapshot(
      snapshot({
        primary_state: "WAITING_FOR_APPROVAL",
        approval_requests: [
          card({
            approval_id: "tool_call:-:t:write",
            action: "write",
            decision_channel: "chat_card",
            read_only_reason: "answered_in_chat",
            trace_id: "t",
          }),
          card({
            approval_id: "project_state:p:tx:d",
            kind: "project_state",
            action: "Project state change",
            decision_channel: "none",
            read_only_reason: "project_state_route_unavailable",
            project_id: "p",
            transaction_id: "tx",
            proposal_digest: "d",
          }),
        ],
      }),
    );
    render(<HudView />);
    expect(screen.queryByTestId("hud-approve")).toBeNull();
    expect(screen.queryByTestId("hud-deny")).toBeNull();
    expect(screen.getAllByTestId("hud-approval-readonly")).toHaveLength(2);
  });

  it("shows the active project with its CURRENT task", () => {
    useHudStore.getState().applySnapshot(
      snapshot({
        active_project: {
          project_id: "p1",
          project_name: "Atlas",
          current_task: "T-2 ship the HUD",
          state_revision: "r2",
          state_valid: true,
          issue_codes: [],
          updated_at_ns: 0,
        },
      }),
    );
    render(<HudView />);
    const panel = screen.getByTestId("hud-panel-project");
    expect(panel.textContent).toContain("Atlas");
    expect(panel.textContent).toContain("T-2 ship the HUD");
  });

  it("renders OFF only from an actually disconnected client", () => {
    useHudStore.getState().applySnapshot(snapshot({ primary_state: "IDLE" }));
    useEventStore.setState({ connected: false, wsWarming: false });
    render(<HudView />);
    expect(screen.getByTestId("hud-reactor").getAttribute("data-visual-state")).toBe("OFF");
  });

  it("says the connection is being re-established while the socket is down", () => {
    useHudStore.getState().applySnapshot(snapshot());
    useEventStore.setState({ connected: false, wsWarming: false });
    render(<HudView />);
    expect(screen.getByTestId("hud-status-line").textContent).toContain("Reconnecting");
  });

  it("resyncs from the snapshot route when it mounts", async () => {
    render(<HudView />);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/hud/snapshot")).toBe(true));
  });
});
