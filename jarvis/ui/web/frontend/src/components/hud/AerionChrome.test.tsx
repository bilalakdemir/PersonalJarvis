import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandDock } from "@/components/hud/AerionChrome";
import { AERION_COPY, type AerionSay } from "@/components/hud/aerionCopy";
import { useEventStore } from "@/store/events";
import type { HudActivity, HudProjectContext } from "@/types/hud";

const ws = vi.hoisted(() => ({ send: vi.fn() }));
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => ws,
}));

const originalFetch = globalThis.fetch;
const originalEnsureActiveThread = useEventStore.getState().ensureActiveThread;

const say: AerionSay = (key, vars) => {
  let text: string = AERION_COPY[key];
  if (!vars) return text;
  for (const [name, value] of Object.entries(vars)) {
    text = text.replace(`{${name}}`, String(value));
  }
  return text;
};

const noProject: HudProjectContext = {
  status: "NO_PROJECT",
  project_id: null,
  matched_by: "",
  detail: "",
  updated_at_ns: 0,
};

function missionActivity(id = "mission-1"): HudActivity {
  return {
    activity_id: `mission:${id}`,
    kind: "mission",
    label: "Mission",
    status: "running",
    trace_id: "trace",
    project_id: null,
    mission_id: id,
    task_id: null,
    worker_id: null,
    run_id: null,
    detail: "",
    request_detail: "",
    rationale: "",
    started_at_ns: 1,
    updated_at_ns: 1,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  ws.send.mockReset();
  useEventStore.setState({
    connected: true,
    wsWarming: false,
    dictating: false,
    dictationText: "",
    dictationCommitText: "",
    dictationCommitSeq: 0,
    chatThinking: false,
    ensureActiveThread: async () => "thread-1",
  });
});

afterEach(() => {
  cleanup();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  globalThis.fetch = originalFetch;
  useEventStore.setState({ ensureActiveThread: originalEnsureActiveThread });
});

describe("AERION CommandDock", () => {
  it("sends typed text through the canonical chat WebSocket path without navigating away", async () => {
    const navigate = vi.fn();
    render(
      <CommandDock
        say={say}
        onNavigate={navigate}
        operations={[]}
        project={null}
        projectContext={noProject}
      />,
    );

    const input = screen.getByTestId("aerion-command-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Run the status check" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("aerion-command-send"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(ws.send).toHaveBeenCalledWith({
      type: "message",
      kind: "text",
      content: "Run the status check",
      metadata: { thread_id: "thread-1" },
    });

    expect(navigate).not.toHaveBeenCalled();
    expect(input.value).toBe("");
    // The HUD displays project awareness but never injects project routing
    // metadata into the canonical chat wire path.
    expect(ws.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ project_id: expect.anything() }) }),
    );
  });

  it("uses the existing dictation command and mirrors the final transcript", () => {
    render(
      <CommandDock
        say={say}
        onNavigate={vi.fn()}
        operations={[]}
        project={null}
        projectContext={noProject}
      />,
    );

    fireEvent.click(screen.getByTestId("aerion-command-voice"));
    expect(ws.send).toHaveBeenCalledWith({
      type: "command",
      action: "stt_dictate",
      payload: { mode: "start" },
    });

    act(() => {
      useEventStore.getState().commitDictation("dictated command");
    });
    expect((screen.getByTestId("aerion-command-input") as HTMLInputElement).value).toBe(
      "dictated command",
    );
  });

  it("shows an exact stop control only for one unambiguous mission and calls its existing route", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200 })) as unknown as typeof fetch;
    render(
      <CommandDock
        say={say}
        onNavigate={vi.fn()}
        operations={[missionActivity("mission/7")]}
        project={null}
        projectContext={noProject}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("aerion-command-stop"));
      await Promise.resolve();
    });

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/missions/mission%2F7/cancel", {
      method: "POST",
    });
  });

  it("does not render a direct stop button when multiple targets are running", () => {
    render(
      <CommandDock
        say={say}
        onNavigate={vi.fn()}
        operations={[missionActivity("m1"), missionActivity("m2")]}
        project={null}
        projectContext={noProject}
      />,
    );
    expect(screen.queryByTestId("aerion-command-stop")).toBeNull();
  });
});
