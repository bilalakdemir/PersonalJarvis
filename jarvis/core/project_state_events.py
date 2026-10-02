"""Typed events for governed managed-project state operations."""
from __future__ import annotations

from dataclasses import dataclass

from .events import Event


@dataclass(frozen=True, slots=True)
class ProjectContextResolved(Event):
    project_id: str = ""
    project_name: str = ""


@dataclass(frozen=True, slots=True)
class ProjectStateLoaded(Event):
    project_id: str = ""
    state_revision: str = ""
    current_task: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectStateInvalid(Event):
    project_id: str = ""
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectStateMemoryProposalRequested(Event):
    """A memory candidate needs governed project-state proposal construction."""

    project_id: str = ""
    candidate_id: int = 0
    source_state_revision: str = ""
    current_task: str | None = None
    relation: str = ""


@dataclass(frozen=True, slots=True)
class ProjectStateChangeProposed(Event):
    project_id: str = ""
    transaction_id: str = ""
    source_state_revision: str = ""
    files_affected: tuple[str, ...] = ()
    expected_current_task: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectStateApprovalRequired(Event):
    project_id: str = ""
    transaction_id: str = ""
    proposal_digest: str = ""
    files_affected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectStateTransactionStarted(Event):
    project_id: str = ""
    transaction_id: str = ""
    files_affected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectStateCommitted(Event):
    project_id: str = ""
    transaction_id: str = ""
    resulting_state_revision: str = ""
    changed_files: tuple[str, ...] = ()
    current_task: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectStateRolledBack(Event):
    project_id: str = ""
    transaction_id: str = ""
    changed_files: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProjectStateTransactionRejected(Event):
    project_id: str = ""
    transaction_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProjectStateTransactionFailed(Event):
    project_id: str = ""
    transaction_id: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class CurrentTaskChanged(Event):
    project_id: str = ""
    transaction_id: str = ""
    previous_task: str | None = None
    current_task: str | None = None
