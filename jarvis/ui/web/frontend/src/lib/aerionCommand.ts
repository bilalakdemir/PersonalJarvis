import { useEventStore } from "@/store/events";
import type { HudActivity } from "@/types/hud";

export interface HudCancelTarget {
  kind: "mission" | "task";
  id: string;
}

export interface HudCancelResolution {
  target: HudCancelTarget | null;
  ambiguous: boolean;
}

/**
 * Stage files for the next real chat turn through the existing drop-intake.
 *
 * The endpoint only captures context; it never starts a brain turn. The next
 * text message still travels through the canonical WebSocket chat path.
 */
export async function stageChatFiles(files: readonly File[]): Promise<string[]> {
  if (files.length === 0) return [];

  let threadId = "default";
  try {
    threadId = await useEventStore.getState().ensureActiveThread();
  } catch {
    // The chat store may be unavailable on a headless/minimal host. The drop
    // endpoint and WebSocket chat both retain their documented default-thread
    // fallback, so do not invent another persistence path here.
  }

  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append("thread_id", threadId || "default");
  form.append("surface", "aerion");

  const response = await fetch("/api/chat/drop", {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(`chat-drop-failed:${response.status}`);

  const payload = (await response.json()) as {
    dispatched?: boolean;
    files?: unknown;
  };
  if (payload.dispatched !== true) throw new Error("chat-drop-not-captured");

  return Array.isArray(payload.files)
    ? payload.files.filter((name): name is string => typeof name === "string" && name.length > 0)
    : files.map((file) => file.name);
}

/**
 * Pick an exact cancellation target from the canonical HUD operations.
 *
 * One mission may surface through multiple runtime activities, so identities
 * are deduplicated. If more than one distinct mission/task is running, the
 * dock refuses to guess which one the user meant.
 */
export function resolveHudCancelTarget(
  activities: readonly HudActivity[],
): HudCancelResolution {
  const targets = new Map<string, HudCancelTarget>();

  for (const activity of activities) {
    if (activity.status !== "running") continue;
    if (activity.mission_id) {
      const target: HudCancelTarget = { kind: "mission", id: activity.mission_id };
      targets.set(`${target.kind}:${target.id}`, target);
      continue;
    }
    if (activity.task_id) {
      const target: HudCancelTarget = { kind: "task", id: activity.task_id };
      targets.set(`${target.kind}:${target.id}`, target);
    }
  }

  if (targets.size === 1) {
    return { target: targets.values().next().value ?? null, ambiguous: false };
  }
  return { target: null, ambiguous: targets.size > 1 };
}

/** Cancel exactly one already-resolved runtime target through its owning API. */
export async function cancelHudTarget(target: HudCancelTarget): Promise<void> {
  const id = encodeURIComponent(target.id);
  const url =
    target.kind === "mission"
      ? `/api/missions/${id}/cancel`
      : `/api/tasks/${id}/cancel`;

  const response = await fetch(url, { method: "POST" });
  if (!response.ok) throw new Error(`cancel-${target.kind}-failed:${response.status}`);
}
