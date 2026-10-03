import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { refreshHudSnapshot, useHudStore } from "@/store/hud";

function wire(revision: number, epoch = "e1", primary = "IDLE") {
  return {
    primary_state: primary,
    connection_state: "CONNECTED",
    voice_state: "IDLE",
    attention: [],
    active_project: null,
    active_operations: [],
    approval_requests: [],
    agent_activity: [],
    memory_activity: [],
    computer_activity: { active: false },
    last_error: null,
    updated_at_ns: 0,
    epoch,
    revision,
    schema_version: 1,
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  useHudStore.getState().reset();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("useHudStore", () => {
  it("keeps the newest snapshot by (epoch, revision)", () => {
    const store = useHudStore.getState();
    expect(store.applySnapshot(wire(3, "e1", "WORKING"))).toBe(true);
    expect(useHudStore.getState().applySnapshot(wire(2, "e1", "IDLE"))).toBe(false);
    expect(useHudStore.getState().snapshot?.primary_state).toBe("WORKING");
    expect(useHudStore.getState().applySnapshot(wire(0, "e2", "SPEAKING"))).toBe(true);
    expect(useHudStore.getState().snapshot?.primary_state).toBe("SPEAKING");
  });

  it("ignores malformed frames", () => {
    expect(useHudStore.getState().applySnapshot({ primary_state: "IDLE" })).toBe(false);
    expect(useHudStore.getState().snapshot).toBeNull();
  });

  it("resyncs with one read of the snapshot route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(wire(7, "e1", "WAITING_FOR_APPROVAL")),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    // Concurrent welcomes share one request.
    const [a, b] = await Promise.all([refreshHudSnapshot(), refreshHudSnapshot()]);
    expect(a && b).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/hud/snapshot");
    expect(useHudStore.getState().snapshot?.revision).toBe(7);
  });

  it("a failed resync leaves the current snapshot alone", async () => {
    useHudStore.getState().applySnapshot(wire(4));
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline")) as unknown as typeof fetch;
    expect(await refreshHudSnapshot()).toBe(false);
    expect(useHudStore.getState().snapshot?.revision).toBe(4);
  });
});
