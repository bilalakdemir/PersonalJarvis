/**
 * AERION Energy Core — cinematic presentation of canonical HUD state.
 *
 * Operational truth still comes from HudSnapshot. This component only renders
 * that truth as the approved living energy field and preserves the old reactor
 * test/accessibility contracts while changing the visual language completely.
 */
import { REACTOR_STYLES, type AerionVisualState } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type { HudPrimaryState } from "@/types/hud";

const PARTICLES = [
  [158, 44, 1.8], [232, 73, 1.4], [273, 138, 1.6], [259, 226, 1.2],
  [207, 276, 1.7], [116, 270, 1.3], [54, 222, 1.5], [43, 131, 1.2],
  [82, 67, 1.5], [190, 31, 1.1], [286, 186, 1.1], [77, 254, 1.0],
] as const;

const MESH_LONGITUDES = [-48, -32, -16, 0, 16, 32, 48] as const;
const MESH_LATITUDES = [72, 92, 112, 132, 152, 172, 192, 212, 232] as const;

export function Reactor({
  state,
  label,
  attention,
  visualState,
}: {
  state: HudPrimaryState;
  label: string;
  attention: readonly string[];
  visualState: AerionVisualState;
}) {
  const style = REACTOR_STYLES[state];

  return (
    <figure
      className="aerion-energy-figure flex flex-col items-center gap-3"
      data-testid="hud-reactor"
      data-state={state}
      data-visual-state={visualState}
      data-pattern={style.pattern}
    >
      <div
        className="aerion-energy-core-visual relative"
        data-visual-state={visualState}
        aria-hidden
      >
        <div className="aerion-energy-aura" />
        <svg viewBox="0 0 320 320" className="aerion-energy-svg">
          <defs>
            <radialGradient id="aerion-core-fill" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="white" stopOpacity="1" />
              <stop offset="23%" stopColor="currentColor" stopOpacity="1" />
              <stop offset="65%" stopColor="currentColor" stopOpacity="0.34" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="aerion-halo-fill" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="currentColor" stopOpacity="0.32" />
              <stop offset="75%" stopColor="currentColor" stopOpacity="0.05" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
            <clipPath id="aerion-sphere-clip">
              <circle cx="160" cy="160" r="82" />
            </clipPath>
          </defs>

          <circle className="aerion-energy-halo" cx="160" cy="160" r="126" fill="url(#aerion-halo-fill)" />

          <g className="aerion-energy-particles">
            {PARTICLES.map(([cx, cy, r], index) => (
              <circle key={index} cx={cx} cy={cy} r={r} fill="currentColor" />
            ))}
          </g>

          <g className="aerion-energy-orbit aerion-energy-orbit-a">
            <ellipse cx="160" cy="160" rx="123" ry="54" fill="none" stroke="currentColor" strokeWidth="2.2" />
            <circle cx="282" cy="160" r="3.2" fill="currentColor" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-b">
            <ellipse cx="160" cy="160" rx="117" ry="48" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <circle cx="43" cy="160" r="2.6" fill="currentColor" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-c">
            <ellipse cx="160" cy="160" rx="112" ry="42" fill="none" stroke="currentColor" strokeWidth="1.2" />
          </g>

          <g clipPath="url(#aerion-sphere-clip)" className="aerion-energy-mesh">
            <circle cx="160" cy="160" r="82" fill="none" stroke="currentColor" strokeWidth="1.4" />
            {MESH_LATITUDES.map((y) => {
              const dy = Math.abs(y - 160);
              const rx = Math.sqrt(Math.max(0, 82 * 82 - dy * dy));
              return (
                <ellipse
                  key={y}
                  cx="160"
                  cy={y}
                  rx={rx}
                  ry={Math.max(5, rx * 0.22)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="0.75"
                />
              );
            })}
            {MESH_LONGITUDES.map((rotation) => (
              <ellipse
                key={rotation}
                cx="160"
                cy="160"
                rx="28"
                ry="82"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.75"
                transform={`rotate(${rotation} 160 160)`}
              />
            ))}
            <path d="M78 160 C112 112 208 112 242 160 C208 208 112 208 78 160Z" fill="none" stroke="currentColor" strokeWidth="0.9" />
            <path d="M91 119 C139 147 181 147 229 119" fill="none" stroke="currentColor" strokeWidth="0.7" />
            <path d="M91 201 C139 173 181 173 229 201" fill="none" stroke="currentColor" strokeWidth="0.7" />
          </g>

          <g className="aerion-energy-state-ring">
            <circle
              cx="160"
              cy="160"
              r="95"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeDasharray={
                style.pattern === "dotted" ? "2 10"
                  : style.pattern === "dashed" ? "12 8"
                    : style.pattern === "segmented" ? "26 9"
                      : style.pattern === "broken" ? "36 14 5 14"
                        : undefined
              }
            />
          </g>

          <circle className="aerion-energy-core-glow" cx="160" cy="160" r="54" fill="url(#aerion-core-fill)" />
          <circle className="aerion-energy-core-node" cx="160" cy="160" r="16" fill="white" />
        </svg>
      </div>

      <figcaption className="text-center">
        <span
          className="aerion-energy-label block text-lg font-semibold tracking-wide"
          data-testid="hud-state-label"
        >
          {label}
        </span>
        {attention.length > 0 ? (
          <span className="mt-1 flex flex-wrap justify-center gap-1.5" data-testid="hud-attention">
            {attention.map((flag) => (
              <span
                key={flag}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-xs",
                  "border-[color:var(--aerion-hairline)] text-[color:var(--aerion-muted)]",
                )}
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
