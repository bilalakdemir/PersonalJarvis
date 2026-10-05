/**
 * AERION energy core — Canvas 2D renderer.
 *
 * Pure presentation, no dependencies, no React. It draws the luminous
 * neural sphere, the blue/gold orbital rings, particles and bloom for one
 * `AerionVisualState`. It never reads or derives operational state: the
 * caller passes the already-derived visual state and this module only
 * decides how that state LOOKS.
 *
 * Performance budget: one 2D canvas (DPR capped), one half-resolution bloom
 * canvas blurred by the compositor, batched strokes, pre-rendered glow
 * sprites. The loop pauses when the canvas is off-screen or the document is
 * hidden, and `renderStatic()` draws a single frame for reduced motion.
 */
import type { AerionVisualState } from "@/lib/hudSemantics";

type RGB = readonly [number, number, number];

export interface CorePalette {
  /** Blue orbit / mesh hue. */
  blue: RGB;
  /** Gold orbit / spark hue. */
  gold: RGB;
  /** Inner core glow. */
  core: RGB;
  /** Ripples and travelling signals. */
  accent: RGB;
  /** Global brightness multiplier. */
  intensity: number;
  /** Motion multiplier. */
  speed: number;
  pulseHz: number;
  pulseAmp: number;
  /** 0..1 strength of expanding ripples from the core. */
  ripple: number;
  /** 0..1 density of signals travelling along the mesh. */
  signals: number;
  /** 0..1 erratic brightness dips (error). */
  flicker: number;
}

// Base hues are deliberately deep: additive stacking brightens them towards
// azure / amber on the hot parts instead of drifting to cyan / lemon.
const BLUE: RGB = [20, 98, 255];
const GOLD: RGB = [255, 118, 20];

export const CORE_PALETTES: Record<AerionVisualState, CorePalette> = {
  OFF: {
    blue: [58, 88, 108],
    gold: [112, 100, 82],
    core: [150, 160, 170],
    accent: [90, 110, 130],
    intensity: 0.3,
    speed: 0.12,
    pulseHz: 0.1,
    pulseAmp: 0.02,
    ripple: 0,
    signals: 0,
    flicker: 0,
  },
  STANDBY: {
    blue: BLUE,
    gold: GOLD,
    core: [255, 186, 104],
    accent: [90, 200, 255],
    intensity: 1,
    speed: 1,
    pulseHz: 0.22,
    pulseAmp: 0.05,
    ripple: 0,
    signals: 0.25,
    flicker: 0,
  },
  LISTENING: {
    blue: [20, 170, 255],
    gold: [255, 150, 50],
    core: [214, 248, 255],
    accent: [64, 240, 226],
    intensity: 1.08,
    speed: 1.3,
    pulseHz: 0.95,
    pulseAmp: 0.1,
    ripple: 1,
    signals: 0.3,
    flicker: 0,
  },
  THINKING: {
    blue: [50, 100, 255],
    gold: [255, 130, 30],
    core: [255, 214, 150],
    accent: [150, 205, 255],
    intensity: 1.05,
    speed: 1.8,
    pulseHz: 0.6,
    pulseAmp: 0.06,
    ripple: 0,
    signals: 1,
    flicker: 0,
  },
  WORKING: {
    blue: [24, 130, 255],
    gold: [255, 140, 36],
    core: [255, 220, 156],
    accent: [120, 220, 255],
    intensity: 1.08,
    speed: 2.2,
    pulseHz: 0.8,
    pulseAmp: 0.07,
    ripple: 0.3,
    signals: 0.75,
    flicker: 0,
  },
  WAITING_FOR_APPROVAL: {
    blue: [90, 120, 210],
    gold: [255, 136, 16],
    core: [255, 190, 90],
    accent: [255, 170, 50],
    intensity: 0.98,
    speed: 0.7,
    pulseHz: 0.35,
    pulseAmp: 0.12,
    ripple: 0.55,
    signals: 0.15,
    flicker: 0,
  },
  SPEAKING: {
    blue: [40, 150, 255],
    gold: [255, 160, 60],
    core: [255, 236, 200],
    accent: [255, 200, 110],
    intensity: 1.12,
    speed: 1.4,
    pulseHz: 1.6,
    pulseAmp: 0.14,
    ripple: 0.8,
    signals: 0.4,
    flicker: 0,
  },
  ERROR: {
    blue: [255, 40, 80],
    gold: [255, 80, 40],
    core: [255, 120, 108],
    accent: [255, 50, 84],
    intensity: 0.95,
    speed: 0.6,
    pulseHz: 1.2,
    pulseAmp: 0.1,
    ripple: 0.4,
    signals: 0.1,
    flicker: 0.65,
  },
  COMPLETED: {
    blue: [20, 210, 140],
    gold: [255, 180, 70],
    core: [212, 255, 232],
    accent: [62, 240, 172],
    intensity: 1.1,
    speed: 1.2,
    pulseHz: 0.8,
    pulseAmp: 0.08,
    ripple: 1,
    signals: 0.5,
    flicker: 0,
  },
};

// ----------------------------------------------------------------- utilities

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function rgba(c: RGB, a: number): string {
  const alpha = a <= 0 ? 0 : a >= 1 ? 1 : a;
  return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${alpha.toFixed(3)})`;
}

function mix(a: RGB, b: RGB, k: number): RGB {
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
}

function lerpPalette(from: CorePalette, to: CorePalette, k: number): CorePalette {
  const n = (a: number, b: number) => a + (b - a) * k;
  return {
    blue: mix(from.blue, to.blue, k),
    gold: mix(from.gold, to.gold, k),
    core: mix(from.core, to.core, k),
    accent: mix(from.accent, to.accent, k),
    intensity: n(from.intensity, to.intensity),
    speed: n(from.speed, to.speed),
    pulseHz: n(from.pulseHz, to.pulseHz),
    pulseAmp: n(from.pulseAmp, to.pulseAmp),
    ripple: n(from.ripple, to.ripple),
    signals: n(from.signals, to.signals),
    flicker: n(from.flicker, to.flicker),
  };
}

const WHITE: RGB = [255, 255, 255];
const ICE: RGB = [204, 234, 255];

type SpriteKey = "blue" | "gold" | "white" | "accent";

function makeSprite(color: RGB, size = 64): HTMLCanvasElement {
  const sprite = document.createElement("canvas");
  sprite.width = size;
  sprite.height = size;
  const g = sprite.getContext("2d");
  if (!g) return sprite;
  const h = size / 2;
  const grad = g.createRadialGradient(h, h, 0, h, h, h);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.12, rgba(mix(WHITE, color, 0.35), 0.95));
  grad.addColorStop(0.32, rgba(color, 0.42));
  grad.addColorStop(0.62, rgba(color, 0.1));
  grad.addColorStop(1, rgba(color, 0));
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  return sprite;
}

// ------------------------------------------------------------------ geometry

interface MeshNode {
  x: number;
  y: number;
  z: number;
  size: number;
  /** 0 blue, 1 ice-white, 2 gold */
  tone: 0 | 1 | 2;
  bright: boolean;
  star: boolean;
  hero: boolean;
  phase: number;
}

interface Orbit {
  rx: number;
  ry: number;
  rot: number;
  width: number;
  tone: "blue" | "gold";
  alpha: number;
  precess: number;
  phase: number;
  hl: number;
  sparks: number;
}

interface Dust {
  r: number;
  a: number;
  w: number;
  size: number;
  tone: 0 | 1 | 2;
  phase: number;
  bright: boolean;
}

interface Signal {
  edge: number;
  t: number;
  speed: number;
  forward: boolean;
}

const SHELL_NODES = 190;
const INNER_NODES = 130;
const DUST_COUNT = 720;
const RAY_COUNT = 72;

/** Orbit set in sphere-radius units (rotation in radians, screen space). */
const ORBITS: readonly Orbit[] = [
  {
    rx: 1.27,
    ry: 0.45,
    rot: 0.42,
    width: 8.6,
    tone: "gold",
    alpha: 1,
    precess: 0.012,
    phase: 0.2,
    hl: 0.34,
    sparks: 9,
  },
  {
    rx: 1.23,
    ry: 0.4,
    rot: -0.46,
    width: 9.4,
    tone: "blue",
    alpha: 1,
    precess: -0.009,
    phase: 2.1,
    hl: -0.3,
    sparks: 9,
  },
  {
    rx: 1.16,
    ry: 0.42,
    rot: -1.24,
    width: 7.2,
    tone: "blue",
    alpha: 0.95,
    precess: 0.007,
    phase: 4.2,
    hl: 0.26,
    sparks: 6,
  },
  {
    rx: 1.12,
    ry: 0.3,
    rot: 0.16,
    width: 5.4,
    tone: "gold",
    alpha: 0.9,
    precess: -0.006,
    phase: 1.1,
    hl: -0.22,
    sparks: 6,
  },
  {
    rx: 1.03,
    ry: 0.72,
    rot: 0.78,
    width: 2.2,
    tone: "blue",
    alpha: 0.7,
    precess: 0.01,
    phase: 3.3,
    hl: 0.4,
    sparks: 4,
  },
  {
    rx: 0.97,
    ry: 0.58,
    rot: -0.95,
    width: 2,
    tone: "gold",
    alpha: 0.65,
    precess: -0.012,
    phase: 5.1,
    hl: -0.36,
    sparks: 4,
  },
  {
    rx: 1.42,
    ry: 0.6,
    rot: 0.04,
    width: 1.6,
    tone: "blue",
    alpha: 0.4,
    precess: 0.004,
    phase: 0.7,
    hl: 0.18,
    sparks: 3,
  },
];

function buildMesh(rand: () => number) {
  const nodes: MeshNode[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < SHELL_NODES; i += 1) {
    const y = 1 - (2 * (i + 0.5)) / SHELL_NODES;
    const ring = Math.sqrt(1 - y * y);
    const theta = golden * i + (rand() - 0.5) * 0.35;
    const r = 0.9 + rand() * 0.1;
    const roll = rand();
    nodes.push({
      x: Math.cos(theta) * ring * r,
      y: y * r,
      z: Math.sin(theta) * ring * r,
      size: 0.95 + rand() * 1.45,
      tone: roll < 0.2 ? 2 : roll < 0.58 ? 1 : 0,
      bright: rand() < 0.4,
      star: rand() < 0.2,
      hero: false,
      phase: rand() * Math.PI * 2,
    });
  }
  for (let i = 0; i < INNER_NODES; i += 1) {
    const u = rand() * 2 - 1;
    const a = rand() * Math.PI * 2;
    const ring = Math.sqrt(1 - u * u);
    const r = 0.1 + 0.78 * Math.sqrt(rand());
    const roll = rand();
    nodes.push({
      x: Math.cos(a) * ring * r,
      y: u * r,
      z: Math.sin(a) * ring * r,
      size: 0.8 + rand() * 1.2,
      tone: r < 0.55 ? (roll < 0.65 ? 2 : 1) : roll < 0.3 ? 2 : roll < 0.62 ? 1 : 0,
      bright: rand() < 0.36,
      star: rand() < 0.16,
      hero: r < 0.75 && rand() < 0.11,
      phase: rand() * Math.PI * 2,
    });
  }

  // Short links: k nearest neighbours in 3D.
  const edges: Array<[number, number]> = [];
  const seen = new Set<number>();
  const n = nodes.length;
  const addEdge = (a: number, b: number) => {
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    const key = lo * 4096 + hi;
    if (lo === hi || seen.has(key)) return;
    seen.add(key);
    edges.push([lo, hi]);
  };
  for (let i = 0; i < n; i += 1) {
    const a = nodes[i] as MeshNode;
    const near: Array<{ j: number; d: number }> = [];
    for (let j = 0; j < n; j += 1) {
      if (j === i) continue;
      const b = nodes[j] as MeshNode;
      const d = (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
      near.push({ j, d });
    }
    near.sort((p, q) => p.d - q.d);
    const k = i < SHELL_NODES ? 3 : 2;
    for (let m = 0; m < k; m += 1) {
      const hit = near[m];
      if (hit) addEdge(i, hit.j);
    }
  }
  const shortCount = edges.length;
  // Long chords across the shell: the geodesic "web" look.
  for (let c = 0; c < 230; c += 1) {
    const a = Math.floor(rand() * SHELL_NODES);
    const b = Math.floor(rand() * SHELL_NODES);
    const na = nodes[a] as MeshNode;
    const nb = nodes[b] as MeshNode;
    const d = Math.sqrt((na.x - nb.x) ** 2 + (na.y - nb.y) ** 2 + (na.z - nb.z) ** 2);
    if (d > 0.45 && d < 1.9) addEdge(a, b);
  }
  // Spokes: core → node.
  const spokes: number[] = [];
  for (let s = 0; s < 46; s += 1) spokes.push(Math.floor(rand() * n));

  // Adjacency for chained signals.
  const adjacency: number[][] = Array.from({ length: n }, () => []);
  edges.forEach(([a, b], index) => {
    adjacency[a]?.push(index);
    adjacency[b]?.push(index);
  });

  return { nodes, edges, shortCount, spokes, adjacency };
}

function buildDust(rand: () => number): Dust[] {
  const dust: Dust[] = [];
  for (let i = 0; i < DUST_COUNT; i += 1) {
    const band = rand() < 0.6;
    const r = band ? 0.95 + rand() * 0.55 : 0.35 + Math.pow(rand(), 0.9) * 1.3;
    const roll = rand();
    dust.push({
      r,
      a: rand() * Math.PI * 2,
      w: (0.018 + rand() * 0.05) * (r < 0.9 ? 1.6 : 1) * (rand() < 0.5 ? 1 : -1),
      size: 0.8 + rand() * 1.7,
      tone: roll < 0.56 ? 2 : roll < 0.9 ? 0 : 1,
      phase: rand() * Math.PI * 2,
      bright: rand() < 0.24,
    });
  }
  return dust;
}

function buildRays(rand: () => number) {
  return Array.from({ length: RAY_COUNT }, () => ({
    a: rand() * Math.PI * 2,
    len: 0.22 + Math.pow(rand(), 2.2) * 0.72,
    w: 0.4 + rand() * 0.9,
    gold: rand() < 0.62,
  }));
}

function buildCorona(rand: () => number) {
  const count = 150;
  return Array.from({ length: count }, (_, i) => ({
    a: (i / count) * Math.PI * 2 + rand() * 0.04,
    len: 0.17 + Math.pow(rand(), 1.6) * 0.27,
  }));
}

// ------------------------------------------------------------------ renderer

export interface CoreRendererOptions {
  /** Upper bound for devicePixelRatio (performance). */
  maxDpr?: number;
  /** Frame cap. */
  maxFps?: number;
}

export class CoreRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly bloom: HTMLCanvasElement | null;
  private readonly bloomCtx: CanvasRenderingContext2D | null;
  private maxDpr: number;
  private minFrameMs: number;

  private readonly mesh = buildMesh(mulberry32(0xae1e0));
  private readonly dust = buildDust(mulberry32(0x5eed5));
  private readonly rays = buildRays(mulberry32(0xc0de));
  private readonly corona = buildCorona(mulberry32(0xc0a0));
  private readonly signals: Signal[] = [];
  private readonly rand = mulberry32(0x51a1);

  private px = new Float32Array(0);
  private py = new Float32Array(0);
  private pd = new Float32Array(0);

  private sprites: Record<SpriteKey, HTMLCanvasElement> | null = null;
  private target: CorePalette = CORE_PALETTES.STANDBY;
  private palette: CorePalette = CORE_PALETTES.STANDBY;
  private spriteFor: CorePalette | null = null;

  private size = 0;
  private dpr = 1;
  private raf = 0;
  private running = false;
  private lastTs = 0;
  private lastPaint = 0;
  private clock = 0;
  private spin = 0;
  private orbitClock = 0;
  private flickerLevel = 1;
  private cssSize = 0;
  private quality: "high" | "low" | "min" = "high";
  private costEma = 0;
  private framesMeasured = 0;
  private maxFrameMs: number;

  constructor(canvas: HTMLCanvasElement, bloom: HTMLCanvasElement | null, opts: CoreRendererOptions = {}) {
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) throw new Error("2d canvas unavailable");
    this.canvas = canvas;
    this.ctx = ctx;
    this.bloom = bloom;
    this.bloomCtx = bloom ? bloom.getContext("2d", { alpha: true }) : null;
    this.maxDpr = opts.maxDpr ?? 1.75;
    this.minFrameMs = 1000 / (opts.maxFps ?? 60) - 1;
    this.maxFrameMs = this.minFrameMs;
    const count = this.mesh.nodes.length;
    this.px = new Float32Array(count);
    this.py = new Float32Array(count);
    this.pd = new Float32Array(count);
  }

  /** Set the visual state; colours/motion ease towards it. */
  setState(state: AerionVisualState, immediate = false): void {
    this.target = CORE_PALETTES[state] ?? CORE_PALETTES.STANDBY;
    if (immediate) this.palette = this.target;
  }

  /** CSS pixel size of the (square) drawing area. */
  resize(cssSize: number): void {
    this.cssSize = cssSize;
    const dpr = Math.min(window.devicePixelRatio || 1, this.maxDpr);
    const size = Math.max(1, Math.round(cssSize));
    if (size === this.size && dpr === this.dpr) return;
    this.size = size;
    this.dpr = dpr;
    this.canvas.width = Math.round(size * dpr);
    this.canvas.height = Math.round(size * dpr);
    if (this.bloom) {
      const b = Math.max(1, Math.round(size * 0.5));
      this.bloom.width = b;
      this.bloom.height = b;
    }
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.lastTs = 0;
    const loop = (ts: number) => {
      if (!this.running) return;
      this.raf = window.requestAnimationFrame(loop);
      if (this.lastTs && ts - this.lastPaint < this.minFrameMs) return;
      const dt = this.lastTs ? Math.min(0.1, (ts - this.lastTs) / 1000) : 0.016;
      this.lastTs = ts;
      this.lastPaint = ts;
      this.frame(dt);
    };
    this.raf = window.requestAnimationFrame(loop);
  }

  stop(): void {
    this.running = false;
    if (this.raf) window.cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  /** Draw one deterministic, composed frame (reduced motion / static). */
  renderStatic(): void {
    this.palette = this.target;
    this.clock = 8.4;
    this.spin = 0.62;
    this.orbitClock = 8.4;
    this.signals.length = 0;
    this.flickerLevel = 1;
    this.draw(0, true);
  }

  destroy(): void {
    this.stop();
    this.sprites = null;
  }

  // ---------------------------------------------------------------- internal

  private frame(dt: number): void {
    const k = 1 - Math.exp(-dt * 3.2);
    this.palette = lerpPalette(this.palette, this.target, k);
    this.clock += dt;
    this.spin += dt * 0.075 * this.palette.speed;
    this.orbitClock += dt * this.palette.speed;
    this.updateSignals(dt);
    const t0 = performance.now();
    if (this.palette.flicker > 0.01) {
      if (this.rand() < 0.06 * this.palette.flicker) this.flickerLevel = 0.45 + this.rand() * 0.3;
      this.flickerLevel += (1 - this.flickerLevel) * Math.min(1, dt * 7);
    } else {
      this.flickerLevel = 1;
    }
    this.draw(dt, false);
    this.adapt(performance.now() - t0);
  }

  /** Step quality down on slow machines (software raster, weak iGPU). */
  private adapt(cost: number): void {
    this.framesMeasured += 1;
    this.costEma = this.framesMeasured === 1 ? cost : this.costEma * 0.92 + cost * 0.08;
    if (this.framesMeasured < 45) return;
    if (this.quality === "high" && this.costEma > 11) {
      this.quality = "low";
      this.maxDpr = 1;
      this.minFrameMs = Math.max(this.maxFrameMs, 1000 / 30 - 1);
      this.size = 0;
      this.resize(this.cssSize);
      this.draw(0, false);
      this.framesMeasured = 0;
    } else if (this.quality === "low" && this.costEma > 24) {
      this.quality = "min";
      this.minFrameMs = 1000 / 20 - 1;
      this.framesMeasured = 0;
    }
  }

  private ensureSprites(): Record<SpriteKey, HTMLCanvasElement> {
    if (!this.sprites || this.spriteFor !== this.target) {
      this.sprites = {
        blue: makeSprite(this.target.blue),
        gold: makeSprite(this.target.gold),
        white: makeSprite(ICE),
        accent: makeSprite(this.target.accent),
      };
      this.spriteFor = this.target;
    }
    return this.sprites;
  }

  private updateSignals(dt: number): void {
    const { edges, adjacency } = this.mesh;
    const want = Math.round(34 * this.palette.signals);
    while (this.signals.length < want) {
      this.signals.push({
        edge: Math.floor(this.rand() * edges.length),
        t: 0,
        speed: 0.9 + this.rand() * 1.4,
        forward: this.rand() < 0.5,
      });
    }
    if (this.signals.length > want) this.signals.length = want;
    for (const s of this.signals) {
      s.t += dt * s.speed * (0.6 + this.palette.speed * 0.4);
      if (s.t >= 1) {
        const edge = edges[s.edge];
        const end = edge ? (s.forward ? edge[1] : edge[0]) : 0;
        const next = adjacency[end] ?? [];
        const pick = next.length ? next[Math.floor(this.rand() * next.length)] : undefined;
        s.edge = pick ?? Math.floor(this.rand() * edges.length);
        const e2 = edges[s.edge];
        s.forward = e2 ? e2[0] === end : true;
        s.t = 0;
      }
    }
  }

  private draw(_dt: number, still: boolean): void {
    const ctx = this.ctx;
    const S = this.size;
    if (S <= 1) return;
    const sprites = this.ensureSprites();
    const pal = this.palette;
    const c = S / 2;
    const R = S * 0.31;
    const t = this.clock;
    const I = Math.max(0, pal.intensity * this.flickerLevel);
    const pulse = 1 + pal.pulseAmp * Math.sin(t * Math.PI * 2 * pal.pulseHz);

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, S, S);
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";

    // 1 — haze behind everything
    ctx.globalAlpha = I;
    // glass-sphere rim (the wide haze lives in CSS so the bloom stays crisp)
    const rim = ctx.createRadialGradient(c, c, R * 0.62, c, c, R * 1.05);
    rim.addColorStop(0, rgba(pal.blue, 0));
    rim.addColorStop(0.75, rgba(pal.blue, 0.06));
    rim.addColorStop(0.92, rgba(pal.blue, 0.2));
    rim.addColorStop(1, rgba(pal.blue, 0));
    ctx.fillStyle = rim;
    ctx.beginPath();
    ctx.arc(c, c, R * 1.05, 0, Math.PI * 2);
    ctx.fill();

    // 2 — dust field
    this.drawDust(c, R, t, I, sprites);

    // 3 — orbits, back pass (full ellipse, dimmed)
    for (const orbit of ORBITS) this.drawOrbit(orbit, c, R, I * 0.78, false);

    // 4 — mesh
    this.projectMesh(c, R);
    this.drawMesh(c, R, I, t, sprites);

    // 5 — travelling signals
    if (!still) this.drawSignals(I, sprites);

    // 6 — core
    this.drawCore(c, R, I, t, pulse, sprites);

    // 7 — orbits, front pass + sparks
    for (const orbit of ORBITS) this.drawOrbit(orbit, c, R, I, true);
    for (const orbit of ORBITS) this.drawOrbitSparks(orbit, c, R, I, sprites);

    // 8 — ripples
    if (pal.ripple > 0.02) this.drawRipples(c, R, I, t, still);

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";

    // 9 — bloom source (blurred by CSS on the compositor)
    if (this.bloom && this.bloomCtx) {
      const b = this.bloomCtx;
      b.globalCompositeOperation = "source-over";
      b.clearRect(0, 0, this.bloom.width, this.bloom.height);
      b.drawImage(this.canvas, 0, 0, this.bloom.width, this.bloom.height);
    }
  }

  private drawDust(
    c: number,
    R: number,
    t: number,
    I: number,
    sprites: Record<SpriteKey, HTMLCanvasElement>,
  ): void {
    const ctx = this.ctx;
    const pal = this.palette;
    const spin = this.orbitClock;
    const colors: RGB[] = [pal.blue, ICE, pal.gold];
    for (let tone = 0; tone < 3; tone += 1) {
      ctx.fillStyle = rgba(colors[tone] as RGB, 1);
      for (let di = 0; di < this.dust.length; di += this.quality === "high" ? 1 : 2) {
        const d = this.dust[di] as Dust;
        if (d.tone !== tone) continue;
        const a = d.a + spin * d.w;
        const x = c + Math.cos(a) * d.r * R;
        const y = c + Math.sin(a) * d.r * R * 0.84;
        const tw = 0.45 + 0.55 * Math.abs(Math.sin(t * 0.9 + d.phase));
        ctx.globalAlpha = I * tw * (d.r > 1.45 ? 0.6 : 0.95);
        const s = d.size;
        ctx.fillRect(x - s / 2, y - s / 2, s, s);
      }
    }
    for (const d of this.dust) {
      if (!d.bright) continue;
      const a = d.a + spin * d.w;
      const x = c + Math.cos(a) * d.r * R;
      const y = c + Math.sin(a) * d.r * R * 0.84;
      const tw = 0.4 + 0.6 * Math.abs(Math.sin(t * 1.3 + d.phase));
      const g = 9 + d.size * 6;
      ctx.globalAlpha = I * tw;
      ctx.drawImage(
        d.tone === 2 ? sprites.gold : d.tone === 1 ? sprites.white : sprites.blue,
        x - g / 2,
        y - g / 2,
        g,
        g,
      );
    }
  }

  private projectMesh(c: number, R: number): void {
    const nodes = this.mesh.nodes;
    const ay = this.spin;
    const ax = 0.34 + Math.sin(this.clock * 0.11) * 0.06;
    const cy = Math.cos(ay);
    const sy = Math.sin(ay);
    const cx = Math.cos(ax);
    const sx = Math.sin(ax);
    const f = 3.4;
    for (let i = 0; i < nodes.length; i += 1) {
      const n = nodes[i] as MeshNode;
      const x1 = n.x * cy + n.z * sy;
      const z1 = -n.x * sy + n.z * cy;
      const y2 = n.y * cx - z1 * sx;
      const z2 = n.y * sx + z1 * cx;
      const s = f / (f - z2);
      this.px[i] = c + x1 * s * R;
      this.py[i] = c + y2 * s * R;
      this.pd[i] = Math.max(0, Math.min(1, (z2 + 1) / 2));
    }
  }

  private drawMesh(
    c: number,
    R: number,
    I: number,
    t: number,
    sprites: Record<SpriteKey, HTMLCanvasElement>,
  ): void {
    const ctx = this.ctx;
    const pal = this.palette;
    const { edges, shortCount, spokes, nodes } = this.mesh;
    const px = this.px;
    const py = this.py;
    const pd = this.pd;
    const lineColor = mix(pal.blue, ICE, 0.5);

    const buckets: Array<{ max: number; alpha: number; width: number }> = [
      { max: 0.38, alpha: 0.28, width: 0.65 },
      { max: 0.66, alpha: 0.5, width: 0.9 },
      { max: 1.01, alpha: 0.82, width: 1.2 },
    ];
    let lo = -1;
    for (const bucket of buckets) {
      for (let pass = 0; pass < 2; pass += 1) {
        const chords = pass === 1;
        ctx.beginPath();
        const from = chords ? shortCount : 0;
        const to = chords ? edges.length : shortCount;
        for (let e = from; e < to; e += 1) {
          const edge = edges[e] as [number, number];
          const a = edge[0];
          const b = edge[1];
          const depth = ((pd[a] as number) + (pd[b] as number)) / 2;
          if (depth <= lo || depth > bucket.max) continue;
          ctx.moveTo(px[a] as number, py[a] as number);
          ctx.lineTo(px[b] as number, py[b] as number);
        }
        ctx.globalAlpha = I * bucket.alpha * (chords ? 0.75 : 1);
        ctx.strokeStyle = rgba(lineColor, 1);
        ctx.lineWidth = bucket.width * (chords ? 0.7 : 1);
        ctx.stroke();
      }
      lo = bucket.max;
    }

    const spokeGrad = ctx.createRadialGradient(c, c, 0, c, c, R * 1.05);
    spokeGrad.addColorStop(0, rgba(WHITE, 0.85));
    spokeGrad.addColorStop(0.22, rgba(pal.gold, 0.55));
    spokeGrad.addColorStop(0.7, rgba(pal.gold, 0.12));
    spokeGrad.addColorStop(1, rgba(pal.gold, 0));
    ctx.beginPath();
    for (const idx of spokes) {
      ctx.moveTo(c, c);
      ctx.lineTo(px[idx] as number, py[idx] as number);
    }
    ctx.globalAlpha = I * 0.75;
    ctx.strokeStyle = spokeGrad;
    ctx.lineWidth = 0.8;
    ctx.stroke();

    const tones: RGB[] = [mix(pal.blue, ICE, 0.25), ICE, pal.gold];
    for (let tone = 0; tone < 3; tone += 1) {
      ctx.fillStyle = rgba(tones[tone] as RGB, 1);
      for (let i = 0; i < nodes.length; i += 1) {
        const n = nodes[i] as MeshNode;
        if (n.tone !== tone) continue;
        const depth = pd[i] as number;
        const r = n.size * (0.6 + depth * 0.9);
        ctx.globalAlpha = I * (0.5 + depth * 0.5);
        ctx.beginPath();
        ctx.arc(px[i] as number, py[i] as number, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    for (let i = 0; i < nodes.length; i += 1) {
      const n = nodes[i] as MeshNode;
      if (!n.hero) continue;
      const depth = pd[i] as number;
      const g = (26 + n.size * 12) * (0.7 + depth * 0.5);
      ctx.globalAlpha = I * (0.55 + depth * 0.45) * (0.75 + 0.25 * Math.sin(t * 1.1 + n.phase));
      ctx.drawImage(sprites.gold, (px[i] as number) - g / 2, (py[i] as number) - g / 2, g, g);
    }

    for (let i = 0; i < nodes.length; i += 1) {
      const n = nodes[i] as MeshNode;
      if (!n.bright && !n.star) continue;
      const depth = pd[i] as number;
      const tw = 0.55 + 0.45 * Math.sin(t * 1.7 + n.phase);
      const g = (12 + n.size * 9) * (0.6 + depth * 0.7);
      const x = px[i] as number;
      const y = py[i] as number;
      ctx.globalAlpha = I * (0.35 + depth * 0.65) * tw;
      ctx.drawImage(
        n.tone === 2 ? sprites.gold : n.tone === 1 ? sprites.white : sprites.blue,
        x - g / 2,
        y - g / 2,
        g,
        g,
      );
      if (n.star && depth > 0.35) {
        const L = g * 1.1;
        ctx.globalAlpha = I * (0.3 + depth * 0.7) * tw * 0.85;
        ctx.strokeStyle = rgba(n.tone === 2 ? pal.gold : ICE, 1);
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        ctx.moveTo(x - L, y);
        ctx.lineTo(x + L, y);
        ctx.moveTo(x, y - L);
        ctx.lineTo(x, y + L);
        ctx.stroke();
      }
    }
  }

  private drawSignals(I: number, sprites: Record<SpriteKey, HTMLCanvasElement>): void {
    if (!this.signals.length) return;
    const ctx = this.ctx;
    const { edges } = this.mesh;
    for (const s of this.signals) {
      const edge = edges[s.edge];
      if (!edge) continue;
      const a = s.forward ? edge[0] : edge[1];
      const b = s.forward ? edge[1] : edge[0];
      const x = (this.px[a] as number) + ((this.px[b] as number) - (this.px[a] as number)) * s.t;
      const y = (this.py[a] as number) + ((this.py[b] as number) - (this.py[a] as number)) * s.t;
      const depth = ((this.pd[a] as number) + (this.pd[b] as number)) / 2;
      const g = 10 + depth * 8;
      ctx.globalAlpha = I * (0.45 + depth * 0.55) * Math.sin(Math.PI * s.t);
      ctx.drawImage(sprites.accent, x - g / 2, y - g / 2, g, g);
    }
  }

  private drawCore(
    c: number,
    R: number,
    I: number,
    t: number,
    pulse: number,
    sprites: Record<SpriteKey, HTMLCanvasElement>,
  ): void {
    const ctx = this.ctx;
    const pal = this.palette;

    const rayGrad = ctx.createRadialGradient(c, c, 0, c, c, R * 0.98);
    rayGrad.addColorStop(0, rgba(WHITE, 0.95));
    rayGrad.addColorStop(0.2, rgba(mix(pal.core, pal.gold, 0.4), 0.6));
    rayGrad.addColorStop(0.55, rgba(pal.gold, 0.16));
    rayGrad.addColorStop(1, rgba(pal.gold, 0));
    const rot = t * 0.035;
    ctx.beginPath();
    for (const ray of this.rays) {
      const a = ray.a + rot;
      const len = ray.len * R * (0.92 + 0.08 * pulse);
      ctx.moveTo(c + Math.cos(a) * R * 0.05, c + Math.sin(a) * R * 0.05);
      ctx.lineTo(c + Math.cos(a) * len, c + Math.sin(a) * len);
    }
    ctx.globalAlpha = I * 0.85;
    ctx.strokeStyle = rayGrad;
    ctx.lineWidth = 0.9;
    ctx.stroke();

    const corona = ctx.createRadialGradient(c, c, R * 0.08, c, c, R * 0.46);
    corona.addColorStop(0, rgba(mix(pal.core, WHITE, 0.4), 0.9));
    corona.addColorStop(0.45, rgba(pal.gold, 0.55));
    corona.addColorStop(1, rgba(pal.gold, 0));
    ctx.beginPath();
    for (const ray of this.corona) {
      const a = ray.a - rot * 1.6;
      ctx.moveTo(c + Math.cos(a) * R * 0.09, c + Math.sin(a) * R * 0.09);
      ctx.lineTo(c + Math.cos(a) * ray.len * R, c + Math.sin(a) * ray.len * R);
    }
    ctx.globalAlpha = I * 0.9;
    ctx.strokeStyle = corona;
    ctx.lineWidth = 0.75;
    ctx.stroke();

    const outer = ctx.createRadialGradient(c, c, 0, c, c, R * 0.8 * pulse);
    outer.addColorStop(0, rgba(pal.core, 0.9));
    outer.addColorStop(0.14, rgba(pal.gold, 0.55));
    outer.addColorStop(0.4, rgba(pal.gold, 0.15));
    outer.addColorStop(1, rgba(pal.gold, 0));
    ctx.globalAlpha = I;
    ctx.fillStyle = outer;
    ctx.beginPath();
    ctx.arc(c, c, R * 0.8 * pulse, 0, Math.PI * 2);
    ctx.fill();

    const hotR = R * 0.24 * pulse;
    const hot = ctx.createRadialGradient(c, c, 0, c, c, hotR);
    hot.addColorStop(0, rgba(WHITE, 1));
    hot.addColorStop(0.3, rgba(mix(WHITE, pal.core, 0.3), 0.95));
    hot.addColorStop(0.65, rgba(pal.core, 0.45));
    hot.addColorStop(1, rgba(pal.core, 0));
    ctx.fillStyle = hot;
    ctx.beginPath();
    ctx.arc(c, c, hotR, 0, Math.PI * 2);
    ctx.fill();

    ctx.globalAlpha = I * 0.85;
    ctx.save();
    ctx.translate(c, c);
    ctx.scale(1, 0.03);
    const streak = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 1.05);
    streak.addColorStop(0, rgba(WHITE, 0.9));
    streak.addColorStop(0.3, rgba(pal.core, 0.35));
    streak.addColorStop(1, rgba(pal.core, 0));
    ctx.fillStyle = streak;
    ctx.beginPath();
    ctx.arc(0, 0, R * 1.05, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    ctx.save();
    ctx.translate(c, c);
    ctx.scale(0.028, 1);
    ctx.globalAlpha = I * 0.5;
    ctx.fillStyle = streak;
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    const g = R * 0.32 * pulse;
    ctx.globalAlpha = I * 0.9;
    ctx.drawImage(sprites.white, c - g / 2, c - g / 2, g, g);
  }

  private orbitGeometry(orbit: Orbit, R: number) {
    const tt = this.orbitClock;
    const rot = orbit.rot + tt * orbit.precess;
    const ry = orbit.ry * (1 + 0.07 * Math.sin(tt * 0.21 + orbit.phase)) * R;
    const rx = orbit.rx * R;
    return { rot, rx, ry };
  }

  private orbitStroke(orbit: Orbit, c: number, alpha: number): CanvasGradient | string {
    const ctx = this.ctx;
    const pal = this.palette;
    const base = orbit.tone === "gold" ? pal.gold : pal.blue;
    const hi = mix(base, WHITE, orbit.tone === "gold" ? 0.3 : 0.45);
    const start = orbit.phase + this.orbitClock * orbit.hl;
    const conic = (
      ctx as CanvasRenderingContext2D & {
        createConicGradient?: (a: number, x: number, y: number) => CanvasGradient;
      }
    ).createConicGradient;
    if (typeof conic === "function") {
      const g = conic.call(ctx, start, c, c);
      g.addColorStop(0, rgba(base, 0.5 * alpha));
      g.addColorStop(0.12, rgba(base, 0.85 * alpha));
      g.addColorStop(0.2, rgba(hi, 1 * alpha));
      g.addColorStop(0.3, rgba(base, 0.9 * alpha));
      g.addColorStop(0.48, rgba(base, 0.52 * alpha));
      g.addColorStop(0.64, rgba(base, 0.7 * alpha));
      g.addColorStop(0.72, rgba(hi, 0.95 * alpha));
      g.addColorStop(0.82, rgba(base, 0.6 * alpha));
      g.addColorStop(1, rgba(base, 0.5 * alpha));
      return g;
    }
    return rgba(base, 0.7 * alpha);
  }

  private drawOrbit(orbit: Orbit, c: number, R: number, I: number, front: boolean): void {
    const ctx = this.ctx;
    const { rot, rx, ry } = this.orbitGeometry(orbit, R);
    const stroke = this.orbitStroke(orbit, c, orbit.alpha);
    const passes: Array<[number, number]> = front
      ? [
          [orbit.width * 3.6, 0.08],
          [orbit.width * 1.9, 0.24],
          [orbit.width * 0.95, 0.62],
          [orbit.width * 0.42, 0.9],
        ]
      : [
          [orbit.width * 2.8, 0.06],
          [orbit.width * 1.3, 0.24],
          [orbit.width * 0.55, 0.6],
        ];
    ctx.strokeStyle = stroke;
    for (const [w, a] of passes) {
      ctx.globalAlpha = I * a;
      ctx.lineWidth = w;
      ctx.beginPath();
      if (front) ctx.ellipse(c, c, rx, ry, rot, 0.02, Math.PI - 0.02);
      else ctx.ellipse(c, c, rx, ry, rot, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (front) {
      ctx.globalAlpha = I * 0.4 * orbit.alpha;
      ctx.strokeStyle = rgba(
        mix(orbit.tone === "gold" ? this.palette.gold : this.palette.blue, WHITE, 0.7),
        0.9,
      );
      ctx.lineWidth = Math.max(0.6, orbit.width * 0.22);
      ctx.beginPath();
      ctx.ellipse(c, c, rx, ry, rot, 0.35, Math.PI - 0.35);
      ctx.stroke();
    }
  }

  private drawOrbitSparks(
    orbit: Orbit,
    c: number,
    R: number,
    I: number,
    sprites: Record<SpriteKey, HTMLCanvasElement>,
  ): void {
    const ctx = this.ctx;
    const { rot, rx, ry } = this.orbitGeometry(orbit, R);
    const cr = Math.cos(rot);
    const sr = Math.sin(rot);
    const sprite = orbit.tone === "gold" ? sprites.gold : sprites.blue;
    for (let k = 0; k < orbit.sparks; k += 1) {
      const u =
        orbit.phase * 3 + (k / orbit.sparks) * Math.PI * 2 + this.orbitClock * 0.22 * (orbit.hl > 0 ? 1 : -1);
      const ex = Math.cos(u) * rx;
      const ey = Math.sin(u) * ry;
      const x = c + ex * cr - ey * sr;
      const y = c + ex * sr + ey * cr;
      const frontness = Math.sin(u) > 0 ? 1 : 0.45;
      const g = (k % 3 === 0 ? 22 : 13) * (orbit.width / 6 + 0.55);
      ctx.globalAlpha = I * orbit.alpha * frontness * (0.6 + 0.4 * Math.sin(this.clock * 2 + k));
      ctx.drawImage(k % 4 === 0 ? sprites.white : sprite, x - g / 2, y - g / 2, g, g);
    }
  }

  private drawRipples(c: number, R: number, I: number, t: number, still: boolean): void {
    const ctx = this.ctx;
    const pal = this.palette;
    const period = 2.4 / Math.max(0.4, pal.speed * 0.7);
    const count = 3;
    for (let i = 0; i < count; i += 1) {
      const p = still ? (i + 0.5) / count : (t / period + i / count) % 1;
      const r = R * (0.3 + p * 1.15);
      ctx.globalAlpha = I * pal.ripple * (1 - p) * 0.55;
      ctx.strokeStyle = rgba(pal.accent, 1);
      ctx.lineWidth = 1.4 + (1 - p) * 1.6;
      ctx.beginPath();
      ctx.arc(c, c, r, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

/** True where a real 2D canvas exists (false in jsdom / SSR). */
export function canRenderCore(): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) return false;
  return typeof HTMLCanvasElement !== "undefined";
}
