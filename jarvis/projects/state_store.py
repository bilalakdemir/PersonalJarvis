"""Governed Project State Store backed by the existing PersonalJarvis EventBus."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from uuid import UUID, uuid4

from jarvis.core.project_state_events import (
    CurrentTaskChanged,
    ProjectStateApprovalRequired,
    ProjectStateChangeProposed,
    ProjectStateCommitted,
    ProjectStateRolledBack,
    ProjectStateTransactionFailed,
    ProjectStateTransactionRejected,
    ProjectStateTransactionStarted,
)
from jarvis.core.protocols import EventPublisher

from .models import (
    ProjectRegistryEntry,
    ProjectStateApproval,
    ProjectStateChangeProposal,
    ProjectStateTransactionResult,
)
from .proposal import build_project_state_proposal
from .registry import ProjectNotRegisteredError, ProjectRegistry
from .transaction import (
    ProjectStateReplayError,
    ProjectStateRollbackError,
    ProjectStateStaleRevisionError,
    ProjectStateTransactionError,
    apply_project_state_transaction,
)


class ProjectStateApprovalError(ProjectStateTransactionError):
    """The supplied approval is not bound to this exact proposal."""


class ProjectStateStore:
    """Single governed write surface for the five canonical project-state files."""

    def __init__(self, registry: ProjectRegistry, *, bus: EventPublisher | None = None) -> None:
        self._registry = registry
        self._bus = bus
        self._serial_lock = asyncio.Lock()

    def _entry(self, project_id: str) -> ProjectRegistryEntry:
        wanted = project_id.strip().casefold()
        for entry in self._registry.entries:
            if entry.project_id.casefold() == wanted:
                return entry
        raise ProjectNotRegisteredError(f"project_id is not registered: {project_id}")

    async def _publish(self, event) -> None:
        if self._bus is not None:
            await self._bus.publish(event)

    async def propose(
        self,
        project_id: str,
        replacements: Mapping[str, str],
        *,
        reason: str,
        expected_current_task: str | None,
        expected_effect: str,
        trace_id: UUID | None = None,
    ) -> ProjectStateChangeProposal:
        """Prepare an exact read-only proposal and publish approval-visible metadata."""
        entry = self._entry(project_id)
        proposal = await asyncio.to_thread(
            build_project_state_proposal,
            entry,
            replacements,
            reason=reason,
            expected_current_task=expected_current_task,
            expected_effect=expected_effect,
        )
        trace = trace_id or uuid4()
        await self._publish(
            ProjectStateChangeProposed(
                trace_id=trace,
                source_layer="projects",
                project_id=entry.project_id,
                transaction_id=proposal.transaction_id,
                source_state_revision=proposal.source_state_revision,
                files_affected=proposal.files_affected,
                expected_current_task=proposal.expected_current_task,
            )
        )
        await self._publish(
            ProjectStateApprovalRequired(
                trace_id=trace,
                source_layer="projects",
                project_id=entry.project_id,
                transaction_id=proposal.transaction_id,
                proposal_digest=proposal.proposal_digest,
                files_affected=proposal.files_affected,
            )
        )
        return proposal

    async def apply_approved(
        self,
        proposal: ProjectStateChangeProposal,
        approval: ProjectStateApproval,
        *,
        trace_id: UUID | None = None,
    ) -> ProjectStateTransactionResult:
        """Apply only the exact approved proposal; one call is one transaction."""
        if approval.transaction_id != proposal.transaction_id:
            raise ProjectStateApprovalError("approval transaction_id does not match proposal")
        if approval.proposal_digest != proposal.proposal_digest:
            raise ProjectStateApprovalError("approval digest does not match proposal")

        entry = self._entry(proposal.project_id)
        trace = trace_id or uuid4()
        async with self._serial_lock:
            await self._publish(
                ProjectStateTransactionStarted(
                    trace_id=trace,
                    source_layer="projects",
                    project_id=entry.project_id,
                    transaction_id=proposal.transaction_id,
                    files_affected=proposal.files_affected,
                )
            )
            before_task = None
            try:
                from .loader import load_project_context

                before = await asyncio.to_thread(load_project_context, entry)
                if before.snapshot is not None:
                    before_task = before.snapshot.current_task
                result = await asyncio.to_thread(
                    apply_project_state_transaction, entry, proposal
                )
            except (ProjectStateStaleRevisionError, ProjectStateReplayError) as exc:
                await self._publish(
                    ProjectStateTransactionRejected(
                        trace_id=trace,
                        source_layer="projects",
                        project_id=entry.project_id,
                        transaction_id=proposal.transaction_id,
                        reason=str(exc),
                    )
                )
                raise
            except ProjectStateRollbackError as exc:
                await self._publish(
                    ProjectStateTransactionFailed(
                        trace_id=trace,
                        source_layer="projects",
                        project_id=entry.project_id,
                        transaction_id=proposal.transaction_id,
                        error=str(exc),
                    )
                )
                raise
            except Exception as exc:
                await self._publish(
                    ProjectStateTransactionRejected(
                        trace_id=trace,
                        source_layer="projects",
                        project_id=entry.project_id,
                        transaction_id=proposal.transaction_id,
                        reason=str(exc),
                    )
                )
                raise

            if result.status == "COMMITTED":
                from .loader import load_project_context

                after = await asyncio.to_thread(load_project_context, entry)
                current_task = after.snapshot.current_task if after.snapshot is not None else None
                await self._publish(
                    ProjectStateCommitted(
                        trace_id=trace,
                        source_layer="projects",
                        project_id=entry.project_id,
                        transaction_id=proposal.transaction_id,
                        resulting_state_revision=result.resulting_state_revision or "",
                        changed_files=result.changed_files,
                        current_task=current_task,
                    )
                )
                if before_task != current_task:
                    await self._publish(
                        CurrentTaskChanged(
                            trace_id=trace,
                            source_layer="projects",
                            project_id=entry.project_id,
                            transaction_id=proposal.transaction_id,
                            previous_task=before_task,
                            current_task=current_task,
                        )
                    )
            elif result.status == "ROLLED_BACK":
                await self._publish(
                    ProjectStateRolledBack(
                        trace_id=trace,
                        source_layer="projects",
                        project_id=entry.project_id,
                        transaction_id=proposal.transaction_id,
                        changed_files=result.changed_files,
                        error=result.error or "transaction rolled back",
                    )
                )
            return result
