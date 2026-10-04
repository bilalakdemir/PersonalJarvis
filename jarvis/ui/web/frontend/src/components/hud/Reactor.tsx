/**
 * AERION Energy Core — cinematic presentation of canonical HUD state.
 *
 * Operational truth still comes from HudSnapshot. The renderer is decorative:
 * it projects canonical state as a dense blue/gold intelligence field without
 * inventing operational data.
 */
import aerionCoreTarget from "@/assets/aerion-core-target.webp";
import { REACTOR_STYLES, type AerionVisualState } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type { HudPrimaryState } from "@/types/hud";

type Point = readonly [number, number, number];

const PARTICLES: Point[] = Array.from({ length: 96 }, (_, index) => {
  const angle = (index / 96) * Math.PI * 2;
  const ring = 102 + ((index * 31) % 58);
  const wobble = ((index * 23) % 19) - 9;
  return [
    160 + Math.cos(angle * 1.17) * (ring + wobble),
    160 + Math.sin(angle * 0.91) * (ring - wobble * 0.7),
    0.55 + ((index * 7) % 9) * 0.19,
  ] as const;
});

const NETWORK_NODES: Point[] = Array.from({ length: 54 }, (_, index) => {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const z = 1 - (2 * (index + 0.5)) / 54;
  const radius = Math.sqrt(1 - z * z);
  const theta = golden * index;
  const x = Math.cos(theta) * radius;
  const y = Math.sin(theta) * radius;
  const depth = (z + 1) / 2;
  return [
    160 + x * 82,
    160 + y * 82,
    1.1 + depth * 1.7,
  ] as const;
});

const NETWORK_EDGES = (() => {
  const edges: Array<readonly [number, number]> = [];
  for (let i = 0; i < NETWORK_NODES.length; i += 1) {
    const [x1, y1] = NETWORK_NODES[i];
    const nearest = NETWORK_NODES
      .map(([x2, y2], j) => ({ j, d: Math.hypot(x1 - x2, y1 - y2) }))
      .filter(({ j }) => j !== i)
      .sort((a, b) => a.d - b.d)
      .slice(0, i % 3 === 0 ? 4 : 3);
    for (const { j } of nearest) {
      const a = Math.min(i, j);
      const b = Math.max(i, j);
      if (!edges.some(([x, y]) => x === a && y === b)) edges.push([a, b]);
    }
  }
  return edges;
})();

const STAR_NODES = NETWORK_NODES.filter((_, index) => index % 3 === 0);

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
        <img
          className="aerion-core-target-asset"
          src={aerionCoreTarget}
          alt=""
          draggable={false}
        />
        <svg viewBox="0 0 320 320" className="aerion-energy-svg aerion-energy-svg-overlay">
          <defs>
            <radialGradient id="aerion-core-fill" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#fff" stopOpacity="1" />
              <stop offset="12%" stopColor="#fffbe8" stopOpacity="1" />
              <stop offset="25%" stopColor="#ffd36a" stopOpacity="0.98" />
              <stop offset="43%" stopColor="#ffae3d" stopOpacity="0.5" />
              <stop offset="66%" stopColor="currentColor" stopOpacity="0.18" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="aerion-sphere-fill" cx="50%" cy="48%" r="56%">
              <stop offset="0%" stopColor="#087aa6" stopOpacity="0.22" />
              <stop offset="58%" stopColor="#01324d" stopOpacity="0.16" />
              <stop offset="100%" stopColor="#00111d" stopOpacity="0.02" />
            </radialGradient>
            <linearGradient id="aerion-blue-orbit" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#007fc4" stopOpacity="0.15" />
              <stop offset="30%" stopColor="#29cfff" stopOpacity="1" />
              <stop offset="55%" stopColor="#e5fbff" stopOpacity="0.96" />
              <stop offset="78%" stopColor="#16a8ff" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#007fc4" stopOpacity="0.12" />
            </linearGradient>
            <linearGradient id="aerion-gold-orbit" x1="0%" y1="100%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#ff9636" stopOpacity="0.05" />
              <stop offset="30%" stopColor="#ffbd4c" stopOpacity="0.95" />
              <stop offset="52%" stopColor="#fff0a8" stopOpacity="1" />
              <stop offset="76%" stopColor="#ff9c34" stopOpacity="0.86" />
              <stop offset="100%" stopColor="#ff9636" stopOpacity="0.04" />
            </linearGradient>
            <clipPath id="aerion-sphere-clip">
              <circle cx="160" cy="160" r="88" />
            </clipPath>
            <filter id="aerion-blue-glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="2.8" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <filter id="aerion-gold-glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="3.5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          <g className="aerion-tech-rings">
            <circle cx="160" cy="160" r="151" fill="none" stroke="#0dcfff" strokeWidth="0.55" strokeDasharray="2 6 18 9 1 8" />
            <circle cx="160" cy="160" r="143" fill="none" stroke="#0dcfff" strokeWidth="0.45" strokeDasharray="1 5" />
            <circle cx="160" cy="160" r="134" fill="none" stroke="#ffbb4c" strokeWidth="0.5" strokeDasharray="22 8 3 11" />
            <circle cx="160" cy="160" r="122" fill="none" stroke="#0dcfff" strokeWidth="0.4" strokeDasharray="2 9" />
            <circle cx="160" cy="160" r="110" fill="none" stroke="#0dcfff" strokeWidth="0.5" strokeDasharray="12 8" />
          </g>

          <g className="aerion-energy-rays">
            <path d="M160 0 L160 45 M160 275 L160 320 M0 160 L45 160 M275 160 L320 160" />
            <path d="M47 47 L82 82 M238 238 L273 273 M273 47 L238 82 M82 238 L47 273" />
          </g>

          <ellipse className="aerion-core-pedestal aerion-core-pedestal-back" cx="160" cy="282" rx="103" ry="20" />
          <ellipse className="aerion-core-pedestal aerion-core-pedestal-mid" cx="160" cy="284" rx="83" ry="12" />
          <ellipse className="aerion-core-pedestal aerion-core-pedestal-front" cx="160" cy="286" rx="57" ry="7" />

          <circle cx="160" cy="160" r="91" fill="url(#aerion-sphere-fill)" />

          <g className="aerion-energy-particles">
            {PARTICLES.map(([cx, cy, r], index) => (
              <circle
                key={index}
                cx={cx}
                cy={cy}
                r={r}
                fill={index % 4 === 0 ? "#ffc050" : index % 7 === 0 ? "#fff" : "#18cfff"}
              />
            ))}
          </g>

          <g clipPath="url(#aerion-sphere-clip)" className="aerion-network-mesh">
            {NETWORK_EDGES.map(([a, b], index) => {
              const [x1, y1] = NETWORK_NODES[a];
              const [x2, y2] = NETWORK_NODES[b];
              return (
                <line
                  key={`${a}-${b}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={index % 9 === 0 ? "#ffbc4f" : "#53d9ff"}
                  strokeWidth={index % 7 === 0 ? "1.15" : "0.48"}
                  opacity={index % 5 === 0 ? "0.92" : "0.54"}
                />
              );
            })}
            {NETWORK_NODES.map(([cx, cy, r], index) => (
              <circle
                key={index}
                cx={cx}
                cy={cy}
                r={r}
                fill={index % 8 === 0 ? "#ffd16a" : index % 5 === 0 ? "#fff" : "#45d8ff"}
                opacity={index % 4 === 0 ? "1" : "0.82"}
              />
            ))}
          </g>

          <g className="aerion-star-nodes">
            {STAR_NODES.map(([cx, cy], index) => (
              <g key={index} transform={`translate(${cx} ${cy})`}>
                <circle r={index % 4 === 0 ? 3.1 : 2.1} fill={index % 4 === 0 ? "#ffd16a" : "#eaffff"} />
                <path
                  d="M-6 0 H6 M0 -6 V6"
                  stroke={index % 4 === 0 ? "#ffb846" : "#66ddff"}
                  strokeWidth="0.72"
                />
              </g>
            ))}
          </g>

          <g className="aerion-energy-orbit aerion-energy-orbit-a" filter="url(#aerion-gold-glow)">
            <ellipse cx="160" cy="160" rx="127" ry="54" fill="none" stroke="url(#aerion-gold-orbit)" strokeWidth="3.8" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-b" filter="url(#aerion-blue-glow)">
            <ellipse cx="160" cy="160" rx="123" ry="48" fill="none" stroke="url(#aerion-blue-orbit)" strokeWidth="4.1" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-c" filter="url(#aerion-blue-glow)">
            <ellipse cx="160" cy="160" rx="111" ry="61" fill="none" stroke="#56ddff" strokeWidth="2.15" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-d" filter="url(#aerion-gold-glow)">
            <ellipse cx="160" cy="160" rx="114" ry="39" fill="none" stroke="#ffc04f" strokeWidth="2.15" />
          </g>
          <g className="aerion-energy-orbit aerion-energy-orbit-e" filter="url(#aerion-blue-glow)">
            <ellipse cx="160" cy="160" rx="103" ry="72" fill="none" stroke="#21c9ff" strokeWidth="1.45" />
          </g>

          <g className="aerion-energy-state-ring">
            <circle
              cx="160"
              cy="160"
              r="103"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.25"
              strokeDasharray={
                style.pattern === "dotted" ? "2 10"
                  : style.pattern === "dashed" ? "12 8"
                    : style.pattern === "segmented" ? "26 9"
                      : style.pattern === "broken" ? "36 14 5 14"
                        : "8 5"
              }
            />
          </g>

          <circle className="aerion-energy-core-glow" cx="160" cy="160" r="49" fill="url(#aerion-core-fill)" />
          <circle className="aerion-energy-core-node aerion-energy-core-node-outer" cx="160" cy="160" r="18" fill="#fff0b5" opacity="0.42" />
          <circle className="aerion-energy-core-node" cx="160" cy="160" r="8.5" fill="#fff" />
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
