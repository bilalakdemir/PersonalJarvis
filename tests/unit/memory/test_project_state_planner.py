from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.memory.project_state_planner import (
    ProjectStateMemoryPlanner,
    ProjectStateMemoryPlannerError,
    ProjectStateMemoryPlannerStaleError,
    ProjectStateMemoryPlannerUnavailableError,
)
from jarvis.memory.project_state_proposal import ProjectMemoryProposalComposer
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
    current: str = "N-14 — Governed Memory Layer",
) -> str:
    return f"""# Current Project State

Project: Example Project

Phase:
Core Implementation

## Current Position
Planner pending.

## Last Completed
N-13 — Project Delegation Bridge

## CURRENT TASK
{current}

## Next Logical Step
Implement the governed memory planner.

## Blockers
None.
"""


def _tasks_text(
    current: str = "N-14 — Governed Memory Layer",
) -> str:
    return f"""# Tasks

## NOW

### CURRENT — {current}

Objective:
Implement governed memory.

## COMPLETED

### N-13 Project Delegation Bridge

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
    evidence_turn_id: str = "turn-1",
    evidence_excerpt: str = "N-14 is complete. Move to N-15.",
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
                fact="The current milestone is complete and N-15 is next.",
                kind="project",
                subjects=("example",),
                evidence_turn_id=evidence_turn_id,
                evidence_excerpt=evidence_excerpt,
                basis=basis,
            )
        ],
        source_label="realtime:project-memory",
        turn_hash="planner-test-turn",
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
    return root, registry, journal, queue, item


def _valid_response(root: Path) -> str:
    next_task = "N-15 — HUD Semantic Backend"
    state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-14 — Governed Memory Layer",
        next_task,
    )
    tasks = (root / "TASKS.md").read_text(encoding="utf-8").replace(
        "N-14 — Governed Memory Layer",
        next_task,
    )
    return json.dumps(
        {
            "replacements": {
                "STATE.md": state,
                "TASKS.md": tasks,
            },
            "reason": "Advance after explicit milestone completion.",
            "expected_current_task": next_task,
            "expected_effect": "Complete N-14 and promote N-15.",
        }
    )


@pytest.mark.asyncio
async def test_valid_plan_composes_exact_proposal_without_writing(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)
    before = {
        name: (root / name).read_bytes()
        for name in (
            "PROJECT.md",
            "STATE.md",
            "TASKS.md",
            "DECISIONS.md",
            "BACKLOG.md",
        )
    }

    async def completion(_request) -> str:
        return _valid_response(root)

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )
    composer = ProjectMemoryProposalComposer(
        queue=queue,
        journal=journal,
        registry=registry,
    )

    try:
        plan = await planner.plan(item.id)
        proposal = await composer.compose(item.id, plan)
    finally:
        await queue.close()
        journal.close()

    assert set(plan.replacements) == {"STATE.md", "TASKS.md"}
    assert proposal.project_id == "example"
    assert proposal.source_state_revision == item.source_state_revision
    assert proposal.files_affected == ("STATE.md", "TASKS.md")
    assert all(
        (root / name).read_bytes() == raw
        for name, raw in before.items()
    )


@pytest.mark.asyncio
async def test_malformed_structured_output_fails_closed(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)

    async def completion(_request) -> str:
        return "not-json"

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerError,
            match="no JSON object",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_planner_cannot_expand_to_unauthorized_file(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)

    async def completion(_request) -> str:
        return json.dumps(
            {
                "replacements": {
                    "PROJECT.md": (
                        root / "PROJECT.md"
                    ).read_text(encoding="utf-8"),
                },
                "reason": "Unsafe scope expansion.",
                "expected_current_task": "N-14 — Governed Memory Layer",
                "expected_effect": "Should fail.",
            }
        )

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerError,
            match="unauthorized project-state files",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_canonical_change_during_provider_call_is_stale(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)

    async def completion(_request) -> str:
        response = _valid_response(root)
        (root / "BACKLOG.md").write_text(
            "# Backlog\n\n## FUTURE\n\n- Changed concurrently\n",
            encoding="utf-8",
        )
        return response

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerStaleError,
            match="revision",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_inferred_candidate_never_reaches_provider(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(
        tmp_path,
        basis="inferred",
    )
    called = False

    async def completion(_request) -> str:
        nonlocal called
        called = True
        return _valid_response(root)

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerError,
            match="explicit evidence",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()

    assert called is False


@pytest.mark.asyncio
async def test_missing_evidence_excerpt_never_reaches_provider(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(
        tmp_path,
        evidence_excerpt="",
    )
    called = False

    async def completion(_request) -> str:
        nonlocal called
        called = True
        return _valid_response(root)

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerError,
            match="evidence excerpt",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()

    assert called is False


@pytest.mark.asyncio
async def test_provider_failure_is_reported_as_unavailable(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)

    async def completion(_request) -> str:
        raise RuntimeError("provider failed")

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )

    try:
        with pytest.raises(
            ProjectStateMemoryPlannerUnavailableError,
            match="provider failed",
        ):
            await planner.plan(item.id)
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_composer_remains_final_canonical_validation_gate(
    tmp_path: Path,
) -> None:
    root, registry, journal, queue, item = await _setup(tmp_path)
    next_task = "N-15 — HUD Semantic Backend"
    state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-14 — Governed Memory Layer",
        next_task,
    )

    async def completion(_request) -> str:
        return json.dumps(
            {
                "replacements": {"STATE.md": state},
                "reason": "One-sided task advance.",
                "expected_current_task": next_task,
                "expected_effect": "Should fail canonical validation.",
            }
        )

    planner = ProjectStateMemoryPlanner(
        queue=queue,
        journal=journal,
        registry=registry,
        completion=completion,
    )
    composer = ProjectMemoryProposalComposer(
        queue=queue,
        journal=journal,
        registry=registry,
    )

    try:
        plan = await planner.plan(item.id)
        with pytest.raises(ProjectStateProposalError):
            await composer.compose(item.id, plan)
    finally:
        await queue.close()
        journal.close()
