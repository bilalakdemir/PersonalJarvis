/**
 * AERION immersive command center.
 *
 * This view is only a projection of existing stores/snapshots. It owns no
 * second backend, chat, task, agent or approval state.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  Bot,
  Brain,
  CalendarDays,
  CheckCircle2,
  FolderKanban,
  Mail,
  MessageCircle,
  Paperclip,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";

import { Reactor } from "@/components/hud/Reactor";
import {
  ActivityPanel,
  ApprovalsPanel,
  ComputerPanel,
  ErrorPanel,
  MemoryPanel,
  ProjectPanel,
  tr,
} from "@/components/hud/HudPanels";
import { useT } from "@/i18n";
import {
  REACTOR_STYLES,
  aerionVisualState,
  effectiveConnection,
  liveApprovals,
  visiblePanels,
} from "@/lib/hudSemantics";
import { useEventStore, type SectionId } from "@/store/events";
import { refreshHudSnapshot, useHudStore } from "@/store/hud";

const CLOCK_TICK_MS = 1000;

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

function NavButton({
  icon,
  label,
  section,
  active,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  section: SectionId;
  active?: boolean;
  onSelect: (section: SectionId) => void;
}) {
  return (
    <button
      type="button"
      className={`aerion-topnav-button${active ? " is-active" : ""}`}
      onClick={() => onSelect(section)}
    >
      <span aria-hidden>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

function EmptyIntelPanel({
  icon,
  title,
  message,
}: {
  icon: ReactNode;
  title: string;
  message: string;
}) {
  return (
    <section className="aerion-intel-panel aerion-empty-panel">
      <header>
        <span aria-hidden>{icon}</span>
        <strong>{title}</strong>
      </header>
      <p>{message}</p>
    </section>
  );
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

  useEffect(() => {
    void refreshHudSnapshot();
  }, []);

  const connection = effectiveConnection(connected, warming, hadSnapshot);
  const conversation = useMemo(
    () => messages.filter((m) => m.role === "user" || m.role === "assistant").slice(-3),
    [messages],
  );

  if (snapshot === null) {
    return (
      <div className="aerion-command-core aerion-loading" data-testid="hud-view">
        <p role="status">
          {connection === "CONNECTED"
            ? t("hud.loading")
            : t(`hud.connection.${connection.toLowerCase()}`)}
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
  const coreState = aerionVisualState(snapshot, connection, nowMs);
  const coreStateLabel = coreState.replaceAll("_", " ");
  const activeOps = [...snapshot.agent_activity, ...snapshot.active_operations];
  const runningCount = activeOps.filter((item) => item.status === "running").length;
  const agentCount = snapshot.agent_activity.length;

  return (
    <div
      className="aerion-command-core"
      data-testid="hud-view"
      data-primary-state={snapshot.primary_state}
      data-connection-state={connection}
      data-aerion-state={coreState}
    >
      <header className="aerion-global-header">
        <div className="aerion-brand-lockup">
          <div className="aerion-brand-mark" aria-hidden>A</div>
          <div>
            <div className="aerion-wordmark" data-testid="aerion-wordmark">AERION</div>
            <div className="aerion-brand-subtitle">PERSONAL INTELLIGENCE SYSTEM</div>
          </div>
        </div>

        <nav className="aerion-topnav" aria-label="AERION">
          <NavButton icon={<MessageCircle />} label="Chat" section="chats" onSelect={setActiveSection} />
          <NavButton icon={<FolderKanban />} label="Projects" section="chat-workspace" onSelect={setActiveSection} />
          <NavButton icon={<Bot />} label="Agents" section="agents" onSelect={setActiveSection} />
          <NavButton icon={<Brain />} label="Memory" section="memory" onSelect={setActiveSection} />
          <NavButton icon={<Wrench />} label="Tools" section="plugins" onSelect={setActiveSection} />
          <NavButton icon={<Settings />} label="System" section="settings" onSelect={setActiveSection} />
        </nav>

        <div className="aerion-header-status" data-testid="hud-status-line">
          <span className="aerion-status-dot" aria-hidden />
          <span>{coreStateLabel}</span>
          <span className="aerion-status-divider">·</span>
          <span>{t(`hud.connection.${connection.toLowerCase()}`)}</span>
        </div>
      </header>

      <div className="aerion-dashboard-grid" data-testid="aerion-command-grid">
        <aside className="aerion-side-column aerion-left-column" data-testid="aerion-zone-left">
          {panels.approvals ? (
            <ApprovalsPanel cards={approvals} t={t} nowMs={nowMs} />
          ) : (
            <EmptyIntelPanel
              icon={<ShieldCheck />}
              title="YOUR ACTIONS"
              message="Nothing needs your approval right now."
            />
          )}

          {panels.project && snapshot.active_project ? (
            <ProjectPanel project={snapshot.active_project} t={t} />
          ) : (
            <EmptyIntelPanel
              icon={<CalendarDays />}
              title="TODAY"
              message="No active project task is pinned to the command center."
            />
          )}

          <EmptyIntelPanel
            icon={<Mail />}
            title="INBOX INTELLIGENCE"
            message="Inbox intelligence will appear here when a connected mail source publishes actionable items."
          />
        </aside>

        <main className="aerion-center-stage" data-testid="aerion-zone-center">
          <section className="aerion-core-stage">
            <div className="aerion-mode-readout">
              <span>CURRENT MODE</span>
              <strong>{coreStateLabel}</strong>
              <small>{stateLabel} · {t(`hud.connection.${connection.toLowerCase()}`)}</small>
            </div>

            <div className="aerion-system-summary">
              <span><i /> Core runtime</span>
              <span><i /> Event stream</span>
              <span><i /> Memory layer</span>
            </div>

            <Reactor
              state={snapshot.primary_state}
              label={stateLabel}
              attention={attention}
              visualState={coreState}
            />

            <div className="aerion-core-caption">
              <strong>AERION CORE</strong>
              <span>SYNTHESIZE · REASON · PLAN · EXECUTE</span>
            </div>
          </section>

          <section className="aerion-command-dock" aria-label="AERION command">
            <button
              type="button"
              className="aerion-command-input"
              onClick={() => setActiveSection("chats")}
            >
              <Sparkles aria-hidden />
              <span>
                {conversation.length > 0
                  ? conversation[conversation.length - 1]?.content
                  : "Message AERION..."}
              </span>
              <Paperclip aria-hidden className="aerion-command-trailing" />
              <Send aria-hidden />
            </button>
            <div className="aerion-quick-actions">
              {["Deep Research", "Analyze File", "Create Plan", "Work with Agents", "Search Web"].map((label) => (
                <button key={label} type="button" onClick={() => setActiveSection("chats")}>
                  {label}
                </button>
              ))}
            </div>
          </section>
        </main>

        <aside className="aerion-side-column aerion-right-column" data-testid="aerion-zone-right">
          {panels.activity ? (
            <ActivityPanel
              operations={snapshot.active_operations}
              agents={snapshot.agent_activity}
              t={t}
            />
          ) : (
            <EmptyIntelPanel
              icon={<Activity />}
              title={`RUNNING TASKS · ${runningCount}`}
              message="No task is currently running."
            />
          )}

          <EmptyIntelPanel
            icon={<Bot />}
            title={`AGENTS · ${agentCount}`}
            message={
              agentCount > 0
                ? "Agent activity is available in the live activity panel."
                : "No active agent activity is being reported."
            }
          />

          {panels.memory ? (
            <MemoryPanel items={snapshot.memory_activity} t={t} />
          ) : (
            <EmptyIntelPanel
              icon={<CheckCircle2 />}
              title="RECENT OUTPUTS"
              message="Completed outputs will appear here when the runtime publishes them."
            />
          )}

          {panels.error && snapshot.last_error ? (
            <ErrorPanel error={snapshot.last_error} t={t} />
          ) : panels.computer ? (
            <ComputerPanel computer={snapshot.computer_activity} t={t} />
          ) : (
            <EmptyIntelPanel
              icon={<CalendarDays />}
              title="UPCOMING"
              message="Upcoming operational items will appear here from connected sources."
            />
          )}
        </aside>
      </div>
    </div>
  );
}

export default HudView;
