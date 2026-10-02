from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.project_state_proposal import (
    ProjectMemoryProposalComposer,
    ProjectMemoryProposalError,
    ProjectMemoryProposalStaleError,
    ProjectStateMemoryEditPlan,
)
from jarvis.memory.promotion_queue import MemoryPromotionQueue
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal
from jarvis.projects.loader import load_project_context
from jarvis.projects.models import ProjectRegistryEntry
from jarvis.projects.proposal import ProjectStateProposalError
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


def _decisions_text() -> str:
    return """# Decisions

## D-001 — One

Decision:
Keep safe.

Reason:
Safety.

Status:
ACTIVE
"""


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PROJECT.md").write_text(_project_text(), encoding="utf-8")
    (root / "STATE.md").write_text(_state_text(), encoding="utf-8")
    (root / "TASKS.md").write_text(_tasks_text(), encoding="utf-8")
    (root / "DECISIONS.md").write_text(_decisions_text(), encoding="utf-8")
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


async def _setup(
    tmp_path: Path,
    *,
    basis: str = "explicit",
    candidate_id_override: int | None = None,
):
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
                fact="The current project milestone is complete.",
                kind="project",
                subjects=("example",),
                basis=basis,
            )
        ],
        source_label="realtime:project-memory",
        turn_hash="project-memory-turn",
    )
    candidate = journal.pending()[0]
    candidate_id = (
        candidate.id
        if candidate_id_override is None
        else candidate_id_override
    )

    queue = MemoryPromotionQueue(db_path)
    item = await queue.enqueue_project_state(
        candidate_id=candidate_id,
        project_id="example",
        source_state_revision=loaded.snapshot.state_revision,
        current_task=loaded.snapshot.current_task,
        relation="execution-status",
    )
    composer = ProjectMemoryProposalComposer(
        queue=queue,
        journal=journal,
        registry=registry,
    )
    return root, journal, queue, item, composer


def _valid_plan(root: Path) -> ProjectStateMemoryEditPlan:
    next_task = "N-11 — Capability & Governance Enforcement"
    state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine",
        next_task,
    )
    tasks = (root / "TASKS.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine",
        next_task,
    )
    return ProjectStateMemoryEditPlan(
        replacements={
            "STATE.md": state,
            "TASKS.md": tasks,
        },
        reason="Advance after verified milestone completion.",
        expected_current_task=next_task,
        expected_effect="Complete N-10 and promote N-11.",
    )


@pytest.mark.asyncio
async def test_valid_candidate_produces_exact_read_only_proposal(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(tmp_path)
    before = {
        name: (root / name).read_bytes()
        for name in ("STATE.md", "TASKS.md")
    }

    try:
        proposal = await composer.compose(item.id, _valid_plan(root))
    finally:
        await queue.close()
        journal.close()

    assert proposal.project_id == "example"
    assert proposal.source_state_revision == item.source_state_revision
    assert proposal.files_affected == ("STATE.md", "TASKS.md")
    assert proposal.proposal_digest
    assert all(change.unified_diff for change in proposal.changes)
    assert all(
        (root / name).read_bytes() == raw
        for name, raw in before.items()
    )


@pytest.mark.asyncio
async def test_stale_project_revision_rejects_before_proposal(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(tmp_path)
    (root / "BACKLOG.md").write_text(
        "# Backlog\n\n## FUTURE\n\n- Changed elsewhere\n",
        encoding="utf-8",
    )

    try:
        with pytest.raises(
            ProjectMemoryProposalStaleError,
            match="revision",
        ):
            await composer.compose(item.id, _valid_plan(root))
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_inferred_project_candidate_cannot_create_proposal(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(
        tmp_path,
        basis="inferred",
    )

    try:
        with pytest.raises(
            ProjectMemoryProposalError,
            match="requires explicit evidence",
        ):
            await composer.compose(item.id, _valid_plan(root))
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_missing_candidate_cannot_create_proposal(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(
        tmp_path,
        candidate_id_override=999,
    )

    try:
        with pytest.raises(
            ProjectMemoryProposalError,
            match="candidate not found",
        ):
            await composer.compose(item.id, _valid_plan(root))
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_non_pending_candidate_cannot_create_proposal(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(tmp_path)
    journal.mark([item.candidate_id], status="skipped")

    try:
        with pytest.raises(
            ProjectMemoryProposalError,
            match="candidate is not pending",
        ):
            await composer.compose(item.id, _valid_plan(root))
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_execution_status_plan_cannot_expand_file_scope(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(tmp_path)
    plan = ProjectStateMemoryEditPlan(
        replacements={
            "PROJECT.md": (root / "PROJECT.md").read_text(encoding="utf-8"),
        },
        reason="Do not allow scope expansion.",
        expected_current_task="N-10 — Project State Transaction Engine",
        expected_effect="No scope expansion.",
    )

    try:
        with pytest.raises(
            ProjectMemoryProposalError,
            match="STATE.md/TASKS.md",
        ):
            await composer.compose(item.id, plan)
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_invalid_resulting_project_state_is_rejected_by_validator(
    tmp_path: Path,
) -> None:
    root, journal, queue, item, composer = await _setup(tmp_path)
    next_task = "N-11 — Capability & Governance Enforcement"
    state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine",
        next_task,
    )
    plan = ProjectStateMemoryEditPlan(
        replacements={"STATE.md": state},
        reason="Invalid one-sided CURRENT change.",
        expected_current_task=next_task,
        expected_effect="Should be rejected by canonical validation.",
    )

    try:
        with pytest.raises(ProjectStateProposalError):
            await composer.compose(item.id, plan)
    finally:
        await queue.close()
        journal.close()
