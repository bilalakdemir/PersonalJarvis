/**
 * The main HUD workspace (N-17).
 *
 * A projection of the canonical backend HUD snapshot around one visual
 * anchor, the reactor. Panels are contextual: each appears only while the
 * snapshot has something for it, so a quiet Jarvis shows a quiet screen.
 *
 * Data flow — no second backend, no second socket, no polling:
 *   - `useHudStore` holds the snapshot; it is filled by the REST resync on
 *     every WS welcome and by `hud.snapshot` frames on the existing `/ws`.
 *   - The conversation panel reads the EXISTING chat store; it is a window
 *     onto the conversation, not a second chat.
 *   - The only write is an approval decision, sent through the owning
 *     domain's own route for exactly one card (`lib/hudApi.ts`).
 *
 * A failure inside this view is contained by the shell's ViewErrorBoundary;
 * nothing here can stop the Jarvis core.
 */
import { useEffect, useMemo, useState } from "react";
import { MessageSquare } from "lucide-react";

import { Reactor } from "@/components/hud/Reactor";
import {
  ActivityPanel,
  ApprovalsPanel,
  ComputerPanel,
  ErrorPanel,
  HudPanel,
  MemoryPanel,
  ProjectPanel,
  tr,
} from "@/components/hud/HudPanels";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import {
  REACTOR_STYLES,
  effectiveConnection,
  liveApprovals,
  visiblePanels,
} from "@/lib/hudSemantics";
import { useEventStore } from "@/store/events";
import { refreshHudSnapshot, useHudStore } from "@/store/hud";

/** How often the view re-reads the CLIENT clock to age approval windows.
 *  Local only — this never touches the backend. */
const CLOCK_TICK_MS = 1000;

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function HudView() {
  const t = useT();
  const snapshot = useHudStore((s) => s.snapshot);
  const hadSnapshot = useHudStore((s) => s.hadSnapshot);
  const connected = useEventStore((s) => s.connected);
  const warming = useEventStore((s) => s.wsWarming);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const nowMs = useNow();

  // Opening the view (or remounting after a reload) resyncs once.
  useEffect(() => {
    void refreshHudSnapshot();
  }, []);

  const connection = effectiveConnection(connected, warming, hadSnapshot);
  const conversation = useMemo(
    () => messages.filter((m) => m.role === "user" || m.role === "assistant").slice(-4),
    [messages],
  );

  if (snapshot === null) {
    return (
      <div className="flex h-full items-center justify-center p-8" data-testid="hud-view">
        <p className="text-muted-foreground" role="status">
          {connection === "CONNECTED" ? t("hud.loading") : t(`hud.connection.${connection.toLowerCase()}`)}
        </p>
      </div>
    );
  }

  const panels = visiblePanels(snapshot, nowMs);
  const approvals = liveApprovals(snapshot, nowMs);
  const style = REACTOR_STYLES[snapshot.primary_state];
  const stateLabel = t(style.labelKey);
  const attention = snapshot.attention.map((flag) =>
    tr(t, `hud.attention.${flag}`, flag),
  );
  const quiet = !Object.values(panels).some(Boolean);

  return (
    <div className="hud-workspace h-full overflow-y-auto p-4 sm:p-6" data-testid="hud-view">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-foreground">{t("hud.title")}</h1>
        <p
          className="text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
          data-testid="hud-status-line"
        >
          {stateLabel}
          {" · "}
          {t(`hud.connection.${connection.toLowerCase()}`)}
          {snapshot.voice_state && snapshot.voice_state !== "IDLE"
            ? ` · ${snapshot.voice_state.toLowerCase()}`
            : ""}
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          {panels.project && snapshot.active_project ? (
            <ProjectPanel project={snapshot.active_project} t={t} />
          ) : null}
          {panels.activity ? (
            <ActivityPanel
              operations={snapshot.active_operations}
              agents={snapshot.agent_activity}
              t={t}
            />
          ) : null}
        </div>

        <div className="flex flex-col items-center gap-4">
          <Reactor state={snapshot.primary_state} label={stateLabel} attention={attention} />
          {quiet ? (
            <p className="text-sm text-muted-foreground" data-testid="hud-quiet">
              {t("hud.quiet")}
            </p>
          ) : null}
          <HudPanel
            title={t("hud.panel.conversation")}
            icon={<MessageSquare />}
            testId="hud-panel-conversation"
          >
            {conversation.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("hud.conversation.empty")}</p>
            ) : (
              <ol className="space-y-2 text-sm">
                {conversation.map((message) => (
                  <li key={message.id} className="flex gap-2">
                    <span className="shrink-0 text-xs uppercase text-muted-foreground">
                      {t(`hud.conversation.${message.role}`)}
                    </span>
                    <span className="line-clamp-2 text-foreground">{message.content}</span>
                  </li>
                ))}
              </ol>
            )}
            <Button
              className="mt-3"
              size="sm"
              variant="ghost"
              onClick={() => setActiveSection("chats")}
            >
              {t("hud.conversation.open")}
            </Button>
          </HudPanel>
        </div>

        <div className="space-y-4">
          {panels.approvals ? <ApprovalsPanel cards={approvals} t={t} nowMs={nowMs} /> : null}
          {panels.error && snapshot.last_error ? (
            <ErrorPanel error={snapshot.last_error} t={t} />
          ) : null}
          {panels.computer ? <ComputerPanel computer={snapshot.computer_activity} t={t} /> : null}
          {panels.memory ? <MemoryPanel items={snapshot.memory_activity} t={t} /> : null}
        </div>
      </div>
    </div>
  );
}

export default HudView;
