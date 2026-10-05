/**
 * AERION command-center chrome: header, stage frame, mode / system readouts,
 * capability rails, pedestal and command dock.
 *
 * Purely presentational. Everything shown is passed in from HudView, which
 * reads it from the canonical HudSnapshot and the event store.
 */
import { memo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  AudioLines,
  Bot,
  BookOpen,
  Brain,
  BrainCircuit,
  ClipboardCheck,
  Cog,
  Cpu,
  FileSearch,
  FolderKanban,
  MessageCircle,
  Mic,
  MoreHorizontal,
  Paperclip,
  PlayCircle,
  ScanSearch,
  Search,
  Send,
  Settings,
  Target,
  Users,
  Wrench,
} from "lucide-react";

import type { AerionSay } from "@/components/hud/aerionCopy";
import type { AerionVisualState } from "@/lib/hudSemantics";
import { cn } from "@/lib/utils";
import type { SectionId } from "@/store/events";
import type { HudConnectionState } from "@/types/hud";

type Navigate = (section: SectionId) => void;

export function AerionMark({ className }: { className?: string }) {
  return (
    <svg className={cn("aerion-mark", className)} viewBox="0 0 40 40" aria-hidden focusable="false">
      <defs>
        <linearGradient id="aerion-mark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#7fe6ff" />
          <stop offset="0.55" stopColor="#14a6ff" />
          <stop offset="1" stopColor="#0b5cd6" />
        </linearGradient>
      </defs>
      <path d="M20 2.5 38 36h-8.6L20 17.6 10.6 36H2z" fill="url(#aerion-mark-fill)" />
      <path d="M20 23.5 25.6 33.5H14.4z" fill="#bff3ff" opacity="0.9" />
    </svg>
  );
}

function NavButton({
  icon,
  label,
  section,
  primary,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  section: SectionId;
  primary?: boolean;
  onSelect: Navigate;
}) {
  return (
    <button
      type="button"
      className={cn("aerion-topnav-button", primary && "is-primary")}
      data-section={section}
      onClick={() => onSelect(section)}
    >
      <span aria-hidden>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

function MetricChip({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return (
    <div className="aerion-metric">
      <span className="aerion-metric-icon" aria-hidden>
        {icon}
      </span>
      <span className="aerion-metric-text">
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

export function AerionHeader({
  say,
  connection,
  connectionLabel,
  coreStateLabel,
  voiceState,
  agentCount,
  taskCount,
  dateLabel,
  timeLabel,
  onNavigate,
}: {
  say: AerionSay;
  connection: HudConnectionState;
  connectionLabel: string;
  coreStateLabel: string;
  voiceState: string;
  agentCount: number;
  taskCount: number;
  dateLabel: string;
  timeLabel: string;
  onNavigate: Navigate;
}) {
  return (
    <header className="aerion-global-header">
      <div className="aerion-header-left">
        <div className="aerion-brand-lockup">
          <AerionMark />
          <div className="aerion-wordmark" data-testid="aerion-wordmark">
            AERION
          </div>
        </div>
        <nav className="aerion-topnav" aria-label={say("nav.label")}>
          <NavButton
            primary
            icon={<MessageCircle />}
            label={say("nav.chat")}
            section="chats"
            onSelect={onNavigate}
          />
          <NavButton
            icon={<FolderKanban />}
            label={say("nav.projects")}
            section="chat-workspace"
            onSelect={onNavigate}
          />
          <NavButton icon={<Users />} label={say("nav.agents")} section="agents" onSelect={onNavigate} />
          <NavButton icon={<Brain />} label={say("nav.memory")} section="memory" onSelect={onNavigate} />
          <NavButton icon={<Wrench />} label={say("nav.tools")} section="plugins" onSelect={onNavigate} />
          <NavButton icon={<Cog />} label={say("nav.system")} section="settings" onSelect={onNavigate} />
        </nav>
      </div>

      <div className="aerion-header-core" aria-hidden>
        <span>{say("brand.core")}</span>
        <strong>AERION</strong>
        <small>{say("brand.tagline")}</small>
      </div>

      <div className="aerion-header-right">
        <div className="aerion-status-group" data-testid="hud-status-line" data-connection={connection}>
          <span className="aerion-status-segment aerion-status-online" title={connectionLabel}>
            <span className="aerion-status-dot" aria-hidden />
            <span>{connection === "CONNECTED" ? say("header.online") : connectionLabel}</span>
            {connection === "CONNECTED" ? <span className="sr-only">{connectionLabel}</span> : null}
          </span>
          <span className="aerion-status-segment">{coreStateLabel}</span>
          <span className="aerion-status-segment">
            {say("header.voice")} <em>{voiceState}</em>
          </span>
        </div>
        <MetricChip icon={<Bot />} label={say("header.agents")} value={agentCount} />
        <MetricChip icon={<Cpu />} label={say("header.tasks")} value={taskCount} />
        <div className="aerion-clock">
          <span>{dateLabel}</span>
          <strong>{timeLabel}</strong>
        </div>
        <span className="aerion-header-divider" aria-hidden />
        <button
          type="button"
          className="aerion-system-button"
          onClick={() => onNavigate("settings")}
          aria-label={say("nav.settings")}
        >
          <Settings aria-hidden />
        </button>
      </div>
    </header>
  );
}

export const StageFrame = memo(function StageFrame() {
  const ref = useRef<SVGSVGElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current?.parentElement;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const { w, h } = box;
  const c = 22;
  const top = 16;
  const notch = Math.min(w * 0.25, 220);
  const mid = w / 2;
  const railTop = h * 0.36;
  const railBottom = h * 0.84;
  const frame =
    w > 0
      ? `M0.5 ${h * 0.97}V${top + c}L${c} ${top}H${mid - notch - 26}L${mid - notch} 0.5` +
        `M${mid + notch} 0.5L${mid + notch + 26} ${top}H${w - c}L${w - 0.5} ${top + c}V${h * 0.97}`
      : "";
  const accents =
    w > 0
      ? `M0.5 ${top + c + 70}V${top + c}L${c} ${top}H${c + 120}` +
        `M${w - c - 120} ${top}H${w - c}L${w - 0.5} ${top + c}V${top + c + 70}` +
        `M0.5 ${railTop}V${railBottom}M${w - 0.5} ${railTop}V${railBottom}`
      : "";
  return (
    <svg
      ref={ref}
      className="aerion-stage-frame"
      width={w}
      height={h}
      viewBox={`0 0 ${Math.max(1, w)} ${Math.max(1, h)}`}
      aria-hidden
    >
      <defs>
        <linearGradient id="aerion-frame-fade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1ab8ff" stopOpacity="0.75" />
          <stop offset="0.7" stopColor="#1ab8ff" stopOpacity="0.4" />
          <stop offset="1" stopColor="#1ab8ff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={frame} className="aerion-stage-frame-line" stroke="url(#aerion-frame-fade)" />
      <path d={accents} className="aerion-stage-frame-accent" />
    </svg>
  );
});

const WAVE = [
  4, 7, 12, 9, 16, 22, 14, 26, 18, 30, 21, 13, 24, 16, 9, 18, 27, 15, 11, 20, 13, 8, 12, 6, 9, 5, 7, 4,
];

export function Waveform({ active, className }: { active: boolean; className?: string }) {
  return (
    <svg
      className={cn("aerion-wave", className)}
      viewBox="0 0 168 34"
      data-active={active ? "true" : "false"}
      aria-hidden
    >
      {WAVE.map((h, i) => (
        <rect
          key={i}
          x={i * 6}
          y={17 - h / 2}
          width="2.4"
          height={h}
          rx="1.2"
          style={{ animationDelay: `${(i % 7) * -0.13}s` }}
        />
      ))}
    </svg>
  );
}

export function ModeReadout({
  say,
  coreState,
  stateLabel,
  detail,
}: {
  say: AerionSay;
  coreState: AerionVisualState;
  stateLabel: string;
  detail: string;
}) {
  const active = coreState === "LISTENING" || coreState === "SPEAKING" || coreState === "THINKING";
  return (
    <div className="aerion-mode-readout" data-visual-state={coreState}>
      <span className="aerion-mode-kicker">{say("mode.title")}</span>
      <strong className="aerion-mode-value">{stateLabel}</strong>
      <span className="aerion-mode-detail">{detail}</span>
      <Waveform active={active} />
    </div>
  );
}

export interface StatusLine {
  label: string;
  value?: string;
  tone: "green" | "gold" | "red" | "cyan" | "muted";
}

export function SystemStatus({
  say,
  headline,
  lines,
}: {
  say: AerionSay;
  headline: StatusLine;
  lines: StatusLine[];
}) {
  return (
    <div className="aerion-system-summary">
      <span className="aerion-system-kicker">{say("status.title")}</span>
      <ul>
        {[headline, ...lines].map((line) => (
          <li key={line.label} data-tone={line.tone}>
            <i aria-hidden />
            <span>{line.label}</span>
            {line.value ? <b>{line.value}</b> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface Capability {
  key: string;
  label: string;
  icon: ReactNode;
  active: boolean;
}

export function capabilityIcons() {
  return {
    listen: <AudioLines />,
    think: <BrainCircuit />,
    process: <Cog />,
    learn: <BookOpen />,
    plan: <Target />,
    execute: <PlayCircle />,
  };
}

export function CapabilityRail({ side, items }: { side: "left" | "right"; items: Capability[] }) {
  return (
    <div className={`aerion-core-capabilities aerion-core-capabilities-${side}`} aria-hidden>
      {items.map((item) => (
        <div
          key={item.key}
          className="aerion-cap"
          data-active={item.active ? "true" : "false"}
          style={{ "--cap-chars": Math.max(4, item.label.length) } as CSSProperties}
        >
          <span className="aerion-cap-label">{item.label}</span>
          <span className="aerion-cap-icon">{item.icon}</span>
        </div>
      ))}
    </div>
  );
}

export const Pedestal = memo(function Pedestal() {
  const beams = [-120, -84, -52, -24, 0, 24, 52, 84, 120];
  return (
    <svg
      className="aerion-pedestal"
      viewBox="0 0 1000 240"
      preserveAspectRatio="xMidYMax meet"
      aria-hidden
      focusable="false"
    >
      <defs>
        <radialGradient
          id="aerion-pool"
          cx="500"
          cy="150"
          r="440"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(1 0 0 0.2 0 120)"
        >
          <stop offset="0" stopColor="#ffd27a" stopOpacity="0.55" />
          <stop offset="0.3" stopColor="#ff9d3a" stopOpacity="0.22" />
          <stop offset="0.65" stopColor="#1478d8" stopOpacity="0.14" />
          <stop offset="1" stopColor="#1478d8" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="aerion-beam" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#7fdcff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#7fdcff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="aerion-gold-rim" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#ffb547" stopOpacity="0.15" />
          <stop offset="0.2" stopColor="#ffc35c" stopOpacity="1" />
          <stop offset="0.5" stopColor="#ffe1a0" stopOpacity="0.5" />
          <stop offset="0.8" stopColor="#ffc35c" stopOpacity="1" />
          <stop offset="1" stopColor="#ffb547" stopOpacity="0.15" />
        </linearGradient>
      </defs>
      <ellipse cx="500" cy="160" rx="460" ry="80" fill="url(#aerion-pool)" />
      <g className="aerion-pedestal-beams">
        {beams.map((x) => (
          <path
            key={x}
            d={`M${500 + x} ${176 - Math.abs(x) * 0.06}V${16 + Math.abs(x) * 0.5}`}
            stroke="url(#aerion-beam)"
          />
        ))}
      </g>
      <ellipse cx="500" cy="152" rx="488" ry="86" className="aerion-ped-ticks" />
      <ellipse
        cx="500"
        cy="150"
        rx="454"
        ry="78"
        className="aerion-ped-gold"
        stroke="url(#aerion-gold-rim)"
      />
      <path
        d="M46 150A454 78 0 0 0 954 150"
        className="aerion-ped-gold-front"
        stroke="url(#aerion-gold-rim)"
      />
      <ellipse cx="500" cy="156" rx="408" ry="62" className="aerion-ped-cyan" />
      <ellipse cx="500" cy="166" rx="350" ry="48" className="aerion-ped-gold-dots" />
      <ellipse cx="500" cy="180" rx="282" ry="38" className="aerion-ped-cyan aerion-ped-cyan-bright" />
      <ellipse cx="500" cy="190" rx="206" ry="26" className="aerion-ped-inner" />
      <ellipse cx="500" cy="196" rx="124" ry="15" className="aerion-ped-inner aerion-ped-inner-hot" />
      <circle cx="72" cy="182" r="5" className="aerion-ped-flare" />
      <circle cx="928" cy="182" r="5" className="aerion-ped-flare" />
      <circle cx="500" cy="228" r="4" className="aerion-ped-flare aerion-ped-flare-cyan" />
    </svg>
  );
});

export function CommandDock({ say, onNavigate }: { say: AerionSay; onNavigate: Navigate }) {
  const open = () => onNavigate("chats");
  const quick: Array<{ key: string; label: string; icon: ReactNode; section: SectionId }> = [
    { key: "research", label: say("quick.research"), icon: <ScanSearch />, section: "chats" },
    { key: "analyze", label: say("quick.analyze"), icon: <FileSearch />, section: "chats" },
    { key: "plan", label: say("quick.plan"), icon: <ClipboardCheck />, section: "chats" },
    { key: "agents", label: say("quick.agents"), icon: <Users />, section: "agents" },
    { key: "search", label: say("quick.search"), icon: <Search />, section: "chats" },
    { key: "more", label: say("quick.more"), icon: <MoreHorizontal />, section: "jarvis-actions" },
  ];
  return (
    <section className="aerion-command-dock" aria-label={say("command.open")}>
      <div className="aerion-command">
        <button
          type="button"
          className="aerion-command-input"
          onClick={open}
          aria-label={say("command.open")}
        >
          <span className="aerion-command-wave" aria-hidden>
            <AudioLines />
          </span>
          <span className="aerion-command-divider" aria-hidden />
          <span className="aerion-command-placeholder">{say("command.placeholder")}</span>
        </button>
        <button
          type="button"
          className="aerion-command-icon"
          onClick={open}
          aria-label={say("command.attach")}
        >
          <Paperclip aria-hidden />
        </button>
        <button
          type="button"
          className="aerion-command-icon"
          onClick={open}
          aria-label={say("command.voice")}
        >
          <Mic aria-hidden />
        </button>
        <button type="button" className="aerion-command-send" onClick={open} aria-label={say("command.send")}>
          <Send aria-hidden />
        </button>
      </div>
      <nav className="aerion-quick-actions" aria-label={say("quick.label")}>
        {quick.map((item) => (
          <button key={item.key} type="button" title={item.label} onClick={() => onNavigate(item.section)}>
            <span aria-hidden>{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
    </section>
  );
}
