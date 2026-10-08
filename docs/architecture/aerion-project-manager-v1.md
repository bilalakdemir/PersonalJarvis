# AERION Project Manager v1 — execution contract

Status: proposed architecture for implementation; not a deployed component.
Baseline: PersonalJarvis develop at e967f7f6.
Owner: AERION chief interface; execution authority: each project-specific Project Manager.

## 1. Boundaries and ownership
- AERION interprets owner requests, resolves project identity, presents consolidated status and requests high-risk approvals. It is not a shared project task writer.
- Each Project Manager owns one project's PROJECT.md, STATE.md, DECISIONS.md, TASKS.md and BACKLOG.md, with a strict project_root boundary.
- Existing jarvis/missions/manager.py MissionManager owns dispatch, state transitions and replay/recovery; do not implement a second mission state machine.
- Workers return evidence; they do not silently change the authoritative project ledger or authorize their own output.

## 2. Canonical task lifecycle
Exactly one CURRENT task per project, with one VERIFYING task counting as active:

BACKLOG -> NEXT -> CURRENT -> VERIFYING -> DONE
CURRENT -> BLOCKED -> NEXT (after blocker resolved)
VERIFYING -> CURRENT (failed verification; record reason)
CURRENT -> CANCELLED (authorized owner or Project Manager)

Advancement of NEXT to CURRENT is atomic and forbidden while another CURRENT or VERIFYING exists. Each transition records previous and next state, task/project ID, actor, time, reason and evidence reference. Do not store raw private chat in audit events.

## 3. Durability and concurrency
- Use an atomic compare-and-swap revision per project and append-only audit log. Two concurrent writers must not both advance tasks.
- Persist a state transition before reporting or publishing its effects. Recover from a crash by replaying committed events and reconciling pending missions.
- Human-readable project Markdown files are materialized views and bootstrap context, not concurrent-write coordination.
- Validate project_root canonical resolution and reject symlink escapes, cross-project unauthorized reads and writes.

## 4. Delegation and approvals
- Project Managers request work using existing MissionProjectScope(project_id, task_id, project_root). MissionManager remains the sole owner of mission dispatch and state.
- A worker may propose a change; an independent verifier checks evidence before task completion.
- Explicit risk tiers select automated, human-approved, or rejected actions. No Project Manager may bypass an existing approval gate.
- Cross-project status may include approved metadata, never contents of confidential project memory by default.

## 5. Working loop and minimum interface
AERION request -> classify CURRENT/SIDE QUEST/FUTURE -> resolve project -> PM validates task -> scoped MissionManager dispatch -> result evidence -> independent verification -> durable ledger transition -> concise AERION summary.

Proposed operations:
- register_project(project_id, project_root, charter_ref)
- read_status(project_id): current, last verified, next, blocked, revision
- propose_task(project_id, task, expected_revision)
- advance_task(project_id, task_id, evidence_ref, expected_revision)
- record_decision(project_id, decision, approval_ref, expected_revision)
- dispatch_project_mission(project_id, task_id, prompt, risk_tier)

## 6. Acceptance tests
1. Simultaneous NEXT->CURRENT attempts: at most one succeeds.
2. Project A cannot read/write project B's private task content.
3. Crash after persisted state, before event notification: recovery without double-dispatch.
4. Verification failure returns the same task to CURRENT.
5. High-risk action without approval cannot dispatch.
6. Memory standards cannot be silently promoted by Project Manager tasks.
7. Materialized Markdown views can be regenerated from authoritative state.
8. Mission dispatch and results preserve stable project/task identity.

## 7. Phases
PM-001 contract -> PM-002 durable ledger -> PM-003 isolation and recovery -> PM-004 mission integration -> PM-005 AERION status and control -> PM-006 staged release and rollback. Specialist-agent design is deferred.
