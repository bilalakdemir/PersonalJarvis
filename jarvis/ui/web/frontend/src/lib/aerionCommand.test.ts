import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelHudTarget,
  resolveHudCancelTarget,
  stageChatFiles,
} from "@/lib/aerionCommand";
import { useEventStore } from "@/store/events";
import type { HudActivity } from "@/types/hud";

const originalFetch = globalThis.fetch;
const originalEnsureActiveThread = useEventStore.getState().ensureActiveThread;

function activity(overrides: Partial<HudActivity> = {}): HudActivity {
  return {
    activity_id: "op-1",
    kind: "mission",
    label: "Work",
    status: "running",
    trace_id: "trace-1",
    project_id: null,
    mission_id: "mission-1",
    task_id: null,
    worker_id: null,
    run_id: null,
    detail: "",
    request_detail: "",
    rationale: "",
    started_at_ns: 1,
    updated_at_ns: 1,
    ...overrides,
  };
}

beforeEach(() => {
  useEventStore.setState({
    ensureActiveThread: async () => "thread-7",
  });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  useEventStore.setState({
    ensureActiveThread: originalEnsureActiveThread,
  });
});

describe("stageChatFiles", () => {
  it("uses the existing chat drop intake and the active text thread", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/chat/drop");
      expect(init?.method).toBe("POST");
      return {
        ok: true,
        status: 200,
        json: async () => ({ dispatched: true, files: ["report.pdf"] }),
      } as Response;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const names = await stageChatFiles([
      new File(["report"], "report.pdf", { type: "application/pdf" }),
    ]);

    expect(names).toEqual(["report.pdf"]);
    const body = fetchMock.mock.calls[0]?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get("thread_id")).toBe("thread-7");
    expect(form.get("surface")).toBe("aerion");
    const staged = form.getAll("files");
    expect(staged).toHaveLength(1);
    expect((staged[0] as File).name).toBe("report.pdf");
  });

  it("fails closed when the drop intake does not capture the file", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ dispatched: false, files: [] }),
    })) as unknown as typeof fetch;

    await expect(stageChatFiles([new File(["x"], "x.txt")])).rejects.toThrow(
      "chat-drop-not-captured",
    );
  });
});

describe("resolveHudCancelTarget", () => {
  it("deduplicates multiple activities belonging to one mission", () => {
    const resolution = resolveHudCancelTarget([
      activity({ activity_id: "a" }),
      activity({ activity_id: "b" }),
    ]);
    expect(resolution).toEqual({
      target: { kind: "mission", id: "mission-1" },
      ambiguous: false,
    });
  });

  it("refuses to guess when more than one runtime target is active", () => {
    const resolution = resolveHudCancelTarget([
      activity({ mission_id: "mission-1" }),
      activity({ activity_id: "b", mission_id: null, task_id: "task-2" }),
    ]);
    expect(resolution.target).toBeNull();
    expect(resolution.ambiguous).toBe(true);
  });

  it("selects a task only when no mission identity owns the activity", () => {
    const resolution = resolveHudCancelTarget([
      activity({ mission_id: null, task_id: "task-9", kind: "task" }),
    ]);
    expect(resolution.target).toEqual({ kind: "task", id: "task-9" });
  });
});

describe("cancelHudTarget", () => {
  it("posts only to the exact existing mission/task cancel route", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: String(init?.method ?? "GET") });
      return { ok: true, status: 200 } as Response;
    }) as unknown as typeof fetch;

    await cancelHudTarget({ kind: "mission", id: "m/one" });
    await cancelHudTarget({ kind: "task", id: "task one" });

    expect(calls).toEqual([
      { url: "/api/missions/m%2Fone/cancel", method: "POST" },
      { url: "/api/tasks/task%20one/cancel", method: "POST" },
    ]);
  });
});
