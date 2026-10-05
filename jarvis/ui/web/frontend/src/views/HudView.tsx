/**
 * AERION immersive command center.
 *
 * A projection of existing stores only: the canonical HudSnapshot
 * (`useHudStore`) and the event store (`useEventStore`). It owns no second
 * backend, chat, task, agent or approval state; local state here is purely
 * presentational (panel filters).
 */
import { useEffect, useMemo, useState } from "react";

import "@/aerion-hud.css";

import {
  AerionHeader,
  CapabilityRail,
  CommandDock,
  ModeReadout,
  Pedestal,
  StageFrame,
  SystemStatus,
  capabilityIcons,
  type StatusLine,
} from "@/components/hud/AerionChrome";
import { useAerionCopy } from "@/components/hud/aerionCopy";
import { CoreRings } from "@/components/hud/CoreRings";
import {
  ActivityPanel,
  ApprovalsPanel,
  InboxPanel,
  RecentOutputsPanel,
  SystemPanel,
  TodayPanel,
  UpcomingPanel,
  tr,
} from "@/components/hud/HudPanels";
import { Reactor } from "@/components/hud/Reactor";
import { useUiLanguage } from "@/i18n";
import { clockFormatter, formatHeaderDate, nsToMs } from "@/lib/aerionPresentation";
import {
  REACTOR_STYLES,
  aerionVisualState,
  effectiveConnection,
  liveApprovals,
  visiblePanels,
  type AerionVisualState,
} from "@/lib/hudSemantics";
import { useEventStore } from "@/store/events";
import { refreshHudSnapshot, useHudStore } from "@/store/hud";

const CLOCK_TICK_MS = 1000;
const LEARN_WINDOW_MS = 30_000;
const NO_ATTENTION: readonly string[] = [];

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1).toLowerCase() : value;
}

export function HudView() {
  const { say, t } = useAerionCopy();
  const locale = useUiLanguage();
  const snapshot = useHudStore((s) => s.snapshot);
  const hadSnapshot = useHudStore((s) => s.hadSnapshot);
  const connected = useEventStore((s) => s.connected);
  const warming = useEventStore((s) => s.wsWarming);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const nowMs = useNow();

  useEffect(() => {
    void refreshHudSnapshot();
  }, []);

  const connection = effectiveConnection(connected, warming, hadSnapshot);
  const connectionLabel = t(`hud.connection.${connection.toLowerCase()}`);
  const clock = useMemo(() => clockFormatter(locale), [locale]);
  const dateLabel = formatHeaderDate(nowMs, locale);
  const timeLabel = clock.format(nowMs);

  // Stable identity for the memoised Reactor (re-renders every clock tick otherwise).
  const attentionKey = snapshot
    ? snapshot.attention.map((flag) => tr(t, `hud.attention.${flag}`, flag)).join("\u0000")
    : "";
  const attention = useMemo(
    () => (attentionKey ? attentionKey.split("\u0000") : NO_ATTENTION),
    [attentionKey],
  );

  if (snapshot === null) {
    return (
      <div
        className="aerion-command-core aerion-loading"
        data-testid="hud-view"
        data-connection-state={connection}
        data-aerion-state="OFF"
      >
        <div className="aerion-loading-core" aria-hidden>
          <CoreRings pattern="dotted" />
        </div>
        <p role="status" className="aerion-loading-status">
          {connection === "CONNECTED" ? t("hud.loading") : connectionLabel}
        </p>
      </div>
    );
  }

  const panels = visiblePanels(snapshot, nowMs);
  const approvals = liveApprovals(snapshot, nowMs);
  const style = REACTOR_STYLES[snapshot.primary_state];
  const stateLabel = t(style.labelKey);
  const coreState: AerionVisualState = aerionVisualState(snapshot, connection, nowMs);
  const coreStateLabel = say(`state.${coreState}`);
  const quiet = !Object.values(panels).some(Boolean);
  const runningCount = [...snapshot.active_operations, ...snapshot.agent_activity].filter(
    (item) => item.status === "running",
  ).length;
  const agentCount = snapshot.agent_activity.filter((item) => item.status === "running").length;
  const voiceState = snapshot.voice_state ? capitalize(snapshot.voice_state.replaceAll("_", " ")) : "—";

  const modeDetail =
    coreState === "ERROR" && snapshot.last_error?.message
      ? snapshot.last_error.message
      : say(`mode.${coreState}`);

  const headline: StatusLine =
    connection !== "CONNECTED"
      ? { label: say("status.degraded"), tone: connection === "DISCONNECTED" ? "red" : "gold" }
      : snapshot.primary_state === "ERROR"
        ? { label: say("status.attention"), tone: "red" }
        : approvals.length > 0 || snapshot.attention.length > 0
          ? { label: say("status.attention"), tone: "gold" }
          : { label: say("status.nominal"), tone: "green" };
  const statusLines: StatusLine[] = [
    {
      label: say("status.stream"),
      value: connectionLabel,
      tone: connection === "CONNECTED" ? "green" : connection === "RECONNECTING" ? "gold" : "red",
    },
    {
      label: say("status.voice"),
      value: voiceState,
      tone: snapshot.voice_state === "error" ? "red" : "cyan",
    },
    {
      label: say("status.attention_flags"),
      value: String(snapshot.attention.length),
      tone: snapshot.attention.length > 0 ? "gold" : "green",
    },
  ];

  const newestMemoryMs = snapshot.memory_activity.reduce(
    (latest, item) => Math.max(latest, nsToMs(item.at_ns) ?? 0),
    0,
  );
  const icons = capabilityIcons();
  const leftCaps = [
    { key: "listen", label: say("capability.listen"), icon: icons.listen, active: coreState === "LISTENING" },
    { key: "think", label: say("capability.think"), icon: icons.think, active: coreState === "THINKING" },
    {
      key: "process",
      label: say("capability.process"),
      icon: icons.process,
      active: coreState === "WORKING",
    },
  ];
  const rightCaps = [
    {
      key: "learn",
      label: say("capability.learn"),
      icon: icons.learn,
      active: newestMemoryMs > 0 && nowMs - newestMemoryMs <= LEARN_WINDOW_MS,
    },
    {
      key: "plan",
      label: say("capability.plan"),
      icon: icons.plan,
      active: coreState === "WAITING_FOR_APPROVAL" || Boolean(snapshot.active_project?.current_task),
    },
    {
      key: "execute",
      label: say("capability.execute"),
      icon: icons.execute,
      active: coreState === "SPEAKING" || snapshot.computer_activity.active,
    },
  ];

  return (
    <div
      className="aerion-command-core"
      data-testid="hud-view"
      data-primary-state={snapshot.primary_state}
      data-connection-state={connection}
      data-aerion-state={coreState}
    >
      {quiet ? (
        <span className="sr-only" data-testid="hud-quiet">
          {t("hud.quiet")}
        </span>
      ) : null}

      <AerionHeader
        say={say}
        connection={connection}
        connectionLabel={connectionLabel}
        coreStateLabel={coreStateLabel}
        voiceState={voiceState}
        agentCount={agentCount}
        taskCount={runningCount}
        dateLabel={dateLabel}
        timeLabel={timeLabel}
        onNavigate={setActiveSection}
      />

      <div className="aerion-dashboard-grid" data-testid="aerion-command-grid">
        <aside className="aerion-side-column aerion-left-column" data-testid="aerion-zone-left">
          <ApprovalsPanel cards={approvals} t={t} nowMs={nowMs} onViewAll={() => setActiveSection("tasks")} />
          <TodayPanel
            snapshot={snapshot}
            nowMs={nowMs}
            dateLabel={dateLabel}
            t={t}
            onOpenProjects={() => setActiveSection("chat-workspace")}
          />
          <InboxPanel />
        </aside>

        <div className="aerion-center-stage" data-testid="aerion-zone-center">
          <section className="aerion-core-stage" aria-label={say("brand.core")}>
            <StageFrame />
            <ModeReadout say={say} coreState={coreState} stateLabel={coreStateLabel} detail={modeDetail} />
            <SystemStatus say={say} headline={headline} lines={statusLines} />
            <CapabilityRail side="left" items={leftCaps} />
            <CapabilityRail side="right" items={rightCaps} />
            <Pedestal />
            <Reactor
              key="aerion-reactor"
              state={snapshot.primary_state}
              label={stateLabel}
              attention={attention}
              visualState={coreState}
            />
            <div className="aerion-core-caption" aria-hidden>
              <strong>{say("brand.core")}</strong>
              <span>{say("brand.caption")}</span>
            </div>
          </section>

          <CommandDock say={say} onNavigate={setActiveSection} />
        </div>

        <aside className="aerion-side-column aerion-right-column" data-testid="aerion-zone-right">
          <ActivityPanel
            operations={snapshot.active_operations}
            agents={snapshot.agent_activity}
            t={t}
            nowMs={nowMs}
            onViewTasks={() => setActiveSection("tasks")}
            onManageAgents={() => setActiveSection("agents")}
          />
          <RecentOutputsPanel outputs={snapshot.recent_outputs} t={t} nowMs={nowMs} />
          <UpcomingPanel />
          <SystemPanel
            computer={snapshot.computer_activity}
            error={snapshot.last_error}
            t={t}
            onOpenSystem={() => setActiveSection("computers")}
          />
        </aside>
      </div>
    </div>
  );
}

export default HudView;
