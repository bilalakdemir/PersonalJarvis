/**
 * AERION intelligence panels (N-17 data, AERION presentation).
 *
 * Each panel renders recorded operational state from the canonical
 * HudSnapshot / event store and nothing else: no hidden reasoning, no raw
 * memory, no screen content, no prompts. Every snapshot string arrived
 * secret-masked and capped from the backend reducer.
 *
 * Panel frames are always present (the composition never collapses); the
 * canonical `hud-panel-*` test ids are only attached while that panel
 * actually has snapshot content, exactly as before.
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Bot,
  Brain,
  CalendarDays,
  ChevronsRight,
  Database,
  FolderKanban,
  Info,
  ListChecks,
  MessagesSquare,
  Monitor,
  MonitorSmartphone,
  Plus,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Target,
  Terminal,
  User,
  Workflow,
  Wrench,
} from "lucide-react";

import { useAerionCopy, type AerionSay } from "@/components/hud/aerionCopy";
import { useUiLanguage } from "@/i18n";
import { decideHudApproval, type HudDecision } from "@/lib/hudApi";
import {
  ageOf,
  approvalMatches,
  clockFormatter,
  formatElapsed,
  messageTsToMs,
  nsToMs,
  orderActivities,
  riskTone,
  statusTone,
  todayTimeline,
  type ApprovalFilter,
  type Tone,
} from "@/lib/aerionPresentation";
import { canDecide, secondsLeft, shortId } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type {
  HudActivity,
  HudApproval,
  HudComputerActivity,
  HudError,
  HudMemoryActivity,
  HudProject,
  HudSnapshot,
} from "@/types/hud";

type T = (key: string) => string;

/** The slice of the event store's ChatMessage this panel reads. */
interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  ts: number;
}

/** A translation, or `fallback` when the key has no string yet (the i18n
 *  resolver returns the key itself on a miss). */
export function tr(t: T, key: string, fallback: string): string {
  const value = t(key);
  return value === key ? fallback : value;
}

// --------------------------------------------------------------- primitives

export function IconTile({
  tone = "cyan",
  size = "md",
  children,
}: {
  tone?: Tone;
  size?: "sm" | "md";
  children: ReactNode;
}) {
  return (
    <span className="aerion-tile" data-tone={tone} data-size={size} aria-hidden>
      {children}
    </span>
  );
}

function Dot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <i className={cn("aerion-dot", pulse && "is-pulsing")} data-tone={tone} aria-hidden />;
}

function PanelLink({ label, onClick }: { label: string; onClick?: () => void }) {
  if (!onClick) return null;
  return (
    <button type="button" className="aerion-panel-link" onClick={onClick}>
      <span>{label}</span>
      <ArrowRight aria-hidden />
    </button>
  );
}

export function AerionPanel({
  title,
  icon,
  count,
  countTone = "blue",
  link,
  extra,
  testId,
  slot,
  tone,
  children,
}: {
  title: string;
  icon: ReactNode;
  count?: number;
  countTone?: "red" | "blue" | "gold";
  link?: { label: string; onClick: () => void };
  extra?: ReactNode;
  testId: string;
  slot: string;
  tone?: "warning" | "destructive";
  children: ReactNode;
}) {
  return (
    <section
      className="aerion-panel"
      data-slot={slot}
      data-tone={tone}
      data-testid={testId}
      aria-label={title}
    >
      <header className="aerion-panel-head">
        <span className="aerion-panel-icon" aria-hidden>
          {icon}
        </span>
        <h2 className="aerion-panel-title">{title}</h2>
        {typeof count === "number" ? (
          <span className="aerion-count" data-tone={countTone} data-zero={count === 0 ? "true" : undefined}>
            {count}
          </span>
        ) : null}
        <span className="aerion-panel-head-fill" />
        {extra}
        {link ? <PanelLink label={link.label} onClick={link.onClick} /> : null}
      </header>
      <div className="aerion-panel-body">{children}</div>
    </section>
  );
}

/** Backwards-compatible generic panel (same props as the N-17 HudPanel). */
export function HudPanel({
  title,
  icon,
  children,
  testId,
  tone,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  testId: string;
  tone?: "warning" | "destructive";
}) {
  return (
    <AerionPanel title={title} icon={icon} testId={testId} slot="generic" tone={tone}>
      {children}
    </AerionPanel>
  );
}

function PanelEmpty({
  icon,
  title,
  message,
  action,
}: {
  icon: ReactNode;
  title?: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="aerion-empty">
      <span className="aerion-empty-icon" aria-hidden>
        {icon}
      </span>
      {title ? <strong>{title}</strong> : null}
      <p>{message}</p>
      {action}
    </div>
  );
}

function useClock(): (ms: number | null) => string {
  const locale = useUiLanguage();
  const fmt = useMemo(() => clockFormatter(locale), [locale]);
  return (ms) => (ms === null ? "--:--" : fmt.format(ms));
}

function ageLabel(say: AerionSay, fromMs: number | null, nowMs: number): string {
  const age = ageOf(fromMs, nowMs);
  if (!age) return "";
  if (age.unit === "now") return say("time.now");
  return say(`time.${age.unit}`, { n: age.n });
}

// ------------------------------------------------------------------ approvals

function approvalIcon(card: HudApproval): ReactNode {
  if (card.kind === "project_state") return <FolderKanban />;
  if (card.kind === "memory_promotion") return <Brain />;
  return <Wrench />;
}

function approvalIdentity(card: HudApproval): string {
  if (card.kind === "project_state") {
    return [
      card.queue_item_id ? `#${card.queue_item_id}` : null,
      shortId(card.transaction_id),
      shortId(card.proposal_digest),
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (card.kind === "memory_promotion") {
    return [card.candidate_id ? `#${card.candidate_id}` : null, shortId(card.proposal_digest)]
      .filter(Boolean)
      .join(" · ");
  }
  return `${shortId(card.mission_id)} · ${shortId(card.trace_id)}`;
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="aerion-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function ApprovalCard({ card, t, nowMs }: { card: HudApproval; t: T; nowMs: number }) {
  const { say } = useAerionCopy();
  const clock = useClock();
  const [pending, setPending] = useState<HudDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const decidable = canDecide(card);
  const left = secondsLeft(card, nowMs);
  const tone = riskTone(card.risk_tier);

  async function decide(decision: HudDecision) {
    setPending(decision);
    setError(null);
    try {
      await decideHudApproval(card, decision);
      // The card disappears when the domain publishes its decision and the
      // next snapshot arrives — the HUD never removes it optimistically.
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPending(null);
    }
  }

  return (
    <article
      className="aerion-row aerion-approval"
      data-testid="hud-approval-card"
      data-approval-id={card.approval_id}
      data-tone={tone}
    >
      <IconTile tone={tone}>{approvalIcon(card)}</IconTile>
      <div className="aerion-row-main">
        <p className="aerion-row-title">{card.action}</p>
        {card.target_preview || card.reason ? (
          <p className="aerion-row-sub">{card.target_preview || card.reason}</p>
        ) : null}
        <div className="aerion-approval-bar">
          {decidable ? (
            <>
              <button
                type="button"
                className="aerion-decide"
                data-variant="approve"
                onClick={() => void decide("approve")}
                disabled={pending !== null}
                aria-label={`${t("hud.approval.approve")}: ${card.action}`}
                data-testid="hud-approve"
              >
                {t("hud.approval.approve")}
              </button>
              <button
                type="button"
                className="aerion-decide"
                data-variant="deny"
                onClick={() => void decide("deny")}
                disabled={pending !== null}
                aria-label={`${t("hud.approval.deny")}: ${card.action}`}
                data-testid="hud-deny"
              >
                {t("hud.approval.deny")}
              </button>
            </>
          ) : (
            <p className="aerion-approval-readonly" data-testid="hud-approval-readonly">
              {t(`hud.approval.read_only.${card.read_only_reason || "no_out_of_band_route"}`)}
            </p>
          )}
          {card.risk_tier ? (
            <span className="aerion-chip" data-tone={tone}>
              {card.risk_tier}
            </span>
          ) : null}
          {left !== null ? (
            <span
              className="aerion-chip"
              data-kind="timer"
              data-tone={left < 30 ? "red" : "muted"}
              title={t("hud.approval.expires")}
            >
              {left}s
            </span>
          ) : null}
          <details className="aerion-approval-details">
            <summary aria-label={say("approval.details")} title={say("approval.details")}>
              <Info aria-hidden />
            </summary>
            <dl>
              <Field label={t("hud.approval.target")} value={card.target_preview} />
              <Field label={t("hud.approval.project")} value={card.project_id} />
              <Field label={t("hud.approval.reason")} value={card.reason} />
              <Field label={t("hud.approval.risk")} value={card.risk_tier} />
              <Field label={t("hud.approval.expires")} value={left === null ? null : `${left}s`} />
              <Field
                label={t("hud.approval.identity")}
                value={<code title={card.approval_id}>{approvalIdentity(card)}</code>}
              />
            </dl>
          </details>
        </div>
        {error ? (
          <p className="aerion-row-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <div className="aerion-row-meta">
        <time>{clock(nsToMs(card.requested_at_ns))}</time>
        <Dot tone={tone} pulse />
      </div>
    </article>
  );
}

export function ApprovalsPanel({
  cards,
  t,
  nowMs,
  onViewAll,
}: {
  cards: HudApproval[];
  t: T;
  nowMs: number;
  onViewAll?: () => void;
}) {
  const { say } = useAerionCopy();
  const [filter, setFilter] = useState<ApprovalFilter>("all");
  const counts = {
    all: cards.length,
    tools: cards.filter((c) => approvalMatches(c, "tools")).length,
    proposals: cards.filter((c) => approvalMatches(c, "proposals")).length,
  };
  const shown = cards.filter((c) => approvalMatches(c, filter));
  const filters: Array<[ApprovalFilter, string]> = [
    ["all", say("filter.all")],
    ["tools", say("filter.tools")],
    ["proposals", say("filter.proposals")],
  ];

  return (
    <AerionPanel
      slot="actions"
      title={say("panel.actions")}
      icon={<ShieldCheck />}
      count={cards.length}
      countTone="red"
      link={onViewAll ? { label: say("link.view_all"), onClick: onViewAll } : undefined}
      testId={cards.length > 0 ? "hud-panel-approvals" : "aerion-panel-actions"}
      tone={cards.length > 0 ? "warning" : undefined}
    >
      <div className="aerion-filters" role="group" aria-label={say("panel.actions")}>
        {filters.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            className={cn("aerion-filter", filter === key && "is-active")}
            onClick={() => setFilter(key)}
          >
            <span>{label}</span>
            <b>{counts[key]}</b>
          </button>
        ))}
        <span className="aerion-filters-fill" />
        {onViewAll ? (
          <button
            type="button"
            className="aerion-filter-more"
            onClick={onViewAll}
            aria-label={say("link.view_all")}
          >
            <ChevronsRight aria-hidden />
          </button>
        ) : null}
      </div>
      {shown.length > 0 ? (
        <div className="aerion-rows aerion-scroll">
          {shown.map((card) => (
            <ApprovalCard key={card.approval_id} card={card} t={t} nowMs={nowMs} />
          ))}
        </div>
      ) : (
        <PanelEmpty
          icon={<ShieldCheck />}
          title={say("empty.actions_title")}
          message={say("empty.actions")}
        />
      )}
    </AerionPanel>
  );
}

// -------------------------------------------------------------------- project

export function ProjectPanel({ project, t }: { project: HudProject; t: T }) {
  return (
    <div className="aerion-project" data-testid="hud-panel-project" aria-label={t("hud.panel.project")}>
      <IconTile tone={project.state_valid ? "cyan" : "gold"} size="sm">
        <FolderKanban />
      </IconTile>
      <div className="aerion-project-main">
        <p className="aerion-project-name">{project.project_name || project.project_id}</p>
        <p className="aerion-project-task">
          <span>{t("hud.project.current")}</span>
          {project.current_task ?? t("hud.project.no_current")}
        </p>
        {!project.state_valid ? (
          <p className="aerion-project-warn">
            {t("hud.project.invalid")} {project.issue_codes.join(", ")}
          </p>
        ) : null}
      </div>
      <code className="aerion-project-rev" title={`${t("hud.project.revision")} ${project.state_revision}`}>
        {shortId(project.state_revision, 7)}
      </code>
    </div>
  );
}

// ---------------------------------------------------------------------- today

export function TodayPanel({
  snapshot,
  nowMs,
  dateLabel,
  t,
  onOpenProjects,
}: {
  snapshot: HudSnapshot;
  nowMs: number;
  dateLabel: string;
  t: T;
  onOpenProjects: () => void;
}) {
  const { say } = useAerionCopy();
  const clock = useClock();
  const entries = todayTimeline(snapshot, nowMs, {
    approval: say("today.approval"),
    error: say("today.error"),
    memoryKind: (kind) => tr(t, `hud.memory.${kind}`, kind),
  });

  return (
    <AerionPanel
      slot="today"
      title={say("panel.today")}
      icon={<CalendarDays />}
      testId="aerion-panel-today"
      extra={
        <>
          <span className="aerion-panel-date">{dateLabel}</span>
          <button
            type="button"
            className="aerion-square-button"
            onClick={onOpenProjects}
            aria-label={say("link.open_projects")}
          >
            <Plus aria-hidden />
          </button>
        </>
      }
    >
      {snapshot.active_project ? <ProjectPanel project={snapshot.active_project} t={t} /> : null}
      <ol className="aerion-timeline aerion-scroll">
        <li className="is-now" data-tone="cyan">
          <time>{clock(nowMs)}</time>
          <i aria-hidden />
          <span className="aerion-timeline-label">
            {entries.length === 0 ? say("empty.today") : say("time.now")}
          </span>
        </li>
        {[...entries].reverse().map((entry) => (
          <li key={entry.id} data-tone={entry.tone} data-source={entry.source}>
            <time>{clock(entry.atMs)}</time>
            <i aria-hidden />
            <span className="aerion-timeline-label">{entry.label}</span>
            {entry.detail ? <span className="aerion-timeline-detail">{entry.detail}</span> : null}
          </li>
        ))}
      </ol>
    </AerionPanel>
  );
}

// --------------------------------------------------------------- conversation

type ConversationFilter = "all" | "user" | "assistant";

export function ConversationPanel({
  messages,
  t,
  onOpen,
}: {
  messages: readonly ConversationMessage[];
  t: T;
  onOpen: () => void;
}) {
  const { say } = useAerionCopy();
  const clock = useClock();
  const [filter, setFilter] = useState<ConversationFilter>("all");
  const dialog = useMemo(
    () => messages.filter((m) => m.role === "user" || m.role === "assistant"),
    [messages],
  );
  const counts = {
    all: dialog.length,
    user: dialog.filter((m) => m.role === "user").length,
    assistant: dialog.filter((m) => m.role === "assistant").length,
  };
  const shown = dialog
    .filter((m) => filter === "all" || m.role === filter)
    .slice(-4)
    .reverse();
  const tabs: Array<[ConversationFilter, string]> = [
    ["all", say("filter.all")],
    ["user", t("hud.conversation.user")],
    ["assistant", t("hud.conversation.assistant")],
  ];

  return (
    <AerionPanel
      slot="conversation"
      title={say("panel.conversation")}
      icon={<MessagesSquare />}
      count={counts.all}
      link={{ label: say("link.view_all"), onClick: onOpen }}
      testId="aerion-panel-conversation"
    >
      <div className="aerion-filters" role="group" aria-label={say("panel.conversation")}>
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            className={cn("aerion-filter", filter === key && "is-active")}
            onClick={() => setFilter(key)}
          >
            <span>{label}</span>
            <b>{counts[key]}</b>
          </button>
        ))}
      </div>
      {shown.length > 0 ? (
        <ul className="aerion-rows aerion-scroll">
          {shown.map((m) => (
            <li key={m.id}>
              <button type="button" className="aerion-row aerion-message" data-role={m.role} onClick={onOpen}>
                <IconTile tone={m.role === "user" ? "muted" : "cyan"} size="sm">
                  {m.role === "user" ? <User /> : <Sparkles />}
                </IconTile>
                <span className="aerion-row-main">
                  <span className="aerion-row-title">
                    {m.role === "user" ? t("hud.conversation.user") : t("hud.conversation.assistant")}
                  </span>
                  <span className="aerion-row-sub">{m.content}</span>
                </span>
                <span className="aerion-row-meta">
                  <time>{clock(messageTsToMs(m.ts))}</time>
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <PanelEmpty
          icon={<MessagesSquare />}
          message={t("hud.conversation.empty")}
          action={
            <button type="button" className="aerion-ghost-button" onClick={onOpen}>
              {t("hud.conversation.open")}
            </button>
          }
        />
      )}
    </AerionPanel>
  );
}

// -------------------------------------------------------------------- activity

function activityIcon(kind: string): ReactNode {
  const k = kind.toLowerCase();
  if (k.includes("computer")) return <Monitor />;
  if (k.includes("tool")) return <Wrench />;
  if (k.includes("workflow") || k.includes("run")) return <Workflow />;
  if (k.includes("harness") || k.includes("code") || k.includes("cli")) return <Terminal />;
  if (k.includes("task")) return <ListChecks />;
  if (k.includes("mission") || k.includes("worker") || k.includes("agent")) return <Bot />;
  return <Activity />;
}

function TaskRow({ item, t, nowMs }: { item: HudActivity; t: T; nowMs: number }) {
  const tone = statusTone(item.status);
  const startMs = nsToMs(item.started_at_ns);
  const endMs = item.status === "running" ? nowMs : (nsToMs(item.updated_at_ns) ?? nowMs);
  const context = [item.project_id, item.task_id].filter(Boolean).join(" · ");
  return (
    <li
      className="aerion-row aerion-task"
      data-testid="hud-activity"
      data-kind={item.kind}
      data-status={item.status}
    >
      <IconTile tone={tone === "muted" ? "muted" : tone}>{activityIcon(item.kind)}</IconTile>
      <div className="aerion-row-main">
        <p className="aerion-row-title">{item.label}</p>
        <p className="aerion-row-sub">{item.detail || context || item.kind}</p>
        <div className="aerion-progress" data-status={item.status}>
          <span className="aerion-progress-track">
            <span className="aerion-progress-fill" />
          </span>
          <span className="aerion-progress-value">{formatElapsed(startMs, endMs)}</span>
        </div>
      </div>
      <span className="aerion-status-pill" data-tone={tone}>
        {t(`hud.status.${item.status}`)}
      </span>
    </li>
  );
}

function AgentRow({ item, t }: { item: HudActivity; t: T }) {
  const tone = statusTone(item.status);
  return (
    <li
      className="aerion-row aerion-agent"
      data-testid="hud-activity"
      data-kind={item.kind}
      data-status={item.status}
    >
      <IconTile tone={tone === "muted" ? "muted" : tone} size="sm">
        {activityIcon(item.kind)}
      </IconTile>
      <span className="aerion-agent-name">{item.label}</span>
      <span className="aerion-agent-state" data-tone={tone}>
        <Dot tone={tone} pulse={item.status === "running"} />
        {t(`hud.status.${item.status}`)}
      </span>
      <span className="aerion-agent-detail" data-tone={item.status === "running" ? "cyan" : undefined}>
        {item.detail ||
          [item.mission_id, item.worker_id]
            .filter(Boolean)
            .map((v) => shortId(v))
            .join(" · ")}
      </span>
    </li>
  );
}

export function TasksPanel({
  operations,
  t,
  nowMs,
  onViewAll,
}: {
  operations: HudActivity[];
  t: T;
  nowMs: number;
  onViewAll: () => void;
}) {
  const { say } = useAerionCopy();
  const ordered = orderActivities(operations);
  const running = operations.filter((o) => o.status === "running").length;
  return (
    <AerionPanel
      slot="tasks"
      title={say("panel.tasks")}
      icon={<ListChecks />}
      count={running}
      link={{ label: say("link.view_all"), onClick: onViewAll }}
      testId="aerion-panel-tasks"
    >
      {ordered.length > 0 ? (
        <ul className="aerion-rows aerion-scroll">
          {ordered.map((item) => (
            <TaskRow key={item.activity_id} item={item} t={t} nowMs={nowMs} />
          ))}
        </ul>
      ) : (
        <PanelEmpty icon={<Activity />} message={say("empty.tasks")} />
      )}
    </AerionPanel>
  );
}

export function AgentsPanel({ agents, t, onManage }: { agents: HudActivity[]; t: T; onManage: () => void }) {
  const { say } = useAerionCopy();
  const ordered = orderActivities(agents);
  return (
    <AerionPanel
      slot="agents"
      title={say("panel.agents")}
      icon={<Bot />}
      count={agents.length}
      link={{ label: say("link.manage"), onClick: onManage }}
      testId="aerion-panel-agents"
    >
      {ordered.length > 0 ? (
        <ul className="aerion-rows aerion-rows-tight aerion-scroll">
          {ordered.map((item) => (
            <AgentRow key={item.activity_id} item={item} t={t} />
          ))}
        </ul>
      ) : (
        <PanelEmpty icon={<Bot />} message={say("empty.agents")} />
      )}
    </AerionPanel>
  );
}

/**
 * Missions & agents. Kept as the N-17 export: renders the RUNNING TASKS and
 * AGENTS panels; while either has snapshot content they are grouped under the
 * canonical `hud-panel-activity` test id (the wrapper is `display: contents`
 * so both stay direct members of the column layout).
 */
export function ActivityPanel({
  operations,
  agents,
  t,
  nowMs = Date.now(),
  onViewTasks = () => undefined,
  onManageAgents = () => undefined,
}: {
  operations: HudActivity[];
  agents: HudActivity[];
  t: T;
  nowMs?: number;
  onViewTasks?: () => void;
  onManageAgents?: () => void;
}) {
  const active = operations.length > 0 || agents.length > 0;
  return (
    <div className="aerion-contents" data-testid={active ? "hud-panel-activity" : undefined}>
      <TasksPanel operations={operations} t={t} nowMs={nowMs} onViewAll={onViewTasks} />
      <AgentsPanel agents={agents} t={t} onManage={onManageAgents} />
    </div>
  );
}

// ---------------------------------------------------------------------- memory

function memoryIcon(kind: string): ReactNode {
  const k = kind.toLowerCase();
  if (k.includes("wiki")) return <BookOpen />;
  if (k.includes("profile")) return <User />;
  if (k.includes("memory")) return <Brain />;
  return <Database />;
}

function memoryTone(kind: string): Tone {
  const k = kind.toLowerCase();
  if (k.includes("profile")) return "gold";
  if (k.includes("wiki")) return "cyan";
  return "violet";
}

export function MemoryPanel({
  items,
  t,
  nowMs = Date.now(),
  onViewAll,
}: {
  items: HudMemoryActivity[];
  t: T;
  nowMs?: number;
  onViewAll?: () => void;
}) {
  const { say } = useAerionCopy();
  const ordered = [...items].sort((a, b) => b.at_ns - a.at_ns).slice(0, 6);
  return (
    <AerionPanel
      slot="memory"
      title={tr(t, "hud.panel.memory", say("panel.memory"))}
      icon={<Brain />}
      link={onViewAll ? { label: say("link.view_all"), onClick: onViewAll } : undefined}
      testId={items.length > 0 ? "hud-panel-memory" : "aerion-panel-memory"}
    >
      {ordered.length > 0 ? (
        <ul className="aerion-rows aerion-rows-tight aerion-scroll">
          {ordered.map((item) => (
            <li key={item.activity_id} className="aerion-row aerion-output">
              <span className="aerion-output-icon" data-tone={memoryTone(item.kind)} aria-hidden>
                {memoryIcon(item.kind)}
              </span>
              <span className="aerion-output-name">
                {tr(t, `hud.memory.${item.kind}`, item.kind)}
                {item.subject ? <em>{item.subject}</em> : null}
              </span>
              <span className="aerion-output-age">{ageLabel(say, nsToMs(item.at_ns), nowMs)}</span>
              {item.status ? (
                <span className="aerion-tag" data-tone={memoryTone(item.kind)}>
                  {item.status}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <PanelEmpty icon={<Brain />} message={say("empty.memory")} />
      )}
    </AerionPanel>
  );
}

// ---------------------------------------------------------------------- system

export function ComputerPanel({ computer, t }: { computer: HudComputerActivity; t: T }) {
  const { say } = useAerionCopy();
  const live = Boolean(computer.active || computer.screen_capture_active);
  return (
    <>
      <li
        className="aerion-sys-row"
        data-testid={live ? "hud-panel-computer" : undefined}
        data-tone={computer.active ? "gold" : "muted"}
        title={computer.active ? t("hud.computer.in_control") : t("hud.computer.released")}
      >
        <MonitorSmartphone aria-hidden />
        <span className="aerion-sys-key">{say("system.computer")}</span>
        <span className="aerion-sys-sep" aria-hidden />
        <span className="aerion-sys-value">
          {[computer.phase, computer.last_action_kind].filter(Boolean).join(" · ") || say("system.idle")}
        </span>
        <span className="aerion-sys-sep" aria-hidden />
        <span className="aerion-sys-state">
          <Dot tone={computer.active ? "gold" : "muted"} pulse={computer.active} />
          {computer.active ? say("system.in_control") : say("system.released")}
        </span>
      </li>
      <li className="aerion-sys-row" data-tone={computer.screen_capture_active ? "gold" : "muted"}>
        <ScanSearch aria-hidden />
        <span className="aerion-sys-key">{say("system.capture")}</span>
        <span className="aerion-sys-sep" aria-hidden />
        <span className="aerion-sys-value">
          {computer.screen_capture_active ? computer.capture_target_kind || "…" : "—"}
        </span>
        <span className="aerion-sys-sep" aria-hidden />
        <span className="aerion-sys-state">
          <Dot tone={computer.screen_capture_active ? "gold" : "muted"} />
          {computer.screen_capture_active ? say("system.active") : say("system.idle")}
        </span>
      </li>
    </>
  );
}

export function ErrorPanel({ error, t }: { error: HudError; t: T }) {
  const tone: Tone = error.scope === "global" || !error.recoverable ? "red" : "gold";
  return (
    <li className="aerion-sys-row is-error" data-testid="hud-panel-error" data-tone={tone}>
      <AlertTriangle aria-hidden />
      <span className="aerion-sys-key">{t(`hud.error_scope.${error.scope}`)}</span>
      <span className="aerion-sys-sep" aria-hidden />
      <span
        className="aerion-sys-value"
        title={[error.message, shortId(error.related_id, 24)].filter(Boolean).join(" · ")}
      >
        <b>{error.code}</b>
        {error.message ? ` ${error.message}` : ""}
      </span>
      <span className="aerion-sys-sep" aria-hidden />
      <span className="aerion-sys-state">
        <Dot tone={tone} pulse />
        {error.layer || error.scope}
      </span>
    </li>
  );
}

export function SystemPanel({
  computer,
  error,
  t,
  onOpenSystem,
}: {
  computer: HudComputerActivity;
  error: HudError | null;
  t: T;
  onOpenSystem: () => void;
}) {
  const { say } = useAerionCopy();
  return (
    <AerionPanel
      slot="system"
      title={say("panel.system")}
      icon={<Target />}
      link={{ label: say("link.view_all"), onClick: onOpenSystem }}
      testId="aerion-panel-system"
      tone={error && error.scope === "global" ? "destructive" : undefined}
    >
      <ul className="aerion-sys-rows">
        <ComputerPanel computer={computer} t={t} />
        {error ? (
          <ErrorPanel error={error} t={t} />
        ) : (
          <li className="aerion-sys-row" data-tone="green">
            <ShieldCheck aria-hidden />
            <span className="aerion-sys-key">{say("system.errors")}</span>
            <span className="aerion-sys-sep" aria-hidden />
            <span className="aerion-sys-value">{say("system.no_errors")}</span>
            <span className="aerion-sys-sep" aria-hidden />
            <span className="aerion-sys-state">
              <Dot tone="green" />
              {say("system.ok")}
            </span>
          </li>
        )}
      </ul>
    </AerionPanel>
  );
}
