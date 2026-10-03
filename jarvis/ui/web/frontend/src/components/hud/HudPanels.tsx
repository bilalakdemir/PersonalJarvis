/**
 * Contextual panels of the HUD workspace (N-17). Each renders recorded
 * operational state from the snapshot and nothing else: no hidden reasoning,
 * no raw memory, no screen content, no prompts. Every string arrived
 * secret-masked and capped from the backend reducer.
 */
import { useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Bot,
  Brain,
  FolderOpen,
  MonitorSmartphone,
  ShieldQuestion,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { decideHudApproval, type HudDecision } from "@/lib/hudApi";
import { canDecide, secondsLeft, shortId } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type {
  HudActivity,
  HudApproval,
  HudComputerActivity,
  HudError,
  HudMemoryActivity,
  HudProject,
} from "@/types/hud";

type T = (key: string) => string;

/** A translation, or `fallback` when the key has no string yet (the i18n
 *  resolver returns the key itself on a miss). */
export function tr(t: T, key: string, fallback: string): string {
  const value = t(key);
  return value === key ? fallback : value;
}

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
    <section
      className={cn(
        "rounded-lg border bg-card/80 p-4 shadow-sm backdrop-blur-sm",
        tone === "warning" && "border-warning/60",
        tone === "destructive" && "border-destructive/60",
        !tone && "border-border",
      )}
      data-testid={testId}
      aria-label={title}
    >
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        <span aria-hidden className="[&>svg]:h-4 [&>svg]:w-4">
          {icon}
        </span>
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex gap-2 text-sm">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{value}</dd>
    </div>
  );
}

// ------------------------------------------------------------------ approvals

export function ApprovalCard({ card, t, nowMs }: { card: HudApproval; t: T; nowMs: number }) {
  const [pending, setPending] = useState<HudDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const decidable = canDecide(card);
  const left = secondsLeft(card, nowMs);

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
      className="rounded-md border border-border bg-background/60 p-3"
      data-testid="hud-approval-card"
      data-approval-id={card.approval_id}
    >
      <p className="mb-2 font-medium text-foreground">{card.action}</p>
      <dl className="space-y-1">
        <Field label={t("hud.approval.target")} value={card.target_preview} />
        <Field label={t("hud.approval.project")} value={card.project_id} />
        <Field label={t("hud.approval.reason")} value={card.reason} />
        <Field label={t("hud.approval.risk")} value={card.risk_tier} />
        <Field
          label={t("hud.approval.expires")}
          value={left === null ? null : `${left}s`}
        />
        <Field
          label={t("hud.approval.identity")}
          value={
            <code className="text-xs" title={card.approval_id}>
              {card.kind === "project_state"
                ? [
                    card.queue_item_id ? `#${card.queue_item_id}` : null,
                    shortId(card.transaction_id),
                    shortId(card.proposal_digest),
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : card.kind === "memory_promotion"
                  ? [
                      card.candidate_id ? `#${card.candidate_id}` : null,
                      shortId(card.proposal_digest),
                    ]
                      .filter(Boolean)
                      .join(" · ")
                  : `${shortId(card.mission_id)} · ${shortId(card.trace_id)}`}
            </code>
          }
        />
      </dl>
      {decidable ? (
        <div className="mt-3 flex gap-2">
          <Button
            size="sm"
            onClick={() => void decide("approve")}
            disabled={pending !== null}
            aria-label={`${t("hud.approval.approve")}: ${card.action}`}
            data-testid="hud-approve"
          >
            {t("hud.approval.approve")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void decide("deny")}
            disabled={pending !== null}
            aria-label={`${t("hud.approval.deny")}: ${card.action}`}
            data-testid="hud-deny"
          >
            {t("hud.approval.deny")}
          </Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground" data-testid="hud-approval-readonly">
          {t(`hud.approval.read_only.${card.read_only_reason || "no_out_of_band_route"}`)}
        </p>
      )}
      {error ? (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}

export function ApprovalsPanel({ cards, t, nowMs }: { cards: HudApproval[]; t: T; nowMs: number }) {
  return (
    <HudPanel
      title={t("hud.panel.approvals")}
      icon={<ShieldQuestion />}
      testId="hud-panel-approvals"
      tone="warning"
    >
      <div className="space-y-3">
        {cards.map((card) => (
          <ApprovalCard key={card.approval_id} card={card} t={t} nowMs={nowMs} />
        ))}
      </div>
    </HudPanel>
  );
}

// -------------------------------------------------------------------- project

export function ProjectPanel({ project, t }: { project: HudProject; t: T }) {
  return (
    <HudPanel title={t("hud.panel.project")} icon={<FolderOpen />} testId="hud-panel-project">
      <p className="font-medium text-foreground">{project.project_name || project.project_id}</p>
      <dl className="mt-2 space-y-1">
        <Field
          label={t("hud.project.current")}
          value={project.current_task ?? t("hud.project.no_current")}
        />
        <Field label={t("hud.project.revision")} value={shortId(project.state_revision, 12)} />
      </dl>
      {!project.state_valid ? (
        <p className="mt-2 text-xs text-warning">
          {t("hud.project.invalid")} {project.issue_codes.join(", ")}
        </p>
      ) : null}
    </HudPanel>
  );
}

// -------------------------------------------------------------------- activity

function ActivityRow({ item, t }: { item: HudActivity; t: T }) {
  return (
    <li className="flex items-start gap-2 text-sm" data-testid="hud-activity" data-kind={item.kind}>
      <span
        className={cn(
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          item.status === "running" && "bg-info",
          item.status === "completed" && "bg-success",
          item.status === "failed" && "bg-destructive",
          item.status === "cancelled" && "bg-muted-foreground",
        )}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-foreground">
          {item.label}
          <span className="ml-2 text-xs text-muted-foreground">
            {t(`hud.status.${item.status}`)}
          </span>
        </p>
        {item.detail ? <p className="truncate text-xs text-muted-foreground">{item.detail}</p> : null}
        {item.project_id || item.task_id ? (
          <p className="text-xs text-muted-foreground">
            {[item.project_id, item.task_id].filter(Boolean).join(" · ")}
          </p>
        ) : null}
      </div>
    </li>
  );
}

export function ActivityPanel({
  operations,
  agents,
  t,
}: {
  operations: HudActivity[];
  agents: HudActivity[];
  t: T;
}) {
  return (
    <HudPanel title={t("hud.panel.activity")} icon={<Bot />} testId="hud-panel-activity">
      {agents.length > 0 ? (
        <>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">{t("hud.activity.agents")}</h3>
          <ul className="mb-3 space-y-2">
            {agents.map((item) => (
              <ActivityRow key={item.activity_id} item={item} t={t} />
            ))}
          </ul>
        </>
      ) : null}
      {operations.length > 0 ? (
        <>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">
            {t("hud.activity.operations")}
          </h3>
          <ul className="space-y-2">
            {operations.map((item) => (
              <ActivityRow key={item.activity_id} item={item} t={t} />
            ))}
          </ul>
        </>
      ) : null}
    </HudPanel>
  );
}

// -------------------------------------------------------------------- computer

export function ComputerPanel({ computer, t }: { computer: HudComputerActivity; t: T }) {
  return (
    <HudPanel title={t("hud.panel.computer")} icon={<MonitorSmartphone />} testId="hud-panel-computer">
      <dl className="space-y-1">
        <Field
          label={t("hud.computer.control")}
          value={computer.active ? t("hud.computer.in_control") : t("hud.computer.released")}
        />
        <Field label={t("hud.computer.phase")} value={computer.phase} />
        <Field label={t("hud.computer.action")} value={computer.last_action_kind} />
        <Field
          label={t("hud.computer.capture")}
          value={computer.screen_capture_active ? computer.capture_target_kind || "…" : null}
        />
      </dl>
    </HudPanel>
  );
}

// ---------------------------------------------------------------------- memory

export function MemoryPanel({ items, t }: { items: HudMemoryActivity[]; t: T }) {
  return (
    <HudPanel title={t("hud.panel.memory")} icon={<Brain />} testId="hud-panel-memory">
      <ul className="space-y-1 text-sm">
        {items.slice(0, 6).map((item) => (
          <li key={item.activity_id} className="flex gap-2">
            <span className="text-foreground">{tr(t, `hud.memory.${item.kind}`, item.kind)}</span>
            <span className="text-muted-foreground">{item.subject}</span>
            <span className="ml-auto text-xs text-muted-foreground">{item.status}</span>
          </li>
        ))}
      </ul>
    </HudPanel>
  );
}

// ---------------------------------------------------------------------- errors

export function ErrorPanel({ error, t }: { error: HudError; t: T }) {
  return (
    <HudPanel
      title={t("hud.panel.error")}
      icon={<AlertTriangle />}
      testId="hud-panel-error"
      tone={error.scope === "global" ? "destructive" : undefined}
    >
      <p className="text-sm text-foreground">
        <span className="font-medium">{t(`hud.error_scope.${error.scope}`)}</span>
        <span className="ml-2 text-muted-foreground">{error.code}</span>
      </p>
      {error.message ? <p className="mt-1 text-sm text-muted-foreground">{error.message}</p> : null}
      {error.related_id ? (
        <p className="mt-1 text-xs text-muted-foreground">{shortId(error.related_id, 24)}</p>
      ) : null}
    </HudPanel>
  );
}
