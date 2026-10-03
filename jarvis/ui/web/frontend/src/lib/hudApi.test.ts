import { afterEach, describe, expect, it, vi } from "vitest";

import { HudApprovalReadOnlyError, decideHudApproval } from "@/lib/hudApi";
import type { HudApproval } from "@/types/hud";

function card(overrides: Partial<HudApproval> = {}): HudApproval {
  return {
    approval_id: "tool_call:mission/one:11111111-2222-4333-8444-555555555555:run_shell",
    kind: "tool_call",
    action: "run_shell",
    decision_channel: "mission_tool_api",
    reason: "risk_tier",
    risk_tier: "ask",
    target_preview: "",
    trace_id: "11111111-2222-4333-8444-555555555555",
    project_id: null,
    mission_id: "mission/one",
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

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockFetch() {
  const fn = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) });
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}

describe("decideHudApproval", () => {
  it("approves through the mission route for exactly this card", async () => {
    const fetchMock = mockFetch();
    await decideHudApproval(card(), "approve");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/missions/mission%2Fone/tool-approvals/11111111-2222-4333-8444-555555555555/approve",
    );
  });

  it("denies through the mission route for exactly this card", async () => {
    const fetchMock = mockFetch();
    await decideHudApproval(card(), "deny");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/missions/mission%2Fone/tool-approvals/11111111-2222-4333-8444-555555555555/deny",
    );
  });

  it("refuses every read-only channel without touching the network", async () => {
    const fetchMock = mockFetch();
    const readOnly = [
      card({ decision_channel: "chat_card", read_only_reason: "answered_in_chat" }),
      card({ decision_channel: "none", read_only_reason: "no_out_of_band_route" }),
      card({
        kind: "project_state",
        decision_channel: "none",
        mission_id: null,
        transaction_id: "tx",
        proposal_digest: "digest",
      }),
      card({ mission_id: null }),
    ];
    for (const item of readOnly) {
      await expect(decideHudApproval(item, "approve")).rejects.toBeInstanceOf(
        HudApprovalReadOnlyError,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
