"""Read-only bridge from an *applied* durable approval to PM task recovery.

This module NEVER creates/approves a proposal or commits canonical Markdown.
The existing approval lifecycle (and its authenticated user-facing caller) must
have applied the exact proposal first. A separately issued signed QA receipt
is then required to advance the operational journal, with manifest/revision
checks supplied by ProjectTaskReconciler.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.models import ProjectStateApproval
from jarvis.projects.state_store import ProjectStateStore
from jarvis.projects.task_alignment import ProjectTaskAlignmentReader
from jarvis.projects.task_journal import ProjectTaskJournal
from jarvis.projects.task_reconciliation import (
    ProjectTaskReconciler, ReconciliationBlocked,
)
from jarvis.projects.signed_completion_verifier import (
    CompletionReceiptSource, SignedCompletionVerifier,
)

from .project_state_approval import ProjectStateProposalStore


class ApprovedProjectTaskCompletionBridge:
    """Journal catch-up for a previously user-approved, canonical COMMITTED record.

    The caller MUST already have run authenticated approval via the existing
    approval lifecycle. Reading 'applied' is necessary, not independently
    sufficient to authenticate who approved: decision provenance belongs at
    that lifecycle's UI/identity boundary.
    """

    def __init__(
        self, *, registry: ProjectRegistry,
        proposals: ProjectStateProposalStore,
        journal: ProjectTaskJournal,
        trusted_public_keys: Mapping[str, bytes],
        receipt_source: CompletionReceiptSource,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not trusted_public_keys:
            raise ValueError("no trusted independent reviewer configured")
        self._registry = registry
        self._proposals = proposals
        self._journal = journal
        self._trusted_keys = dict(trusted_public_keys)
        self._source = receipt_source
        self._clock_ms = clock_ms

    async def recover_applied(
        self, *, queue_item_id: int, project_id: str,
        completed_task_id: str, next_task_id: str,
        original_journal_revision: int, actor: str,
    ) -> int:
        """Reconcile only after the exact user-reviewed proposal was applied.

        This operation never calls approve_identity, approve_and_apply,
        apply_approved, or proposal builder. It may ONLY mutate journal rows
        after validating durable approval, external signature, and manifest.
        """
        if type(queue_item_id) is not int or queue_item_id < 1:
            raise ReconciliationBlocked("invalid approval queue item")
        if type(original_journal_revision) is not int or original_journal_revision < 1:
            raise ReconciliationBlocked("invalid original journal revision")
        if not actor or not actor.strip():
            raise ReconciliationBlocked("missing task actor")
        record = await self._proposals.get(queue_item_id)
        if record is None:
            raise ReconciliationBlocked("durable approval record missing")
        if (
            record.queue_item_id != queue_item_id
            or record.project_id != project_id
            or record.proposal.project_id != project_id
            or record.transaction_id != record.proposal.transaction_id
            or record.proposal_digest != record.proposal.proposal_digest
            or record.source_state_revision != record.proposal.source_state_revision
            or record.status != "applied"
            or type(record.decision_ms) is not int
            or type(record.applied_ms) is not int
            or record.decision_ms <= 0
            or record.applied_ms < record.decision_ms
            or not record.resulting_state_revision
        ):
            raise ReconciliationBlocked("proposal was not durably approved and applied")

        alignment = ProjectTaskAlignmentReader(
            self._registry, self._journal,
        ).read(project_id)
        if (
            alignment.canonical_revision != record.resulting_state_revision
            or alignment.canonical_task_id != next_task_id
            or alignment.status == "INVALID_CANONICAL"
        ):
            raise ReconciliationBlocked("canonical state differs from applied approval")

        verifier = SignedCompletionVerifier(
            proposal_digest=record.proposal_digest,
            trusted_public_keys=self._trusted_keys,
            source=self._source,
            clock_ms=self._clock_ms,
        )
        reconciler = ProjectTaskReconciler(
            self._registry, ProjectStateStore(self._registry),
            self._journal, verifier,
        )
        # The exact durable proposal identifies the canonical commit; the
        # signer attests the *old* journal revision and task independently.
        return reconciler.recover(
            project_id, completed_task_id, next_task_id,
            record.proposal,
            ProjectStateApproval(
                transaction_id=record.transaction_id,
                proposal_digest=record.proposal_digest,
            ),
            actor=actor,
            original_journal_revision=original_journal_revision,
        )
