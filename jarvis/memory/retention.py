"""Retention lifecycle for temporary and approval-pending memory."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.memory_events import (
    MemoryDeleted,
    MemoryPreExpiryReviewRequired,
    TemporaryMemoryExpired,
)
from jarvis.core.protocols import EventPublisher

from .persistent_approval import PersistentMemoryApprovalQueue
from .promotion_queue import MemoryPromotionQueue
from .recall import RecallStore
from .temporary import TemporaryMemoryItem, TemporaryMemoryStore

if TYPE_CHECKING:
    from .project_state_approval import ProjectStateProposalStore
    from .wiki.journal import CandidateJournal

log = logging.getLogger(__name__)

_MS_PER_DAY = 86_400_000
DEFAULT_SWEEP_INTERVAL_S = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class MemoryRetentionPolicy:
    conversation_retention_days: int = 30
    temporary_memory_retention_days: int = 30
    candidate_retention_days: int = 30
    promotion_approval_retention_days: int = 30
    pre_expiry_review_hours: int = 24
    active_context_idle_hours: int = 24

    def __post_init__(self) -> None:
        values = (
            self.conversation_retention_days,
            self.temporary_memory_retention_days,
            self.candidate_retention_days,
            self.promotion_approval_retention_days,
            self.pre_expiry_review_hours,
            self.active_context_idle_hours,
        )
        if any(int(value) <= 0 for value in values):
            raise ValueError("memory retention settings must be positive")


@dataclass(frozen=True, slots=True)
class MemorySweepResult:
    temporary_expired: int = 0
    conversation_pruned: int = 0
    candidates_expired: int = 0
    persistent_approvals_expired: int = 0
    project_routes_expired: int = 0
    project_proposals_expired: int = 0
    final_review_attempted: bool = False


FinalReviewHook = Callable[[], Awaitable[None]]
TemporaryReviewHook = Callable[[TemporaryMemoryItem], Awaitable[None]]


class MemoryRetentionSweeper:
    """One bounded retention pass over Jarvis' governed-memory stores."""

    def __init__(
        self,
        *,
        recall: RecallStore,
        temporary: TemporaryMemoryStore,
        journal: CandidateJournal,
        persistent_approvals: PersistentMemoryApprovalQueue,
        project_routes: MemoryPromotionQueue,
        project_proposals: ProjectStateProposalStore | None = None,
        policy: MemoryRetentionPolicy | None = None,
        event_publisher: EventPublisher | None = None,
        final_review: FinalReviewHook | None = None,
        temporary_review: TemporaryReviewHook | None = None,
        clock=time.time,
    ) -> None:
        self._recall = recall
        self._temporary = temporary
        self._journal = journal
        self._persistent_approvals = persistent_approvals
        self._project_routes = project_routes
        self._project_proposals = project_proposals
        self._policy = policy or MemoryRetentionPolicy()
        self._events = event_publisher
        self._final_review = final_review
        self._temporary_review = temporary_review
        self._clock = clock

    async def run_once(self) -> MemorySweepResult:
        """Review near-expiry context first, then expire only what is actually due."""

        reviewed = await self._run_pre_expiry_review()

        expired_items = await self._temporary.expired(limit=1000)
        for item in expired_items:
            if await self._temporary.delete(item.id):
                await self._publish(
                    TemporaryMemoryExpired(
                        source_layer="memory",
                        item_id=item.id,
                        kind=item.kind,
                        project_id=item.project_id,
                    )
                )
                await self._publish(
                    MemoryDeleted(
                        source_layer="memory",
                        memory_kind="temporary",
                        memory_id=str(item.id),
                        reason="retention-expired",
                    )
                )

        conversation_pruned = await self._recall.prune_older_than(
            self._policy.conversation_retention_days
        )

        # Expire governance work before deleting its candidate journal row so
        # the durable decision state remains observable until its own lifecycle
        # closes. Candidate deletion may cascade dependent approval rows.
        persistent_expired = await self._persistent_approvals.expire_due(
            limit=1000
        )
        project_routes_expired = await self._project_routes.expire_due(
            limit=1000
        )
        project_proposals_expired: tuple[int, ...] = ()
        if self._project_proposals is not None:
            project_proposals_expired = (
                await self._project_proposals.expire_due(limit=1000)
            )

        cutoff_ms = int(self._clock() * 1000) - (
            self._policy.candidate_retention_days * _MS_PER_DAY
        )
        candidate_ids = await asyncio.to_thread(
            self._journal.delete_older_than,
            cutoff_ms,
        )
        for candidate_id in candidate_ids:
            await self._publish(
                MemoryDeleted(
                    source_layer="memory",
                    memory_kind="candidate",
                    memory_id=str(candidate_id),
                    reason="retention-expired",
                )
            )

        return MemorySweepResult(
            temporary_expired=len(expired_items),
            conversation_pruned=max(0, int(conversation_pruned)),
            candidates_expired=len(candidate_ids),
            persistent_approvals_expired=len(persistent_expired),
            project_routes_expired=len(project_routes_expired),
            project_proposals_expired=len(project_proposals_expired),
            final_review_attempted=reviewed,
        )

    async def _run_pre_expiry_review(self) -> bool:
        attempted = False
        due = await self._temporary.due_for_review(
            within_hours=self._policy.pre_expiry_review_hours,
            limit=500,
        )
        for item in due:
            attempted = True
            await self._publish(
                MemoryPreExpiryReviewRequired(
                    source_layer="memory",
                    item_id=item.id,
                    kind=item.kind,
                    project_id=item.project_id,
                    expires_ms=item.expires_ms,
                )
            )
            if self._temporary_review is not None:
                await self._temporary_review(item)

        # Candidate extraction/consolidation is already the normal durable
        # review path. Give that existing pipeline one bounded final chance
        # before an old pending journal row becomes retention-eligible.
        if self._final_review is not None:
            oldest_pending = await asyncio.to_thread(
                self._journal.oldest_pending_ms
            )
            now_ms = int(self._clock() * 1000)
            review_cutoff = now_ms - (
                self._policy.candidate_retention_days * _MS_PER_DAY
            ) + self._policy.pre_expiry_review_hours * 3_600_000
            if oldest_pending is not None and oldest_pending <= review_cutoff:
                attempted = True
                await self._final_review()
        return attempted

    async def _publish(self, event: Any) -> None:
        if self._events is not None:
            await self._events.publish(event)


class MemoryRetentionRuntime:
    """Own the periodic retention task and the stores opened for it."""

    def __init__(
        self,
        *,
        sweeper: MemoryRetentionSweeper,
        closeables: tuple[Any, ...],
        journal: CandidateJournal,
        interval_s: float = DEFAULT_SWEEP_INTERVAL_S,
    ) -> None:
        if float(interval_s) <= 0:
            raise ValueError("interval_s must be positive")
        self._sweeper = sweeper
        self._closeables = closeables
        self._journal = journal
        self._interval_s = float(interval_s)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run_loop(),
            name="memory-retention-sweeper",
        )

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                log.debug("Memory retention loop cancelled during shutdown")
        try:
            await asyncio.to_thread(self._journal.close)
        except Exception as exc:  # noqa: BLE001
            log.debug("Memory retention journal close failed: %s", exc)
        for store in self._closeables:
            close = getattr(store, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "Memory retention store close failed (%s): %s",
                    type(store).__name__,
                    exc,
                )

    async def _run_loop(self) -> None:
        while True:
            try:
                result = await self._sweeper.run_once()
                log.debug("Memory retention sweep completed: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Memory retention sweep failed: %s", exc)
            await asyncio.sleep(self._interval_s)


async def bootstrap_memory_retention(
    *,
    db_path: str | Path,
    event_publisher: EventPublisher | None = None,
    recall: RecallStore | None = None,
    policy: MemoryRetentionPolicy | None = None,
    final_review: FinalReviewHook | None = None,
    temporary_review: TemporaryReviewHook | None = None,
    interval_s: float = DEFAULT_SWEEP_INTERVAL_S,
) -> MemoryRetentionRuntime:
    """Open the real governed-memory stores and start periodic retention.

    The BrainManager-owned RecallStore is reused when supplied. All other
    handles are dedicated lightweight SQLite connections and are owned by the
    returned runtime.
    """

    from .project_state_approval import ProjectStateProposalStore
    from .wiki.journal import CandidateJournal

    db = Path(db_path)
    owned_recall = recall is None
    recall_store = recall or RecallStore(db)
    await recall_store.open()

    temporary = TemporaryMemoryStore(db)
    persistent = PersistentMemoryApprovalQueue(db, bus=event_publisher)
    routes = MemoryPromotionQueue(db)
    proposals = ProjectStateProposalStore(db)
    await temporary.open()
    await persistent.open()
    await routes.open()
    await proposals.open()
    journal = CandidateJournal(db)

    closeables: list[Any] = [
        proposals,
        routes,
        persistent,
        temporary,
    ]
    if owned_recall:
        closeables.append(recall_store)

    sweeper = MemoryRetentionSweeper(
        recall=recall_store,
        temporary=temporary,
        journal=journal,
        persistent_approvals=persistent,
        project_routes=routes,
        project_proposals=proposals,
        policy=policy,
        event_publisher=event_publisher,
        final_review=final_review,
        temporary_review=temporary_review,
    )
    runtime = MemoryRetentionRuntime(
        sweeper=sweeper,
        closeables=tuple(closeables),
        journal=journal,
        interval_s=interval_s,
    )
    runtime.start()
    return runtime


__all__ = [
    "DEFAULT_SWEEP_INTERVAL_S",
    "MemoryRetentionPolicy",
    "MemoryRetentionRuntime",
    "MemoryRetentionSweeper",
    "MemorySweepResult",
    "bootstrap_memory_retention",
]
