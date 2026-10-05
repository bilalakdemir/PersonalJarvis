import { useEffect, useRef } from "react";

import { useAerionCopy } from "@/components/hud/aerionCopy";
import {
  AerionProactiveSeen,
  deriveAerionProactiveNotices,
  type AerionProactiveNotice,
} from "@/lib/aerionProactive";
import { useEventStore } from "@/store/events";
import { useHudStore } from "@/store/hud";
import type { HudSnapshot } from "@/types/hud";

function noticeMessage(
  notice: AerionProactiveNotice,
  say: ReturnType<typeof useAerionCopy>["say"],
): string {
  const base =
    notice.category === "approval"
      ? say("proactive.approval", { subject: notice.subject })
      : notice.category === "completed"
        ? say("proactive.completed", { subject: notice.subject })
        : say("proactive.error", { subject: notice.subject });

  if (!notice.detail || notice.detail === notice.subject) return base;
  return `${base} — ${notice.detail}`;
}

/**
 * App-wide projection from canonical HudSnapshot transitions into the existing
 * toast surface. It owns no runtime state and never synthesizes external data.
 */
export function useAerionProactive(enabled = true): void {
  const snapshot = useHudStore((state) => state.snapshot);
  const pushToast = useEventStore((state) => state.pushToast);
  const { say } = useAerionCopy();
  const previousRef = useRef<HudSnapshot | null>(null);
  const seenRef = useRef(new AerionProactiveSeen());

  useEffect(() => {
    if (snapshot === null) {
      previousRef.current = null;
      seenRef.current.clear();
      return;
    }

    const previous = previousRef.current;

    // Detached/solo windows still keep a local baseline, but only the primary
    // app window emits proactive notices. Re-enabling therefore cannot replay
    // everything that happened while this window was passive.
    if (!enabled) {
      previousRef.current = snapshot;
      return;
    }

    if (previous !== null && previous.epoch !== snapshot.epoch) {
      seenRef.current.clear();
    }

    const notices = deriveAerionProactiveNotices(previous, snapshot, Date.now());
    previousRef.current = snapshot;

    for (const notice of notices) {
      if (!seenRef.current.remember(notice.key)) continue;
      pushToast(notice.kind, noticeMessage(notice, say));
    }
  }, [enabled, pushToast, say, snapshot]);
}
