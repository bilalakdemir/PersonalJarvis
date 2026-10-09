"""Fail-closed canonical commit reconciliation for project task journals."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .proposal import proposal_digest

from .models import ProjectStateApproval, ProjectStateChangeProposal
from .registry import ProjectRegistry
from .state_store import ProjectStateStore
from .task_alignment import ProjectTaskAlignmentReader
from .task_journal import ProjectTaskJournal, TaskJournalError


class ReconciliationBlocked(TaskJournalError):
    """Missing or mismatched approval, verification, or commit proof."""


@dataclass(frozen=True)
class CompletionEvidence:
    project_id: str
    task_id: str
    canonical_revision: str
    journal_revision: int
    verifier_id: str
    evidence_ref: str
    verified: bool


class CompletionVerifier(Protocol):
    def verify(self, *, project_id: str, task_id: str,
               canonical_revision: str, journal_revision: int
               ) -> CompletionEvidence | None: ...


class ProjectTaskReconciler:
    """Uses the existing canonical writer; never writes project Markdown."""

    def __init__(self, registry: ProjectRegistry, store: ProjectStateStore,
                 journal: ProjectTaskJournal, verifier: CompletionVerifier):
        self.registry, self.store = registry, store
        self.journal, self.verifier = journal, verifier
        self.alignment = ProjectTaskAlignmentReader(registry, journal)

    def _entry(self, project_id: str):
        entry = self.registry.resolve_exact(project_id)
        if entry is None or entry.project_id != project_id or entry.status != "active":
            raise ReconciliationBlocked("project scope is not active and exact")
        return entry

    @staticmethod
    def _id(label: str | None) -> str:
        import re
        value = label.split(maxsplit=1)[0] if label else ""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
            raise ReconciliationBlocked("invalid task ID")
        return value

    def _check_approval(self, project_id: str, completed: str, next_task: str,
                        proposal: ProjectStateChangeProposal,
                        approval: ProjectStateApproval):
        self._entry(project_id)
        if proposal.proposal_digest != proposal_digest(proposal) or str(UUID(proposal.transaction_id)) != proposal.transaction_id:
            raise ReconciliationBlocked('proposal digest invalid')
        if (proposal.project_id != project_id
            or approval.transaction_id != proposal.transaction_id
            or approval.proposal_digest != proposal.proposal_digest
            or self._id(proposal.expected_current_task) != next_task
            or completed == next_task
            or not {"STATE.md", "TASKS.md"}.issubset(proposal.files_affected)):
            raise ReconciliationBlocked("approval or exact task transition mismatch")

    def _attest(self, project_id: str, completed: str, revision: str,
                journal_revision: int, actor: str) -> CompletionEvidence:
        evidence = self.verifier.verify(
            project_id=project_id, task_id=completed,
            canonical_revision=revision, journal_revision=journal_revision,
        )
        if (evidence is None or not evidence.verified
            or evidence.project_id != project_id or evidence.task_id != completed
            or evidence.canonical_revision != revision
            or evidence.journal_revision != journal_revision
            or not evidence.verifier_id.strip() or evidence.verifier_id == actor
            or not evidence.evidence_ref.strip() or len(evidence.evidence_ref) > 320):
            raise ReconciliationBlocked("independent verification missing or mismatched")
        return evidence

    def _manifest_revision(self, project_id: str,
                           proposal: ProjectStateChangeProposal) -> str:
        root = self._entry(project_id).root_path.resolve(strict=False)
        base = root / ".jarvis"
        backups = base / "state-backups"
        transaction_dir = backups / proposal.transaction_id
        manifest = transaction_dir / "manifest.json"
        if any(path.is_symlink() for path in (base, backups, transaction_dir, manifest)):
            raise ReconciliationBlocked("unsafe canonical transaction path")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReconciliationBlocked("canonical commit manifest unavailable") from exc
        if (data.get("status") != "COMMITTED"
            or data.get("transaction_id") != proposal.transaction_id
            or data.get("project_id") != project_id
            or data.get("proposal_digest") != proposal.proposal_digest
            or data.get("source_state_revision") != proposal.source_state_revision):
            raise ReconciliationBlocked("canonical commit manifest does not match approval")
        revision = data.get("resulting_state_revision")
        if not isinstance(revision, str) or len(revision) != 64:
            raise ReconciliationBlocked("invalid committed state revision")
        return revision

    @staticmethod
    def _audit_ref(proposal: ProjectStateChangeProposal,
                   evidence: CompletionEvidence) -> str:
        digest = hashlib.sha256(evidence.evidence_ref.encode("utf-8")).hexdigest()
        return f"approved:{proposal.transaction_id}:{proposal.proposal_digest}:{digest}"

    def _catch_up(self, project_id: str, completed: str, next_task: str,
                  proposal: ProjectStateChangeProposal,
                  evidence: CompletionEvidence) -> int:
        revision = self._manifest_revision(project_id, proposal)
        aligned = self.alignment.read(project_id)
        if (aligned.status == "INVALID_CANONICAL"
            or aligned.canonical_task_id != next_task
            or aligned.canonical_revision != revision):
            raise ReconciliationBlocked("canonical state differs from committed revision")
        snapshot = self.journal.snapshot(project_id)
        statuses = {task.task_id: task.status for task in snapshot.tasks}
        if (statuses.get(completed) not in ("VERIFYING", "DONE")
            or statuses.get(next_task) not in ("NEXT", "CURRENT")):
            raise ReconciliationBlocked("unexpected journal task state")
        audit_ref = self._audit_ref(proposal, evidence)
        if statuses[completed] == "DONE":
            if not any(
                item.task_id == completed and item.new_state == "DONE"
                and item.evidence_ref == audit_ref
                and item.actor == evidence.verifier_id
                for item in self.journal.audit(project_id)
            ):
                raise ReconciliationBlocked("DONE lacks matching verification audit")
        else:
            if snapshot.revision != evidence.journal_revision:
                raise ReconciliationBlocked('journal changed since verification')
            if statuses[next_task] == "CURRENT":
                raise ReconciliationBlocked("next task active before old completion")
            self.journal.transition(
                project_id, completed, "DONE",
                expected_revision=snapshot.revision,
                actor=evidence.verifier_id, reason="approved canonical commit",
                evidence_ref=audit_ref,
            )
        snapshot = self.journal.snapshot(project_id)
        statuses = {task.task_id: task.status for task in snapshot.tasks}
        if statuses[next_task] == "NEXT":
            self.journal.transition(
                project_id, next_task, "CURRENT",
                expected_revision=snapshot.revision,
                actor=evidence.verifier_id, reason="approved canonical promotion",
            )
        final = self.alignment.read(project_id)
        if not final.aligned or final.canonical_revision != revision:
            raise ReconciliationBlocked("post-reconciliation alignment failed")
        return final.journal_revision

    async def advance(self, project_id: str, completed: str, next_task: str,
                      proposal: ProjectStateChangeProposal,
                      approval: ProjectStateApproval, *, actor: str) -> int:
        self._check_approval(project_id, completed, next_task, proposal, approval)
        aligned = self.alignment.read(project_id)
        if (not aligned.aligned or aligned.canonical_task_id != completed
            or aligned.canonical_revision != proposal.source_state_revision):
            raise ReconciliationBlocked("journal/canonical pre-state not aligned")
        snapshot = self.journal.snapshot(project_id)
        statuses = {task.task_id: task.status for task in snapshot.tasks}
        if (snapshot.revision != aligned.journal_revision
            or statuses.get(completed) != "VERIFYING"
            or statuses.get(next_task) != "NEXT"):
            raise ReconciliationBlocked("journal preconditions not satisfied")
        evidence = self._attest(
            project_id, completed, aligned.canonical_revision, snapshot.revision, actor
        )
        result = await self.store.apply_approved(proposal, approval)
        if (result.status != "COMMITTED"
            or result.resulting_state_revision != self._manifest_revision(project_id, proposal)):
            raise ReconciliationBlocked("canonical commit not verified")
        return self._catch_up(project_id, completed, next_task, proposal, evidence)

    def recover(self, project_id, completed, next_task, proposal, approval, *, actor, original_journal_revision):
        self._check_approval(project_id, completed, next_task, proposal, approval)
        self._manifest_revision(project_id, proposal)
        evidence = self._attest(project_id, completed, proposal.source_state_revision,
                                original_journal_revision, actor)
        return self._catch_up(project_id, completed, next_task, proposal, evidence)
