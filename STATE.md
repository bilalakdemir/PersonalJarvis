# AERION Working State

Project: AERION
Phase: Project Manager v1 foundation
Last reviewed: 2026-10-12
Repository baseline: GitHub `develop` 3038cbf0752c074df522b57f83a0ffbb02869bc7 (verified 2026-10-12)

## Main goal
Deliver a dependable personal chief assistant that routes substantive projects to dedicated Project Managers while retaining strict memory and approval governance.

## Current milestone
Project Manager v1 foundation.

## CURRENT TASK
PM-002 — Securely complete and accept the Project Manager task-execution journal
Status: NOT DONE. Canonical-file changes continue through the existing governed ProjectStateStore.

## Last Completed
- PM-001: Project Manager boundaries, canonical files and loader contract verified; PR #59 merged.
- Memory v1 capture/governance/expiry-review code merged by PR #57.
- Real ChatTurn/ChatCompletion synthetic regression merged by PR #58.
- Last observed production checkout `e967f7f6c` on the Akdemir computer; it was deliberately left unchanged during PM-002/Brave work. The current local production status could not be independently rechecked in this review because the remote connection returned an authorization error.

## PM-002 progress (not yet complete)
- PR #60 MERGED — isolated SQLite WAL task journal, scoped revisions, single CURRENT task and durable restart semantics.
- PR #61 MERGED — read-only journal/canonical-state alignment.
- PR #62 MERGED — fail-closed project-task reconciliation; no alternate canonical Markdown writer.
- PR #63 MERGED — Ed25519 verification of independently issued, exact-task completion receipts. Verification does not provide a real independent reviewer.
- PR #64 MERGED — read-only binding to durable applied owner-approval records.
- PR #66 MERGED — external receipt inbox reader; independent custody and inbox access control still require real provisioning.
- PR #67 MERGED — read-only public-key-pinned receipt diagnostic.
- PR #65 OPEN, NOT MERGED — restart/concurrency regression tests; previous merge operation encountered a safety gate. Do not work around that gate.
- PR #68 OPEN, DRAFT, NOT MERGED — owner WebAuthn/Windows Hello for protected project-state approval, an offline QA signer utility and explicit signed-receipt recovery. Exact head `721c2800d9a311afa79d94023c6e86760656245b` passed GitHub CI. Isolated staging passkey enrollment/assertion is not production acceptance.
- No live independent QA signer/key custody, genuine QA-signed receipt, approved production enrollment, or complete staged rollback acceptance has been verified. Do not declare PM-002 DONE or start PM-003.

## SIDE QUEST status (does not displace CURRENT)
- PR #69 OPEN, NOT MERGED — Brave-first/Brave-only browser launch. Exact head `d88c4f2b728b2cef397ce9f3a7e217d0894b6823` passed CI (22 successful, 8 intentionally skipped). On Akdemir, the user-scoped `JARVIS_BROWSER_ONLY=brave` and direct Brave launch were last verified, but Windows HTTP's general default remained Edge and the code was not deployed to production.

## Known open validation
- A genuine user chat, without simulated completion, has not yet been proven to create an expiring record on the live runtime. Do not incur synthetic paid model calls solely for verification.
- Windows pre-push privacy hook has a known configuration/base-branch warning. Preserve CI privacy gates; do not call a fail-open warning a pass.
- Protected owner-approval paths and real externally operated QA receipt flow need authorized independent security/reviewer acceptance.
- A prior Windows text-encoding test issue was known on the baseline; do not misrepresent excluded tests as passing.

## Next Logical Step
For PM-002, provision or identify an independently authorized reviewer and real externally controlled Ed25519 signing-key/inbox/public-key trust anchor; arrange authorized security review of PR #68 and a genuine staged owner-passkey approval + external-receipt recovery + rollback acceptance test. Only then evaluate merge and production rollout against the approved release gate. No self-signing, self-review, safety bypass, production restart, or PM-003 promotion.

## Blockers
- External independent QA identity, key custody, trusted receipt channel/public-key pin, independent code review, and staged real-world acceptance are not yet verified.
- The remote Akdemir connection was unavailable during this 2026-10-12 reconciliation; do not infer live machine status from GitHub.
- Canonical project files may change only through the existing proposal/approval-bound state writer after the owner reviews the exact diff.

## Deployment and rollback
Production was last observed at `e967f7f6c`, with a shared dirty worktree. This is historical evidence, not a fresh production check. Never reset or overwrite unrelated assets. Preserve the known-good predecessor and the 2026-10-08 Memory v1 SQLite/config backup until release stability and the applicable retention window have been verified.
