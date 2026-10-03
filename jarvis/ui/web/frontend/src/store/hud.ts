/**
 * Client mirror of the canonical HUD snapshot (N-17).
 *
 * Exactly one writer per source: `refreshHudSnapshot()` (the REST resync on
 * every WS welcome, i.e. startup / reconnect / reload / resume) and the
 * `hud.snapshot` frames pushed on the existing socket. Both go through
 * `applySnapshot`, which keeps the newest by `(epoch, revision)` — so a slow
 * resync can never overwrite a newer push, and a backend restart is never
 * mistaken for a stale frame.
 *
 * This store is display state only. It cannot change anything in Jarvis.
 */
import { create } from "zustand";

import { parseHudSnapshot, shouldAcceptSnapshot } from "@/lib/hudSemantics";
import type { HudSnapshot } from "@/types/hud";

interface HudStoreState {
  snapshot: HudSnapshot | null;
  /** Set once the first snapshot has been seen in this page's lifetime. */
  hadSnapshot: boolean;
  applySnapshot: (raw: unknown) => boolean;
  reset: () => void;
}

export const useHudStore = create<HudStoreState>((set, get) => ({
  snapshot: null,
  hadSnapshot: false,
  applySnapshot: (raw) => {
    const parsed = parseHudSnapshot(raw);
    if (parsed === null) return false;
    if (!shouldAcceptSnapshot(get().snapshot, parsed)) return false;
    set({ snapshot: parsed, hadSnapshot: true });
    return true;
  },
  reset: () => set({ snapshot: null, hadSnapshot: false }),
}));

const SNAPSHOT_ENDPOINT = "/api/hud/snapshot";

let inFlight: Promise<boolean> | null = null;

/**
 * Read the full snapshot once. Coalesced: concurrent callers (several
 * welcome frames in quick succession) share one request. No polling — live
 * changes arrive as pushed frames.
 */
export function refreshHudSnapshot(): Promise<boolean> {
  if (inFlight) return inFlight;
  inFlight = fetch(SNAPSHOT_ENDPOINT)
    .then((response) => (response.ok ? response.json() : null))
    .then((body) => (body === null ? false : useHudStore.getState().applySnapshot(body)))
    .catch(() => false)
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}
