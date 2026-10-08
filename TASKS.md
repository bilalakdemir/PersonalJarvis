# AERION Master Task Ledger

One CURRENT task at a time. An item can move into CURRENT only after the previous CURRENT task has been completed, recorded in STATE.md, and verified.

## CURRENT
- [ ] PM-001 — Define Project Manager v1 ownership, task-state contract, mission boundary and recovery invariants. Acceptance: documented contract with one-CURRENT rule, scope isolation, lifecycle and approval gates.

## NEXT (ordered, not active)
- [ ] PM-002 — Implement durable ProjectStateStore/registry and atomic transitions.
- [ ] PM-003 — Add multi-project isolation, crash/restart recovery and concurrent-update tests.
- [ ] PM-004 — Connect Project Manager task dispatch to existing MissionManager project scope.
- [ ] PM-005 — Integrate concise project summaries and permission-gated AERION delegation.
- [ ] PM-006 — Run staged integration/CI/production smoke; preserve known-good rollback.

## BLOCKED / VERIFICATION DEBT
- [ ] MEM-VERIFY-LIVE — Verify one naturally occurring real-user chat turn is captured in the running temporary store without reading private content or incurring synthetic paid requests.
- [ ] PRIVACY-HOOK — Correct pre-push privacy identity and develop-base behavior; preserve CI privacy checks.

## DONE
- [x] MEM-001 — Temporary capture, governed expiry review, and standards gating integrated (PR #57).
- [x] MEM-002 — Real chat completion contract covered by regression test (PR #58).

## LATER
- [ ] Specialist-agent responsibilities and individual skill selection, after Project Manager v1 passes its gates.
- [ ] HUD enhancement from the selectively reused visual reference.
- [ ] Controlled release/rollback automation and stability-window cleanup.
