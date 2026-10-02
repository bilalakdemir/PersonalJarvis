from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.project_state_events import ProjectStateMemoryProposalRequested
from jarvis.memory.promotion_queue import (
    DEFAULT_PROMOTION_RETENTION_DAYS,
    MemoryPromotionQueue,
    attach_project_state_route_queue,
)

_DAY_S = 86_400


@pytest.mark.asyncio
async def test_same_project_route_is_deduped_durably(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    queue = MemoryPromotionQueue(db_path)

    try:
        first = await queue.enqueue_project_state(
            candidate_id=7,
            project_id="personal-jarvis",
            source_state_revision="a" * 64,
            current_task="N-14 — Governed Memory Layer",
            relation="execution-status",
        )
        second = await queue.enqueue_project_state(
            candidate_id=7,
            project_id="personal-jarvis",
            source_state_revision="a" * 64,
            current_task="N-14 — Governed Memory Layer",
            relation="execution-status",
        )

        pending = await queue.pending()
    finally:
        await queue.close()

    assert first.id == second.id
    assert [item.id for item in pending] == [first.id]
    assert first.authority == "project-state"
    assert first.project_id == "personal-jarvis"


@pytest.mark.asyncio
async def test_new_project_revision_creates_new_queue_item(tmp_path: Path) -> None:
    queue = MemoryPromotionQueue(tmp_path / "jarvis.db")

    try:
        first = await queue.enqueue_project_state(
            candidate_id=7,
            project_id="personal-jarvis",
            source_state_revision="a" * 64,
            current_task="N-14 — Governed Memory Layer",
            relation="execution-status",
        )
        second = await queue.enqueue_project_state(
            candidate_id=7,
            project_id="personal-jarvis",
            source_state_revision="b" * 64,
            current_task="N-14 — Governed Memory Layer",
            relation="execution-status",
        )
        pending = await queue.pending()
    finally:
        await queue.close()

    assert first.id != second.id
    assert [item.source_state_revision for item in pending] == [
        "a" * 64,
        "b" * 64,
    ]


@pytest.mark.asyncio
async def test_queue_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"

    first = MemoryPromotionQueue(db_path)
    try:
        item = await first.enqueue_project_state(
            candidate_id=11,
            project_id="personal-jarvis",
            source_state_revision="c" * 64,
            current_task="N-14 — Governed Memory Layer",
            relation="execution-status",
        )
    finally:
        await first.close()

    reopened = MemoryPromotionQueue(db_path)
    try:
        pending = await reopened.pending()
    finally:
        await reopened.close()

    assert [row.id for row in pending] == [item.id]
    assert pending[0].candidate_id == 11


@pytest.mark.asyncio
async def test_default_project_route_expiry_is_thirty_days(tmp_path: Path) -> None:
    now = [1_700_000_000.0]
    queue = MemoryPromotionQueue(
        tmp_path / "jarvis.db",
        clock=lambda: now[0],
    )

    try:
        item = await queue.enqueue_project_state(
            candidate_id=13,
            project_id="personal-jarvis",
            source_state_revision="d" * 64,
            current_task=None,
            relation="execution-status",
        )

        assert item.expires_ms - item.created_ms == (
            DEFAULT_PROMOTION_RETENTION_DAYS * _DAY_S * 1000
        )
        assert len(await queue.pending()) == 1

        now[0] += (DEFAULT_PROMOTION_RETENTION_DAYS + 1) * _DAY_S
        assert await queue.pending() == []
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_event_bus_route_is_idempotent_and_cleanup_unsubscribes(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    queue, cleanup = await attach_project_state_route_queue(
        bus=bus,
        db_path=tmp_path / "jarvis.db",
    )
    event = ProjectStateMemoryProposalRequested(
        source_layer="memory",
        project_id="personal-jarvis",
        candidate_id=17,
        source_state_revision="e" * 64,
        current_task="N-14 — Governed Memory Layer",
        relation="execution-status",
    )

    try:
        await bus.publish(event)
        await bus.publish(event)
        pending = await queue.pending()
        assert len(pending) == 1
        assert pending[0].candidate_id == 17
    finally:
        await cleanup()

    await bus.publish(event)

    reopened = MemoryPromotionQueue(tmp_path / "jarvis.db")
    try:
        assert len(await reopened.pending()) == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_secret_shaped_queue_metadata_is_rejected(tmp_path: Path) -> None:
    queue = MemoryPromotionQueue(tmp_path / "jarvis.db")

    try:
        with pytest.raises(ValueError, match="project_id contains secret-shaped"):
            await queue.enqueue_project_state(
                candidate_id=19,
                project_id="sk-proj-" + ("x" * 30),
                source_state_revision="f" * 64,
                current_task=None,
                relation="execution-status",
            )
    finally:
        await queue.close()
