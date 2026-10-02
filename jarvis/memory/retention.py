"""Retention sweeper for temporary and approval-pending memory."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.core.memory_events import MemoryDeleted, TemporaryMemoryExpired
from jarvis.core.protocols import EventPublisher

from .persistent_approval import PersistentMemoryApprovalQueue
from .promotion_queue import MemoryPromotionQueue
from .recall import RecallStore
from .temporary import TemporaryMemoryItem, TemporaryMemoryStore

if TYPE_CHECKING:
    from .project_state_approval import ProjectStateProposalStore
    from .wiki.journal import CandidateJournal

_MS_PER_DAY = 86_400_000


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
    """Bounded one-shot sweep; scheduling remains the host runtime's job."""

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
        """Review near-expiry context once, then expire what is actually due."""

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
        if due and self._temporary_review is not None:
            attempted = True
            for item in due:
                await self._temporary_review(item)

        # Candidate/conversation extraction is already the normal review path.
        # A single bounded final-review callback lets the existing extractor /
        # consolidator perform one last pass without duplicating model logic.
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

    async def _publish(self, event) -> None:
        if self._events is not None:
            await self._events.publish(event)


__all__ = [
    "MemoryRetentionPolicy",
    "MemoryRetentionSweeper",
    "MemorySweepResult",
]
