"""Compose exact, reviewable project-state proposals from governed memory."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass

from jarvis.projects.loader import load_project_context
from jarvis.projects.models import ProjectStateChangeProposal
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.state_store import ProjectStateStore

from .promotion_queue import MemoryPromotionQueue
from .wiki.journal import CandidateJournal

_ALLOWED_EXECUTION_STATUS_FILES = frozenset({"STATE.md", "TASKS.md"})


class ProjectMemoryProposalError(ValueError):
    """A queued project-memory candidate cannot become a safe proposal."""


class ProjectMemoryProposalStaleError(ProjectMemoryProposalError):
    """The queued candidate is bound to an older canonical project revision."""


@dataclass(frozen=True, slots=True)
class ProjectStateMemoryEditPlan:
    """Exact replacement plan supplied by a later governed planner."""

    replacements: Mapping[str, str]
    reason: str
    expected_current_task: str | None
    expected_effect: str


class ProjectMemoryProposalComposer:
    """Bind a queued project-memory candidate to an exact validated proposal."""

    def __init__(
        self,
        *,
        queue: MemoryPromotionQueue,
        journal: CandidateJournal,
        registry: ProjectRegistry,
    ) -> None:
        self._queue = queue
        self._journal = journal
        self._registry = registry
        # Intentionally no EventBus in N-14F. Proposal creation must remain
        # side-effect free until the approval lifecycle is wired explicitly.
        self._store = ProjectStateStore(registry)

    async def compose(
        self,
        queue_item_id: int,
        plan: ProjectStateMemoryEditPlan,
    ) -> ProjectStateChangeProposal:
        """Return one exact proposal without mutating canonical project files."""

        item = await self._queue.get(queue_item_id)
        if item is None:
            raise ProjectMemoryProposalError("promotion queue item not found")
        if item.status != "pending":
            raise ProjectMemoryProposalError(
                f"promotion queue item is not pending: {item.status}"
            )
        if item.expires_ms <= int(time.time() * 1000):
            raise ProjectMemoryProposalError("promotion queue item has expired")
        if item.authority != "project-state":
            raise ProjectMemoryProposalError(
                "promotion queue item is not project-state authority"
            )
        if item.relation != "execution-status":
            raise ProjectMemoryProposalError(
                f"unsupported project-memory relation: {item.relation}"
            )
        if not item.project_id:
            raise ProjectMemoryProposalError(
                "project-state queue item has no project_id"
            )

        candidate = self._journal.get(item.candidate_id)
        if candidate is None:
            raise ProjectMemoryProposalError("journal candidate not found")
        if candidate.status != "pending":
            raise ProjectMemoryProposalError(
                f"journal candidate is not pending: {candidate.status}"
            )
        if candidate.kind != "project":
            raise ProjectMemoryProposalError(
                f"journal candidate is not project-scoped: {candidate.kind}"
            )
        if candidate.basis != "explicit":
            raise ProjectMemoryProposalError(
                f"project proposal requires explicit evidence: {candidate.basis}"
            )

        entry = self._registry.resolve_exact(item.project_id)
        if entry is None or entry.project_id.casefold() != item.project_id.casefold():
            raise ProjectMemoryProposalError(
                f"queued project is not registered canonically: {item.project_id}"
            )

        loaded = await asyncio.to_thread(load_project_context, entry)
        if not loaded.validation.valid or loaded.snapshot is None:
            raise ProjectMemoryProposalError(
                "canonical project state is unavailable or invalid"
            )
        snapshot = loaded.snapshot
        if snapshot.state_revision != item.source_state_revision:
            raise ProjectMemoryProposalStaleError(
                "queued project revision no longer matches canonical state"
            )
        if snapshot.current_task != item.current_task:
            raise ProjectMemoryProposalStaleError(
                "queued CURRENT task no longer matches canonical state"
            )

        replacements = dict(plan.replacements)
        if not replacements:
            raise ProjectMemoryProposalError(
                "project-memory edit plan must contain replacements"
            )
        unsupported = sorted(
            set(replacements) - _ALLOWED_EXECUTION_STATUS_FILES
        )
        if unsupported:
            raise ProjectMemoryProposalError(
                "execution-status proposal may only replace STATE.md/TASKS.md; "
                f"unsupported: {', '.join(unsupported)}"
            )
        if any(not isinstance(value, str) for value in replacements.values()):
            raise ProjectMemoryProposalError(
                "project-memory replacements must be text"
            )

        proposal = await self._store.propose(
            item.project_id,
            replacements,
            reason=plan.reason,
            expected_current_task=plan.expected_current_task,
            expected_effect=plan.expected_effect,
        )

        # Close the check/build race: ProjectStateStore.propose() re-reads the
        # exact canonical files. If they changed after the preflight read, the
        # returned proposal is rejected here and no write has occurred.
        if proposal.source_state_revision != item.source_state_revision:
            raise ProjectMemoryProposalStaleError(
                "canonical state changed while composing the proposal"
            )

        return proposal


__all__ = [
    "ProjectMemoryProposalComposer",
    "ProjectMemoryProposalError",
    "ProjectMemoryProposalStaleError",
    "ProjectStateMemoryEditPlan",
]
