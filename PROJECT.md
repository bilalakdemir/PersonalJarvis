# Project
AERION

# AERION Project Charter

## Main Goal
Build a private, dependable AERION chief assistant on PersonalJarvis that delegates scoped projects to dedicated managers while enforcing memory, approval and rollback governance.

## Purpose
Build AERION as the owner's private chief assistant on the PersonalJarvis foundation. AERION is the interface to personal tasks and to project-specific Project Manager agents, not a substitute for their execution ledgers.

## Scope
- Stable core: desktop/web access, orchestration, identity, permissions, memory, observability and rollback.
- Two-layer memory: bounded temporary context and explicitly approved persistent decisions, projects, and standards.
- One dedicated Project Manager per substantive project, created only after core governance and state contracts are verified.
- Reliable work handoff using existing mission infrastructure; AERION routes, reports, and coordinates.
- Visual layer may selectively borrow HUD ideas from forks-ai/Jarvis; PersonalJarvis remains the primary foundation.

## Out of scope for the current milestone
- Specialist-agent roster or skills before core and Project Manager boundaries are stable.
- Webcam/camera functions.
- Autonomous final approval for high-risk actions, standards, irreversible changes, or external submissions.
- Replacing existing mission dispatch and recovery frameworks.

## Engineering and operating rules
1. Exactly one CURRENT project task. Complete, record, verify, then advance.
2. Every new idea is CURRENT, SIDE QUEST, or FUTURE. Only CURRENT changes execution order.
3. Persistent memory follows governed approvals; temporary memory expires with pre-expiry review.
4. Protect production and user data. Use isolated worktrees, CI, staged releases, rollback, and a stability window before pruning backups.
5. Separate project memories and permission scopes; no accidental cross-project promotion.
6. Do not claim live acceptance from simulated tests. Record any unverified gates explicitly.
7. No production restart or destructive operation without validated rollback and the applicable approval gate.

## Completion criteria for Project Manager v1
- A validated project registry, state ledger, and task lifecycle enforce one CURRENT task per project.
- State updates survive restart and support independent audit and recovery.
- Project Manager can request existing missions under a bounded project scope.
- AERION can obtain concise project status without accessing unrelated project content.
- Automated tests cover task transitions, concurrency, crash recovery, permissions and scope isolation.
- A clear handoff from approved completion to the next task is demonstrable end-to-end.
