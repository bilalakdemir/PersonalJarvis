from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionApproved
from jarvis.memory.project_state_approval import (
    ProjectStateMemoryApprovalLifecycle,
    ProjectStateProposalIdentityError,
    ProjectStateProposalLifecycleError,
    ProjectStateProposalStore,
    ProjectStateProposalStaleError,
)
from jarvis.memory.project_state_planner import ProjectStateMemoryPlanner
from jarvis.memory.promotion_queue import MemoryPromotionQueue
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal
from jarvis.projects.loader import load_project_context
from jarvis.projects.models import ProjectRegistryEntry, ProjectStateApproval
from jarvis.projects.registry import ProjectRegistry


def _project_text() -> str:
    return """# Project
Example Project

## Main Goal
Build safely.
"""


def _state_text(
    current: str = "N-10 — Project State Transaction Engine",
) -> str:
    return f"""# Current Project State

Project: Example Project

Phase:
Core Implementation

## Current Position
Ready.

## Last Completed
N-09 — Project State Read Foundation

## CURRENT TASK
{current}

## Next Logical Step
Implement transactions.

## Blockers
None.
"""


def _tasks_text(
    current: str = "N-10 — Project State Transaction Engine",
) -> str:
    return f"""# Tasks

## NOW

### CURRENT — {current}

Objective:
Implement.

## COMPLETED

### N-09 Project State Read Foundation

Status:
COMPLETED
"""


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PROJECT.md").write_text(_project_text(), encoding="utf-8")
    (root / "STATE.md").write_text(_state_text(), encoding="utf-8")
    (root / "TASKS.md").write_text(_tasks_text(), encoding="utf-8")
    (root / "DECISIONS.md").write_text(
        "# Decisions\n\n## D-001 — One\n\nDecision:\nKeep safe.\n\n"
        "Reason:\nSafety.\n\nStatus:\nACTIVE\n",
        encoding="utf-8",
    )
    (root / "BACKLOG.md").write_text(
        "# Backlog\n\n## FUTURE\n\n- Later\n",
        encoding="utf-8",
    )


def _registry(root: Path) -> ProjectRegistry:
    return ProjectRegistry(
        entries=(
            ProjectRegistryEntry(
                project_id="example",
                project_name="Example Project",
                aliases=("ex",),
                root_path=root.resolve(),
                status="active",
            ),
        )
    )


async def _setup(tmp_path: Path, *, bus: EventBus | None = None):
    root = tmp_path / "project"
    _write_project(root)
    registry = _registry(root)
    entry = registry.resolve_exact("example")
    assert entry is not None
    loaded = load_project_context(entry)
    assert loaded.validation.valid
    assert loaded.snapshot is not None

    db_path = tmp_path / "jarvis.db"
    journal = CandidateJournal(db_path)
    journal.append(
        [
            CandidateFact(
                fact="N-10 is complete and N-11 is now the current task.",
                kind="project",
                subjects=("example",),
                evidence_turn_id="turn-42",
                evidence_excerpt=(
                    "N-10 is complete. Advance the project to N-11 "
                    "Capability & Governance Enforcement."
                ),
                basis="explicit",
            )
        ],
        source_label="realtime:project-memory",
        turn_hash="project-memory-turn",
    )
    candidate = journal.pending()[0]

    queue = MemoryPromotionQueue(db_path)
    item = await queue.enqueue_project_state(
        candidate_id=candidate.id,
        project_id="example",
        source_state_revision=loaded.snapshot.state_revision,
        current_task=loaded.snapshot.current_task,
        relation="execution-status",
    )

    next_task = "N-11 — Capability & Governance Enforcement"
    next_state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine",
        next_task,
    )
    next_tasks = (root / "TASKS.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine",
        next_task,
    )

    async def completion(_request):
        return json.dumps(
            {
                "replacements": {
                    "STATE.md": next_state,
                    "TASKS.md": next_tasks,
                },
                "reason": "Advance after verified N-10 completion.",
                "expected_current_task": next_task,
                "expected_effect": "Promote N-11 as the single CURRENT task.",
            }
        )

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )
    lifecycle = ProjectStateMemoryApprovalLifecycle(
        queue=queue,
        journal=journal,
        registry=registry,
        db_path=db_path,
        planner=planner,
        bus=bus,
    )
    return root, journal, queue, item, lifecycle, db_path


@pytest.mark.asyncio
async def test_prepare_is_durable_read_only_and_claims_queue(tmp_path: Path) -> None:
    root, journal, queue, item, lifecycle, db_path = await _setup(tmp_path)
    before = {
        name: (root / name).read_bytes()
        for name in ("STATE.md", "TASKS.md")
    }

    try:
        stored = await lifecycle.prepare(item.id)
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "proposal-created"
        assert stored.status == "awaiting-approval"
        assert stored.proposal.files_affected == ("STATE.md", "TASKS.md")
        assert all(
            (root / name).read_bytes() == raw
            for name, raw in before.items()
        )
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()

    reopened = ProjectStateProposalStore(db_path)
    try:
        recovered = await reopened.get(item.id)
        assert recovered is not None
        assert recovered.transaction_id == stored.transaction_id
        assert recovered.proposal_digest == stored.proposal_digest
        assert recovered.proposal == stored.proposal
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_exact_approval_commits_and_closes_queue(tmp_path: Path) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)

    try:
        stored = await lifecycle.prepare(item.id)
        result = await lifecycle.approve_and_apply(
            queue_item_id=item.id,
            transaction_id=stored.transaction_id,
            proposal_digest=stored.proposal_digest,
        )
        assert result.status == "COMMITTED"
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "approved"
        persisted = await lifecycle._proposals.get(item.id)
        assert persisted is not None
        assert persisted.status == "applied"
        assert (
            persisted.resulting_state_revision
            == result.resulting_state_revision
        )
        assert "N-11 — Capability & Governance Enforcement" in (
            root / "STATE.md"
        ).read_text(encoding="utf-8")
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_wrong_identity_fails_closed_without_write(tmp_path: Path) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)
    before = (root / "STATE.md").read_bytes()

    try:
        stored = await lifecycle.prepare(item.id)
        with pytest.raises(ProjectStateProposalIdentityError, match="digest"):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=stored.transaction_id,
                proposal_digest="0" * 64,
            )
        with pytest.raises(
            ProjectStateProposalIdentityError,
            match="transaction_id",
        ):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=str(uuid4()),
                proposal_digest=stored.proposal_digest,
            )
        assert (root / "STATE.md").read_bytes() == before
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "proposal-created"
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_reject_is_terminal_and_never_writes(tmp_path: Path) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)
    before = (root / "STATE.md").read_bytes()

    try:
        stored = await lifecycle.prepare(item.id)
        rejected = await lifecycle.reject(
            queue_item_id=item.id,
            transaction_id=stored.transaction_id,
            proposal_digest=stored.proposal_digest,
        )
        assert rejected.status == "rejected"
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "rejected"
        assert (root / "STATE.md").read_bytes() == before
        with pytest.raises(ProjectStateProposalLifecycleError, match="terminal"):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=stored.transaction_id,
                proposal_digest=stored.proposal_digest,
            )
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_stale_canonical_state_fails_and_marks_queue_failed(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)

    try:
        stored = await lifecycle.prepare(item.id)
        (root / "BACKLOG.md").write_text(
            "# Backlog\n\n## FUTURE\n\n- Changed elsewhere\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectStateProposalStaleError, match="revision"):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=stored.transaction_id,
                proposal_digest=stored.proposal_digest,
            )
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "failed"
        assert "N-10 — Project State Transaction Engine" in (
            root / "STATE.md"
        ).read_text(encoding="utf-8")
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_generic_action_approval_does_not_apply_project_state(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    root, journal, queue, item, lifecycle, _ = await _setup(
        tmp_path,
        bus=bus,
    )
    before = (root / "STATE.md").read_bytes()

    try:
        stored = await lifecycle.prepare(item.id)
        await bus.publish(
            ActionApproved(
                trace_id=uuid4(),
                tool_name="write",
                approved_by="auto",
            )
        )
        assert (root / "STATE.md").read_bytes() == before
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "proposal-created"
        persisted = await lifecycle._proposals.get(item.id)
        assert persisted is not None
        assert persisted.status == "awaiting-approval"
        assert persisted.transaction_id == stored.transaction_id
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_committed_manifest_recovers_after_crash_before_queue_finalize(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)

    try:
        stored = await lifecycle.prepare(item.id)
        approved = await lifecycle._proposals.approve_identity(
            queue_item_id=item.id,
            transaction_id=stored.transaction_id,
            proposal_digest_value=stored.proposal_digest,
        )
        direct = await lifecycle._project_store.apply_approved(
            approved.proposal,
            ProjectStateApproval(
                approved.transaction_id,
                approved.proposal_digest,
            ),
        )
        assert direct.status == "COMMITTED"

        recovered = await lifecycle.approve_and_apply(
            queue_item_id=item.id,
            transaction_id=stored.transaction_id,
            proposal_digest=stored.proposal_digest,
        )
        assert recovered.status == "COMMITTED"
        assert (
            recovered.resulting_state_revision
            == direct.resulting_state_revision
        )
        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "approved"
        persisted = await lifecycle._proposals.get(item.id)
        assert persisted is not None
        assert persisted.status == "applied"
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_tampered_durable_proposal_payload_fails_closed(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)
    before = (root / "STATE.md").read_bytes()

    try:
        await lifecycle.prepare(item.id)
        conn = await lifecycle._proposals._ensure_open()
        cur = await conn.execute(
            "SELECT proposal_json FROM memory_project_state_proposals "
            "WHERE queue_item_id = ?",
            (item.id,),
        )
        row = await cur.fetchone()
        await cur.close()
        assert row is not None
        payload = json.loads(str(row["proposal_json"]))
        payload["reason"] = "tampered after persistence"
        await conn.execute(
            "UPDATE memory_project_state_proposals SET proposal_json = ? "
            "WHERE queue_item_id = ?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                item.id,
            ),
        )

        with pytest.raises(ProjectStateProposalIdentityError, match="digest"):
            await lifecycle._proposals.get(item.id)
        assert (root / "STATE.md").read_bytes() == before
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_project_state_proposal_expiry_closes_queue_consistently(
    tmp_path: Path,
) -> None:
    _root, journal, queue, item, lifecycle, _ = await _setup(tmp_path)

    try:
        await lifecycle.prepare(item.id)
        conn = await lifecycle._proposals._ensure_open()
        await conn.execute(
            "UPDATE memory_project_state_proposals SET expires_ms = 0 "
            "WHERE queue_item_id = ?",
            (item.id,),
        )

        expired = await lifecycle._proposals.expire_due()
        assert expired == (item.id,)

        queued = await queue.get(item.id)
        assert queued is not None
        assert queued.status == "expired"

        stored = await lifecycle._proposals.get(item.id)
        assert stored is not None
        assert stored.status == "expired"
    finally:
        await lifecycle.close()
        await queue.close()
        journal.close()
