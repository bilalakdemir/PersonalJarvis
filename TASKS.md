# AERION Master Task Ledger

One CURRENT task at a time. An item can move into CURRENT only after the previous CURRENT task has been completed, recorded in STATE.md, and verified.

## CURRENT
### CURRENT — PM-002 — Implement a durable PM task-execution journal, reusing the existing governed ProjectStateStore for canonical-file changes.
Acceptance: SQLite WAL journal, transactional project revisions, single active task, restart persistence and isolated tests. Canonical project Markdown changes still require existing approval-bound ProjectStateStore.

## NEXT (ordered, not active)
- [ ] PM-003 — Add multi-project isolation, crash/restart recovery and concurrent-update tests.
- [ ] PM-004 — Connect Project Manager task dispatch to existing MissionManager project scope.
- [ ] PM-005 — Integrate concise project summaries and permission-gated AERION delegation.
- [ ] PM-006 — Run staged integration/CI/production smoke; preserve known-good rollback.

## BLOCKED / VERIFICATION DEBT
- [ ] MEM-VERIFY-LIVE — Verify one naturally occurring real-user chat turn is captured in the running temporary store without reading private content or incurring synthetic paid requests.
- [ ] PRIVACY-HOOK — Correct pre-push privacy identity and develop-base behavior; preserve CI privacy checks.

## DONE
- [x] PM-001 — Documented and verified Project Manager contracts and canonical project loader integration (PR #59; CI PASS; merge eaa9aac4).
- [x] MEM-001 — Temporary capture, governed expiry review, and standards gating integrated (PR #57).
- [x] MEM-002 — Real chat completion contract covered by regression test (PR #58).

## LATER
- [ ] Specialist-agent responsibilities and individual skill selection, after Project Manager v1 passes its gates.
- [ ] HUD enhancement from the selectively reused visual reference.
- [ ] Controlled release/rollback automation and stability-window cleanup.
