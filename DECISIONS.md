# AERION Decision Log

Each entry records a durable approved direction, not a transient conversation summary.

## ADR-AERION-001 — Personal chief/orchestrator (2026-09-30)
AERION is the owner's only top-level personal interface; project-specific managers own execution and state. This keeps individual project workloads bounded.

## ADR-AERION-002 — Project-scoped management (2026-09-30)
Each substantive project has one dedicated Project Manager and canonical PROJECT.md, STATE.md, DECISIONS.md, TASKS.md, and BACKLOG.md in its own project context. Managers may call specialist workers only after their role boundaries are defined.

## ADR-AERION-003 — Single CURRENT task (2026-09-30)
Execute small task -> complete -> record -> verify -> next. SIDE QUEST and FUTURE work must not silently displace CURRENT.

## ADR-AERION-004 — Governed memory (2026-09-30)
Keep approved durable decisions/standards separate from expiring conversation context. New standards require explicit approval before promotion.

## ADR-AERION-005 — Safe releases and rollback (2026-09-30)
Preserve the currently running version and one known-good predecessor, with approximately seven days of stability validation before deleting redundant full backups.

## ADR-AERION-006 — Reuse core infrastructure (2026-10-08)
Project Manager v1 must extend existing jarvis/missions MissionManager, MissionProjectScope, event store and recovery contracts rather than spawning a competing mission dispatcher. AERION remains the user's interface.

## ADR-AERION-007 — Verified evidence only (2026-10-08)
Green unit/CI tests justify a code acceptance gate; they do not prove a live, billable user turn. Record live verification separately and never fabricate its success.

## ADR-AERION-008 — Phased agents (2026-09-30)
Stabilize core Jarvis first, then Project Managers, then specialist-agent roster and skills. PersonalJarvis is the foundation and forks-ai/Jarvis is a HUD inspiration; camera features are excluded.
