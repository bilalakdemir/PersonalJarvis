/**
 * AERION Energy Core — cinematic presentation of canonical HUD state.
 *
 * Operational truth still comes from HudSnapshot. This component only renders
 * that truth as the approved living energy field.
 */
import { REACTOR_STYLES, type AerionVisualState } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type { HudPrimaryState } from "@/types/hud";

const PARTICLES = Array.from({ length: 72 }, (_, index) => {
  const angle = (index / 72) * Math.PI * 2;
  const ring = 98 + ((index * 37) % 64);
  const wobble = ((index * 19) % 17) - 8;
  return [
    160 + Math.cos(angle * 1.13) * (ring + wobble),
    160 + Math.sin(angle * 0.93) * (ring - wobble * 0.6),
    0.7 + ((index * 7) % 8) * 0.22,
  ] as const;
});

const STAR_POINTS = Array.from({ length: 30 }, (_, index) => {
  const angle = (index / 30) * Math.PI * 2 + 0.17;
  const radius = 62 + ((index * 29) % 42);
  return [
    160 + Math.cos(angle) * radius,
    160 + Math.sin(angle) * radius,
    1 + ((index * 11) % 5) * 0.45,
  ] as const;
});

const MESH_LONGITUDES = [-72, -56, -40, -24, -8, 8, 24, 40, 56, 72] as const;
const MESH_LATITUDES = [88, 100, 112, 124, 136, 148, 160, 172, 184, 196, 208, 220, 232] as const;

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
      <div className="aerion-energy-core-visual relative" data-visual-state={visualState} aria-hidden>
        <div className="aerion-energy-aura" />
        <div className="aerion-energy-aura aerion-energy-aura-gold" />
        <svg viewBox="0 0 320 320" className="aerion-energy-svg">
          <defs>
            <radialGradient id="aerion-core-fill" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
              <stop offset="14%" stopColor="#fff8d6" stopOpacity="1" />
              <stop offset="28%" stopColor="#ffd56f" stopOpacity="0.98" />
              <stop offset="46%" stopColor="currentColor" stopOpacity="0.9" />
              <stop offset="72%" stopColor="currentColor" stopOpacity="0.24" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="aerion-halo-fill" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity="0.18" />
              <stop offset="32%" stopColor="currentColor" stopOpacity="0.28" />
              <stop offset="72%" stopColor="currentColor" stopOpacity="0.06" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="aerion-gold-streak" x1="0%" y1="50%" x2="100%" y2="50%">
              <stop offset="0%" stopColor="#ffb347" stopOpacity="0" />
              <stop offset="32%" stopColor="#ffd36b" stopOpacity="0.8" />
              <stop offset="50%" stopColor="#fff2b0" stopOpacity="1" />
              <stop offset="68%" stopColor="#ffd36b" stopOpacity="0.8" />
              <stop offset="100%" stopColor="#ffb347" stopOpacity="0" />
            </linearGradient>
            <clipPath id="aerion-sphere-clip">
              <circle cx="160" cy="160" r="86" />
            </clipPath>
          </defs>

          <g className="aerion-tech-rings">
            <circle cx="160" cy="160" r="151" fill="none" stroke="currentColor" strokeWidth="0.7" strokeDasharray="3 7 18 8 1 6" />
            <circle cx="160" cy="160" r="143" fill="none" stroke="currentColor" strokeWidth="0.55" strokeDasharray="1 4" />
            <circle cx="160" cy="160" r="133" fill="none" stroke="currentColor" strokeWidth="0.7" strokeDasharray="24 7 4 10" />
            <circle cx="160" cy="160" r="118" fill="none" stroke="currentColor" strokeWidth="0.45" strokeDasharray="2 8" />
          </g>

          <g className="aerion-energy-rays">
            <path d="M160 0 L160 44 M160 276 L160 320 M0 160 L44 160 M276 160 L320 160" />
            <path d="M46 46 L79 79 M241 241 L274 274 M274 46 L241 79 M79 241 L46 274" />
            <path d="M111 7 L126 51 M209 269 L224 313 M313 111 L269 126 M51 209 L7 224" />
          </g>

          <circle className="aerion-energy-halo" cx="160" cy="160" r="132" fill="url(#aerion-halo-fill)" />

          <g className="aerion-energy-particles">
            {PARTICLES.map(([cx, cy, r], index) => (
              <circle key={index} cx={cx} cy={cy} r={r} fill={index % 5 === 0 ? "#ffd36b" : "currentColor"} />
            ))}
          </g>

          <g className="aerion-energy-stars">
            {STAR_POINTS.map(([cx, cy, r], index) => (
              <g key={index} transform={`translate(${cx} ${cy})`}>
                <circle r={r} fill={index % 3 === 0 ? "#fff2b0" : "#d9fbff"} />
                <path d="M-5 0 H5 M0 -5 V5" stroke={index % 3 === 0 ? "#ffd36b" : "#76dcff"} strokeWidth="0.75" />
              </g>
            ))}
          </g>

          <g className="aerion-energy-orbit aerion-energy-orbit-a">
            <ellipse cx="160" cy="160" rx="128" ry="56" fill="none" stroke="url(#aerion-gold-streak)" strokeWidth="3.2" />
            <circle cx="287" cy="160" r="3.7" fill="#ffd36b" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-b">
            <ellipse cx="160" cy="160" rx="122" ry="51" fill="none" stroke="currentColor" strokeWidth="2.3" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-c">
            <ellipse cx="160" cy="160" rx="116" ry="44" fill="none" stroke="#7ee5ff" strokeWidth="1.7" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-d">
            <ellipse cx="160" cy="160" rx="130" ry="37" fill="none" stroke="#b7f2ff" strokeWidth="1.2" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-e">
            <ellipse cx="160" cy="160" rx="108" ry="64" fill="none" stroke="#ffbf56" strokeWidth="1.25" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-f">
            <ellipse cx="160" cy="160" rx="98" ry="72" fill="none" stroke="#33ccff" strokeWidth="1.1" />
          </g>

          <g clipPath="url(#aerion-sphere-clip)" className="aerion-energy-mesh">
            <circle cx="160" cy="160" r="86" fill="none" stroke="currentColor" strokeWidth="1.4" />
            {MESH_LATITUDES.map((y) => {
              const dy = Math.abs(y - 160);
              const rx = Math.sqrt(Math.max(0, 86 * 86 - dy * dy));
              return (
                <ellipse key={y} cx="160" cy={y} rx={rx} ry={Math.max(4, rx * 0.21)} fill="none" stroke="currentColor" strokeWidth="0.65" />
              );
            })}
            {MESH_LONGITUDES.map((rotation) => (
              <ellipse key={rotation} cx="160" cy="160" rx="28" ry="86" fill="none" stroke="currentColor" strokeWidth="0.65" transform={`rotate(${rotation} 160 160)`} />
            ))}
            <path d="M74 160 C106 108 214 108 246 160 C214 212 106 212 74 160Z" fill="none" stroke="currentColor" strokeWidth="0.9" />
            <path d="M82 124 C130 154 190 154 238 124" fill="none" stroke="currentColor" strokeWidth="0.8" />
            <path d="M82 196 C130 166 190 166 238 196" fill="none" stroke="currentColor" strokeWidth="0.8" />
            <path d="M104 96 C146 134 174 186 216 224" fill="none" stroke="#ffd36b" strokeWidth="0.75" opacity="0.7" />
            <path d="M216 96 C174 134 146 186 104 224" fill="none" stroke="#7ce5ff" strokeWidth="0.75" opacity="0.8" />
          </g>

          <g className="aerion-energy-state-ring">
            <circle
              cx="160"
              cy="160"
              r="101"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeDasharray={
                style.pattern === "dotted" ? "2 10"
                  : style.pattern === "dashed" ? "12 8"
                    : style.pattern === "segmented" ? "26 9"
                      : style.pattern === "broken" ? "36 14 5 14"
                        : "8 5"
              }
            />
          </g>

          <circle className="aerion-energy-core-glow" cx="160" cy="160" r="62" fill="url(#aerion-core-fill)" />
          <circle className="aerion-energy-core-node aerion-energy-core-node-outer" cx="160" cy="160" r="27" fill="#fff7d4" opacity="0.55" />
          <circle className="aerion-energy-core-node" cx="160" cy="160" r="14" fill="white" />
        </svg>
      </div>

      <figcaption className="text-center">
        <span className="aerion-energy-label block text-lg font-semibold tracking-wide" data-testid="hud-state-label">
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
