import { describe, expect, it } from "vitest";
import { WSAudioLevel, WSCommand, WSHudSnapshot, WSOutbound, WSWelcome } from "./ws";

describe("WSCommand mission.inject", () => {
  it("validates a mission.inject command", () => {
    const parsed = WSCommand.parse({
      type: "command",
      action: "mission.inject",
      payload: { slug: "s", utterance: "u", status: "success" },
    });
    expect(parsed.action).toBe("mission.inject");
  });
});

describe("WSWelcome", () => {
  it("does not retain legacy session tokens", () => {
    const parsed = WSWelcome.parse({
      type: "welcome",
      session_id: "session-1",
      version: "1.0.0",
      token: "must-not-reach-the-client",
    });

    expect(parsed).toEqual({
      type: "welcome",
      session_id: "session-1",
      version: "1.0.0",
    });
    expect("token" in parsed).toBe(false);
  });
});

describe("WSAudioLevel", () => {
  it("accepts only normalized microphone samples", () => {
    expect(WSAudioLevel.parse({ type: "audio.level", input: 0.72 }).input).toBe(0.72);
    expect(WSAudioLevel.safeParse({ type: "audio.level", input: 1.2 }).success).toBe(
      false,
    );
  });
});

describe("WSHudSnapshot", () => {
  it("is part of the outbound union and keeps the snapshot body", () => {
    const frame = { type: "hud.snapshot", snapshot: { primary_state: "IDLE", revision: 3 } };
    expect(WSHudSnapshot.parse(frame).snapshot.revision).toBe(3);
    expect(WSOutbound.parse(frame).type).toBe("hud.snapshot");
  });

  it("rejects a frame without a snapshot object", () => {
    expect(WSHudSnapshot.safeParse({ type: "hud.snapshot" }).success).toBe(false);
  });
});
