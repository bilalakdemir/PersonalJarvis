/**
 * Concentric technical HUD rings around the AERION core (static SVG).
 *
 * Decorative except for the state ring, which carries the canonical
 * `REACTOR_STYLES[state].pattern` so every state stays distinguishable
 * without motion (label + tone + pattern).
 */
import { memo } from "react";

import type { ReactorStyle } from "@/lib/hudSemantics";

const TAU = Math.PI * 2;

function polar(r: number, deg: number): [number, number] {
  const a = (deg / 360) * TAU;
  return [Math.cos(a) * r, Math.sin(a) * r];
}

function arc(r: number, from: number, to: number): string {
  const [x0, y0] = polar(r, from);
  const [x1, y1] = polar(r, to);
  const sweep = (((to - from) % 360) + 360) % 360;
  const large = sweep > 180 ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function ticks(rOuter: number, step: number, minor: number, major: number, every: number): string {
  let d = "";
  for (let i = 0, deg = 0; deg < 360; i += 1, deg += step) {
    const len = i % every === 0 ? major : minor;
    const [x0, y0] = polar(rOuter - len, deg);
    const [x1, y1] = polar(rOuter, deg);
    d += `M${x0.toFixed(1)} ${y0.toFixed(1)}L${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }
  return d;
}

const OUTER_TICKS = ticks(492, 2, 5, 13, 5);
const SPOKES = ticks(346, 5, 14, 22, 6);
const FAR_TICKS = ticks(560, 6, 8, 18, 5);
const MID_TICKS = ticks(452, 1.5, 3, 3, 1);
const INNER_TICKS = ticks(404, 10, 6, 6, 1);
const GOLD_ARCS = [arc(457, 200, 246), arc(457, 292, 331), arc(457, 18, 52), arc(457, 122, 150)].join("");
const CYAN_ARCS = [arc(362, 150, 214), arc(362, 326, 390), arc(425, 160, 200), arc(425, 340, 380)].join("");
const GOLD_DOT_ARCS = [
  arc(392, 96, 170),
  arc(392, 10, 84),
  arc(378, 196, 238),
  arc(378, 302, 344),
  arc(448, 112, 150),
  arc(448, 30, 68),
].join("");
const BRACKETS = [arc(412, 40, 50), arc(412, 130, 140), arc(412, 220, 230), arc(412, 310, 320)].join("");

const DIAMONDS: Array<[number, number]> = [0, 90, 180, 270].map((deg) => polar(404, deg));

const PATTERN_DASH: Record<ReactorStyle["pattern"], string | undefined> = {
  solid: undefined,
  double: undefined,
  dotted: "1.5 8",
  dashed: "18 10",
  segmented: "46 10",
  broken: "70 22 8 22",
};

export const CoreRings = memo(function CoreRings({ pattern }: { pattern: ReactorStyle["pattern"] }) {
  const [sx, sy] = polar(440, -52);
  return (
    <svg className="aerion-core-rings" viewBox="-500 -500 1000 1000" aria-hidden focusable="false">
      <defs>
        <linearGradient id="aerion-cross-v" x1="0" y1="-500" x2="0" y2="500" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#14c8ff" stopOpacity="0" />
          <stop offset="0.3" stopColor="#14c8ff" stopOpacity="0.55" />
          <stop offset="0.5" stopColor="#bff2ff" stopOpacity="0.9" />
          <stop offset="0.7" stopColor="#14c8ff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#14c8ff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="aerion-cross-h" x1="-500" y1="0" x2="500" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#14c8ff" stopOpacity="0" />
          <stop offset="0.25" stopColor="#14c8ff" stopOpacity="0.4" />
          <stop offset="0.5" stopColor="#ffe3a8" stopOpacity="0.85" />
          <stop offset="0.75" stopColor="#14c8ff" stopOpacity="0.4" />
          <stop offset="1" stopColor="#14c8ff" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="aerion-ring-lens" cx="0" cy="0" r="500" gradientUnits="userSpaceOnUse">
          <stop offset="0.55" stopColor="#0a5c9c" stopOpacity="0" />
          <stop offset="0.8" stopColor="#0a5c9c" stopOpacity="0.12" />
          <stop offset="1" stopColor="#0a5c9c" stopOpacity="0" />
        </radialGradient>
      </defs>

      <circle r="496" fill="url(#aerion-ring-lens)" />

      <circle r="560" className="aerion-ring-far" />
      <circle r="612" className="aerion-ring-far-dash" />
      <circle r="680" className="aerion-ring-far" />
      <path d={FAR_TICKS} className="aerion-ring-far" />

      <path className="aerion-ring-cross" d="M0 -500V500" stroke="url(#aerion-cross-v)" />
      <path className="aerion-ring-cross" d="M-500 0H500" stroke="url(#aerion-cross-h)" />

      <g className="aerion-ring-spin-slow">
        <circle r="492" className="aerion-ring-hair" />
        <path d={OUTER_TICKS} className="aerion-ring-ticks" />
      </g>

      <g className="aerion-ring-spin-rev">
        <circle r="468" className="aerion-ring-seg" />
        <path d={GOLD_ARCS} className="aerion-ring-gold" />
      </g>

      <path d={MID_TICKS} className="aerion-ring-ticks aerion-ring-ticks-dim" />
      <circle r="440" className="aerion-ring-dots" />
      <path d={CYAN_ARCS} className="aerion-ring-cyan" />

      <g className="aerion-ring-spin-mid">
        <circle r="404" className="aerion-ring-hair" />
        <path d={INNER_TICKS} className="aerion-ring-ticks aerion-ring-ticks-dim" />
        <path d={BRACKETS} className="aerion-ring-bracket" />
      </g>
      {DIAMONDS.map(([x, y], i) => (
        <rect
          key={i}
          x={x - 4}
          y={y - 4}
          width="8"
          height="8"
          transform={`rotate(45 ${x} ${y})`}
          className="aerion-ring-diamond"
        />
      ))}

      <g className="aerion-ring-state" data-pattern={pattern}>
        {pattern === "double" ? (
          <>
            <circle r="384" strokeDasharray={undefined} />
            <circle r="392" strokeDasharray={undefined} />
          </>
        ) : (
          <circle r="388" strokeDasharray={PATTERN_DASH[pattern]} />
        )}
      </g>

      <path d={GOLD_DOT_ARCS} className="aerion-ring-gold-dots" />

      <g className="aerion-ring-spin-slow">
        <path d={SPOKES} className="aerion-ring-spokes" />
        <circle r="330" className="aerion-ring-hair aerion-ring-hair-dim" />
      </g>

      <g className="aerion-ring-satellite" transform={`translate(${sx.toFixed(1)} ${sy.toFixed(1)})`}>
        <circle r="32" />
        <circle r="24" className="aerion-ring-satellite-inner" />
        <path d="M-40 0H-34M34 0H40M0 -40V-34M0 34V40" />
      </g>
    </svg>
  );
});
