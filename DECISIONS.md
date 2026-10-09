# AERION Decision Log

These decisions are approved operating directions. Any new standard requires an explicit governed approval; this file alone does not create new authority.

## D-0001 — Personal chief/orchestrator (ADR-AERION-001; 2026-09-30)
Decision: AERION is the owner's only top-level personal interface; project-specific managers own execution and state.
Reason: Keep project execution and user-level orchestration separate.
Status: ACTIVE

## D-0002 — Project-scoped management (ADR-AERION-002; 2026-09-30)
Decision: Each substantive project has one dedicated Project Manager and canonical PROJECT.md, STATE.md, DECISIONS.md, TASKS.md and BACKLOG.md in its own context. Managers may call specialist workers only after their roles are defined.
Reason: Ensure bounded authority and persistent project context.
Status: ACTIVE

## D-0003 — Single CURRENT task (ADR-AERION-003; 2026-09-30)
Decision: Execute one small task, complete it, record it, verify it and only then advance. SIDE QUEST and FUTURE items cannot silently displace CURRENT.
Reason: Prevent context drift and unverified work progression.
Status: ACTIVE

## D-0004 — Governed memory (ADR-AERION-004; 2026-09-30)
Decision: Separate approved durable project decisions and standards from expiring conversation context. New long-term standards require explicit approval before promotion.
Reason: Preserve intentional memory governance and user control.
Status: ACTIVE

## D-0005 — Safe releases and rollback (ADR-AERION-005; 2026-09-30)
Decision: Preserve the current production version and a known-good predecessor, with approximately seven days of stability validation before deleting redundant full backups.
Reason: Enable safe rollback without indefinitely retaining full releases.
Status: ACTIVE

## D-0006 — Reuse core infrastructure (ADR-AERION-006; 2026-10-08)
Decision: Project Manager v1 extends the existing MissionManager, MissionProjectScope, event store, approval workflow and ProjectStateStore rather than creating competing mission or canonical-file writers. AERION remains the owner's interface.
Reason: Prevent divergent writers, duplicated state machines and approval bypasses.
Status: ACTIVE

## D-0007 — Verified evidence only (ADR-AERION-007; 2026-10-08)
Decision: Green unit and CI tests are code acceptance evidence but do not prove a genuine live user turn. Record live verification separately.
Reason: Keep completion claims aligned with independently verified evidence.
Status: ACTIVE

## D-0008 — Phased agents (ADR-AERION-008; 2026-09-30)
Decision: Stabilize core Jarvis first, then Project Managers, then specialist-agent roles and skills. PersonalJarvis is the foundation and forks-ai/Jarvis is a HUD inspiration; camera features are excluded.
Reason: Limit early complexity and defer unnecessary components.
Status: ACTIVE
