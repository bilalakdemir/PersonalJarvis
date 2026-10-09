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

## 8. Baseline contract audit (2026-10-09)

Inspected existing source before implementing a new project ledger:
- `jarvis/missions/manager.py`: `MissionProjectScope` already carries `project_id`, `task_id`, and `project_root`. `dispatch()` validates the ID/root pairing and writes these fields in `MissionDispatched`; `project_scope(mission_id)` reconstructs them from persisted events after restart. Do not create a competing scope store.
- `jarvis/missions/event_store.py`: mission events use a SQLite WAL append-before-publish flow. The project ledger needs its own atomic compare-and-swap transaction; the mission event store is not a task-state database.
- `jarvis/missions/manager.py`: crash recovery is explicitly opt-in and requires primary-instance authority. The Project Manager must not invoke recovery from a secondary or read-only process.
- `jarvis/missions/task_bridge.py`: global `MissionCompleted` signals carry `mission_id` and terminal status, **not** `project_id` or `task_id`. PM-004 must resolve the persisted scope via `MissionManager.project_scope(mission_id)` before updating a project task. Unknown, missing, or mismatched scope must fail closed.
- `jarvis/missions/tool_approvals.py`: the approval coordinator is mission-scoped and only projects existing approval events. A Project Manager cannot infer approval from its own task state; it must use the existing approval workflow and verify that the approval belongs to the dispatched mission.

### Integration hazards and required evidence
1. A mission header is written before the `MissionDispatched` event; a crash between them can leave a header without a scoped event. Do not assume every mission header has valid project authority.
2. A `MissionCompleted` notification may be lost or duplicated across restarts. The Project Manager must reconcile durable mission terminal events and apply completion idempotently, keyed by mission ID and task revision.
3. A worker's terminal `approved` mission state is **not** automatically an approved Project Manager task: task acceptance requires independent evidence verification.
4. The ledger's `CURRENT`/`VERIFYING` uniqueness constraint must be enforced transactionally, not only by a Markdown file or a process-local lock.
5. Before any live deployment, test project/task scope resolution after restart and confirm the existing high-risk approval path is unchanged.

Audit outcome: the proposed boundary is compatible with the existing mission subsystem, but the project ledger, reconciliation adapter and independent verifier remain **unimplemented**. This is a documentation/contract gate, not production acceptance.
