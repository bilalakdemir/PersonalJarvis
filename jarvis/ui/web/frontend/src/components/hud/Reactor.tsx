/**
 * The central reactor of the HUD workspace (N-17).
 *
 * A pure presentation of `snapshot.primary_state`: it owns no state and asks
 * the backend for nothing. All motion is client-side CSS (`.hud-reactor*` in
 * index.css) — the backend never streams frames — and every state is told
 * apart without motion by three static cues: the readable label underneath,
 * the tone token, and the ring's stroke pattern (REACTOR_STYLES). Under
 * `prefers-reduced-motion` the rings simply stand still.
 *
 * Colours come from theme tokens only, so light and dark both work.
 */
import { REACTOR_STYLES, type ReactorStyle } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type { HudPrimaryState } from "@/types/hud";

const TONE_CLASS: Record<ReactorStyle["tone"], string> = {
  muted: "text-muted-foreground",
  accent: "text-accent",
  info: "text-info",
  warning: "text-warning",
  success: "text-success",
  destructive: "text-destructive",
};

const DASH: Record<ReactorStyle["pattern"], string | undefined> = {
  solid: undefined,
  double: undefined,
  dotted: "1 7",
  dashed: "10 6",
  segmented: "24 8",
  broken: "36 14 4 14",
};

export function Reactor({
  state,
  label,
  attention,
}: {
  state: HudPrimaryState;
  label: string;
  attention: readonly string[];
}) {
  const style = REACTOR_STYLES[state];
  const dash = DASH[style.pattern];
  return (
    <figure
      className="flex flex-col items-center gap-3"
      data-testid="hud-reactor"
      data-state={state}
      data-pattern={style.pattern}
    >
      <div
        className={cn("hud-reactor relative h-48 w-48 sm:h-56 sm:w-56", TONE_CLASS[style.tone])}
        data-state={state}
        aria-hidden
      >
        <svg viewBox="0 0 200 200" className="h-full w-full">
          {/* Outer arc ring — the slow rotation layer. */}
          <circle
            className="hud-reactor-outer"
            cx="100"
            cy="100"
            r="92"
            fill="none"
            stroke="currentColor"
            strokeOpacity="0.35"
            strokeWidth="2"
            strokeDasharray={dash ?? "140 24"}
          />
          {/* Middle ring carries the state's static stroke pattern. */}
          <circle
            className="hud-reactor-mid"
            cx="100"
            cy="100"
            r="72"
            fill="none"
            stroke="currentColor"
            strokeOpacity="0.7"
            strokeWidth={style.pattern === "double" ? 2 : 4}
            strokeDasharray={dash}
            strokeLinecap="round"
          />
          {style.pattern === "double" ? (
            <circle
              cx="100"
              cy="100"
              r="64"
              fill="none"
              stroke="currentColor"
              strokeOpacity="0.55"
              strokeWidth="2"
            />
          ) : null}
          {/* Core. */}
          <circle
            className="hud-reactor-core"
            cx="100"
            cy="100"
            r="38"
            fill="currentColor"
            fillOpacity="0.14"
            stroke="currentColor"
            strokeOpacity="0.9"
            strokeWidth="3"
          />
          <circle cx="100" cy="100" r="12" fill="currentColor" fillOpacity="0.85" />
        </svg>
      </div>
      <figcaption className="text-center">
        <span
          className={cn("block text-lg font-semibold tracking-wide", TONE_CLASS[style.tone])}
          data-testid="hud-state-label"
        >
          {label}
        </span>
        {attention.length > 0 ? (
          <span className="mt-1 flex flex-wrap justify-center gap-1.5" data-testid="hud-attention">
            {attention.map((flag) => (
              <span
                key={flag}
                className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground"
              >
                {flag}
              </span>
            ))}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
