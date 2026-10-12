# AERION Master Task Ledger

One CURRENT task at a time. An item can move into CURRENT only after the previous CURRENT task has been completed, recorded in STATE.md, and verified.

## CURRENT
### CURRENT — PM-002 — Securely complete and accept the Project Manager task-execution journal
Acceptance: durable SQLite WAL journal; exact project/task/revision matching; one active task; restart persistence; read-only canonical alignment; fail-closed approved reconciliation; separately verified signed QA receipt; genuine owner WebAuthn for protected approval paths; staged end-to-end recovery and rollback. Canonical file changes must continue through the existing approval-bound ProjectStateStore.

- [x] Core journal, alignment, reconciliation, receipt-verification and approval-record bridge code MERGED (PRs #60, #61, #62, #63, #64, #66, #67). Code merged does not mean live acceptance.
- [ ] PR #65: open restart/concurrency test PR remains under previously encountered merge safety restrictions; do not bypass.
- [ ] PR #68: obtain authorized independent security/code review for owner WebAuthn and recovery integration; current head CI passed, PR remains draft and unmerged.
- [ ] Independently authorized external reviewer or service, with exclusive signing-key custody, secure receipt inbox/ACL, and admin-verified public-key fingerprint.
- [ ] Genuine independently issued receipt linked to actual durable owner approval, exact task/project/revisions and audit evidence.
- [ ] Staged owner Windows Hello approval, signed completion recovery, failure/rollback scenarios and independent acceptance evidence.
- [ ] Apply gated production release only after approvals, known-good backup and rollback checks; update verified project state and close PM-002 only after real acceptance.

## NEXT (ordered, not active)
- [ ] PM-003 — Add multi-project isolation, crash/restart recovery and concurrent-update tests.
- [ ] PM-004 — Connect Project Manager task dispatch to existing MissionManager project scope.
- [ ] PM-005 — Integrate concise project summaries and permission-gated AERION delegation.
- [ ] PM-006 — Run staged integration/CI/production smoke; preserve known-good rollback.

## BLOCKED / VERIFICATION DEBT
- [ ] QA-INDEPENDENCE — Identify an actual independent reviewer/service, segregated signing-key custody, inbox ACL and pinned trust root before declaring PM-002 acceptance.
- [ ] SECURITY-REVIEW — Obtain authorized review of protected project-state approval code in PR #68. No approval bypass or author self-attestation.
- [ ] LOCAL-VERIFY — Reconnect the authorized Akdemir desktop session and verify the local canonical docs, production version, health, guards and backup inventory before deployment.
- [ ] MEM-VERIFY-LIVE — Verify one naturally occurring real-user chat turn is captured in the running temporary store without reading private content or incurring synthetic paid requests.
- [ ] PRIVACY-HOOK — Correct pre-push privacy identity and develop-base behavior; preserve CI privacy checks.

## DONE
- [x] PM-001 — Documented and verified Project Manager contracts and canonical project loader integration (PR #59).
- [x] MEM-001 — Temporary capture, governed expiry review, and standards gating integrated (PR #57).
- [x] MEM-002 — Real chat completion contract covered by regression test (PR #58).

## LATER
- [ ] Specialist-agent responsibilities and individual skill selection, after Project Manager v1 passes its gates.
- [ ] HUD enhancement from the selectively reused visual reference.
- [ ] Controlled release/rollback automation and stability-window cleanup.
