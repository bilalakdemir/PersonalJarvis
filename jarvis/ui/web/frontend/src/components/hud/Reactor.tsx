/**
 * AERION Energy Core — cinematic presentation of canonical HUD state.
 *
 * Operational truth comes from HudSnapshot only. `state` is the canonical
 * primary state (drives the label, tone and ring pattern); `visualState` is
 * the presentation-only derivative from `aerionVisualState()` (drives colour
 * and motion of the energy field). Nothing here feeds back into Jarvis.
 *
 * Layers (back → front): aura (CSS) · technical rings (SVG) · energy field
 * (Canvas 2D) · bloom (half-res canvas blurred by the compositor).
 * Reduced motion renders one static frame and stops all CSS animation.
 */
import { memo, useEffect, useRef, useState } from "react";

import { CoreRings } from "@/components/hud/CoreRings";
import { CoreRenderer, canRenderCore } from "@/components/hud/coreRenderer";
import { REACTOR_STYLES, type AerionVisualState } from "@/lib/hudSemantics";
import type { HudPrimaryState } from "@/types/hud";

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(REDUCED_MOTION_QUERY).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    const onChange = () => setReduced(query.matches);
    onChange();
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

function EnergyField({ visualState, reduced }: { visualState: AerionVisualState; reduced: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bloomRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<CoreRenderer | null>(null);
  const stateRef = useRef(visualState);
  const [available, setAvailable] = useState(true);

  // Lifecycle: one renderer per mount / motion preference.
  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas || !canRenderCore()) {
      setAvailable(false);
      return;
    }
    let renderer: CoreRenderer;
    try {
      renderer = new CoreRenderer(canvas, bloomRef.current);
    } catch {
      setAvailable(false);
      return;
    }
    rendererRef.current = renderer;
    renderer.setState(stateRef.current, true);

    let visible = true;
    let onScreen = true;
    const sync = () => {
      if (reduced) {
        renderer.stop();
        renderer.renderStatic();
      } else if (visible && onScreen) {
        renderer.start();
      } else {
        renderer.stop();
      }
    };

    const measure = () => {
      renderer.resize(host.clientWidth);
      if (reduced) renderer.renderStatic();
    };
    measure();

    const resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    resizeObserver?.observe(host);
    const onWindowResize = () => measure();
    if (!resizeObserver) window.addEventListener("resize", onWindowResize);

    const intersection =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver((entries) => {
            onScreen = entries.some((entry) => entry.isIntersecting);
            sync();
          })
        : null;
    intersection?.observe(host);

    const onVisibility = () => {
      visible = document.visibilityState !== "hidden";
      sync();
    };
    document.addEventListener("visibilitychange", onVisibility);
    sync();

    return () => {
      resizeObserver?.disconnect();
      intersection?.disconnect();
      window.removeEventListener("resize", onWindowResize);
      document.removeEventListener("visibilitychange", onVisibility);
      renderer.destroy();
      rendererRef.current = null;
    };
  }, [reduced]);

  // State changes ease in (or redraw once under reduced motion).
  useEffect(() => {
    stateRef.current = visualState;
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setState(visualState, reduced);
    if (reduced) renderer.renderStatic();
  }, [visualState, reduced]);

  return (
    <div ref={hostRef} className="aerion-energy-field" data-canvas={available ? "on" : "off"}>
      <canvas ref={canvasRef} className="aerion-energy-canvas" />
      <canvas ref={bloomRef} className="aerion-energy-bloom" />
      {/* No-canvas fallback (jsdom / disabled 2D): a lit CSS core. */}
      {!available ? <span className="aerion-energy-fallback" /> : null}
    </div>
  );
}

export const Reactor = memo(function Reactor({
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
  const reduced = usePrefersReducedMotion();

  return (
    <figure
      className="aerion-reactor"
      data-testid="hud-reactor"
      data-state={state}
      data-visual-state={visualState}
      data-pattern={style.pattern}
      data-tone={style.tone}
      data-reduced-motion={reduced ? "true" : "false"}
    >
      <div className="aerion-reactor-visual" aria-hidden>
        <div className="aerion-reactor-aura" />
        <CoreRings pattern={style.pattern} />
        <EnergyField visualState={visualState} reduced={reduced} />
      </div>

      <figcaption className="aerion-reactor-caption">
        <span className="sr-only" data-testid="hud-state-label">
          {label}
        </span>
        {attention.length > 0 ? (
          <span className="aerion-reactor-attention" data-testid="hud-attention">
            {attention.map((flag) => (
              <span key={flag} className="aerion-reactor-flag">
                {flag}
              </span>
            ))}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
});
