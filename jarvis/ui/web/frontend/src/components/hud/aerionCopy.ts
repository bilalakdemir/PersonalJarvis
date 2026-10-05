/**
 * AERION chrome copy.
 *
 * Every string lives under `hud.aerion.*` in the locale files (en/de/es
 * fragments ship with this change). The English map below is the fallback
 * used when a key is missing, so the HUD never renders a raw key.
 */
import { useMemo } from "react";

import { useT } from "@/i18n";

export const AERION_COPY = {
  "brand.core": "AERION CORE",
  "brand.tagline": "PERSONAL INTELLIGENCE SYSTEM",
  "brand.caption": "SYNTHESIZE · REASON · PLAN · EXECUTE",

  "nav.label": "AERION navigation",
  "nav.chat": "Chat",
  "nav.projects": "Projects",
  "nav.agents": "Agents",
  "nav.memory": "Memory",
  "nav.tools": "Tools",
  "nav.system": "System",
  "nav.settings": "Settings",

  "header.online": "Online",
  "header.agents": "Agents",
  "header.tasks": "Tasks",
  "header.voice": "Voice",

  "state.OFF": "Offline",
  "state.STANDBY": "Standby",
  "state.LISTENING": "Listening",
  "state.THINKING": "Thinking",
  "state.WORKING": "Working",
  "state.WAITING_FOR_APPROVAL": "Awaiting approval",
  "state.SPEAKING": "Speaking",
  "state.ERROR": "Error",
  "state.COMPLETED": "Completed",

  "mode.title": "Current mode",
  "mode.OFF": "Runtime unreachable",
  "mode.STANDBY": "Ready for your next command",
  "mode.LISTENING": "Listening to you",
  "mode.THINKING": "Reasoning on your request",
  "mode.WORKING": "Working on active operations",
  "mode.WAITING_FOR_APPROVAL": "Waiting for your decision",
  "mode.SPEAKING": "Responding",
  "mode.ERROR": "Something needs attention",
  "mode.COMPLETED": "Operation completed",

  "status.title": "System status",
  "status.nominal": "All systems nominal",
  "status.attention": "Attention required",
  "status.degraded": "Link degraded",
  "status.stream": "Event stream",
  "status.voice": "Voice",
  "status.attention_flags": "Attention",

  "capability.listen": "Listen",
  "capability.think": "Think",
  "capability.process": "Process",
  "capability.learn": "Learn",
  "capability.plan": "Plan",
  "capability.execute": "Execute",

  "panel.actions": "Your actions",
  "panel.today": "Today",
  "panel.conversation": "Conversation",
  "panel.tasks": "Running tasks",
  "panel.agents": "Agents",
  "panel.memory": "Memory activity",
  "panel.system": "System",

  "link.view_all": "View all",
  "link.manage": "Manage",
  "link.open_projects": "Open projects",

  "filter.all": "All",
  "filter.tools": "Tools",
  "filter.proposals": "Proposals",

  "empty.actions_title": "All clear",
  "empty.actions": "Nothing needs your approval right now.",
  "empty.today": "No activity recorded today yet.",
  "empty.tasks": "No operation is running.",
  "empty.agents": "No agent activity is being reported.",
  "empty.memory": "No memory activity yet.",
  "today.approval": "Approval requested",
  "today.error": "Error",

  "system.computer": "Computer use",
  "system.capture": "Screen capture",
  "system.errors": "Errors",
  "system.no_errors": "None reported",
  "system.idle": "Idle",
  "system.active": "Active",
  "system.in_control": "In control",
  "system.released": "Released",
  "system.ok": "OK",

  "approval.details": "Details",

  "command.placeholder": "Message AERION...",
  "command.open": "Open the AERION conversation",
  "command.attach": "Attach a file in chat",
  "command.voice": "Voice input in chat",
  "command.send": "Open chat",

  "quick.label": "Quick actions",
  "quick.research": "Deep Research",
  "quick.analyze": "Analyze File",
  "quick.plan": "Create Plan",
  "quick.agents": "Work with Agents",
  "quick.search": "Search Web",
  "quick.more": "More",

  "time.now": "just now",
  "time.min": "{n} min ago",
  "time.hour": "{n} h ago",
  "time.day": "{n} d ago",
} as const;

export type AerionCopyKey = keyof typeof AERION_COPY;
export type AerionSay = (key: AerionCopyKey, vars?: Record<string, string | number>) => string;

function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in vars ? String(vars[key]) : match));
}

export function useAerionCopy(): { say: AerionSay; t: (key: string) => string } {
  const t = useT();
  const say = useMemo<AerionSay>(
    () => (key, vars) => {
      const full = `hud.aerion.${key}`;
      const value = t(full);
      const text = value === full ? AERION_COPY[key] : value;
      return vars ? fill(text, vars) : text;
    },
    [t],
  );
  return { say, t };
}
