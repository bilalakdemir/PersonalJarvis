from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from jarvis.core.bus import EventBus
from jarvis.memory.recall import RecallStore
from jarvis.memory.retention import (
    MemoryRetentionPolicy,
    MemoryRetentionSweeper,
    bootstrap_memory_retention,
)


@dataclass
class _TempItem:
    id: int
    kind: str = "research"
    project_id: str | None = None
    expires_ms: int = 0


class _Recall:
    def __init__(self) -> None:
        self.days = None

    async def prune_older_than(self, days: int) -> int:
        self.days = days
        return 3


class _Temporary:
    def __init__(self) -> None:
        self.deleted: list[int] = []

    async def due_for_review(self, *, within_hours: int, limit: int):
        assert within_hours == 24
        assert limit == 500
        return [_TempItem(7, expires_ms=123)]

    async def expired(self, *, limit: int):
        assert limit == 1000
        return [_TempItem(8)]

    async def delete(self, item_id: int) -> bool:
        self.deleted.append(item_id)
        return True


class _Journal:
    def __init__(self) -> None:
        self.cutoff = None

    def oldest_pending_ms(self):
        return 0

    def delete_older_than(self, cutoff_ms: int):
        self.cutoff = cutoff_ms
        return (11, 12)


class _ApprovalQueue:
    async def expire_due(self, *, limit: int):
        assert limit == 1000
        return (21,)


class _ProjectRoutes:
    async def expire_due(self, *, limit: int):
        assert limit == 1000
        return (31, 32)


class _ProjectProposals:
    async def expire_due(self, *, limit: int):
        assert limit == 1000
        return (41,)


@pytest.mark.asyncio
async def test_retention_sweep_reviews_then_expires_all_temporary_layers() -> None:
    recall = _Recall()
    temporary = _Temporary()
    journal = _Journal()
    reviewed: list[int] = []
    final_reviews: list[bool] = []

    async def review_temp(item) -> None:
        reviewed.append(item.id)

    async def final_review() -> None:
        final_reviews.append(True)

    sweeper = MemoryRetentionSweeper(
        recall=recall,  # type: ignore[arg-type]
        temporary=temporary,  # type: ignore[arg-type]
        journal=journal,  # type: ignore[arg-type]
        persistent_approvals=_ApprovalQueue(),  # type: ignore[arg-type]
        project_routes=_ProjectRoutes(),  # type: ignore[arg-type]
        project_proposals=_ProjectProposals(),  # type: ignore[arg-type]
        policy=MemoryRetentionPolicy(),
        temporary_review=review_temp,
        final_review=final_review,
        clock=lambda: 40 * 86_400.0,
    )

    result = await sweeper.run_once()

    assert reviewed == [7]
    assert final_reviews == [True]
    assert temporary.deleted == [8]
    assert recall.days == 30
    assert journal.cutoff == 10 * 86_400_000
    assert result.temporary_expired == 1
    assert result.conversation_pruned == 3
    assert result.candidates_expired == 2
    assert result.persistent_approvals_expired == 1
    assert result.project_routes_expired == 2
    assert result.project_proposals_expired == 1
    assert result.final_review_attempted is True


def test_retention_policy_rejects_nonpositive_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        MemoryRetentionPolicy(candidate_retention_days=0)


@pytest.mark.asyncio
async def test_bootstrap_memory_retention_starts_and_closes_runtime(
    tmp_path,
) -> None:
    db_path = tmp_path / "jarvis.db"
    recall = RecallStore(db_path)
    await recall.open()
    runtime = await bootstrap_memory_retention(
        db_path=db_path,
        event_publisher=EventBus(),
        recall=recall,
        interval_s=3600,
    )
    try:
        # Let the background task enter its first bounded sweep. It should not
        # block bootstrap or require Wiki integration to exist.
        await asyncio.sleep(0.05)
    finally:
        await runtime.close()
        await recall.close()
