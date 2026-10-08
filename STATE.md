# AERION Working State

Last reviewed: 2026-10-08
Repository baseline: e967f7f6c8e44492375a314401f0b1bd29437053

## Main goal
Deliver a dependable personal chief assistant that routes substantive projects to dedicated Project Managers while retaining strict memory and approval governance.

## Current milestone
Project Manager v1 foundation.

## CURRENT (exactly one)
PM-001 — Specify the Project Manager boundary and canonical state/task contract, with existing MissionManager integration and no new specialist agents.

## Last completed and verified
- Memory v1 capture/governance/expiry-review code merged by PR #57 as 41cf27ec.
- Real ChatTurn/ChatCompletion synthetic regression merged by PR #58 as e967f7f6; 44 focused tests passed locally and GitHub CI passed.
- Production checkout updated to e967f7f6; previous runtime health and rollback checks passed.

## Known open validation
- A genuine user chat, without a simulated completion, has not yet been proven to create an expiring record on the live runtime. Do not send billable model calls solely for this check while the user is away.
- Privacy pre-push on Windows warned about missing private-email configuration and an origin/main comparison on a develop-based repository. This is a tooling debt; CI privacy gates for PR #57 passed.

## Single next logical step
Verify PM-001 contract against the existing missions, approvals and event systems; then implement the smallest project-state ledger with contract tests in a separate branch.

## Deployment and rollback
Production is a shared dirty worktree. Never reset or overwrite unrelated assets. Retain the previous known-good version and the 2026-10-08 Memory v1 SQLite/config backup until the updated release has completed its stability window.
