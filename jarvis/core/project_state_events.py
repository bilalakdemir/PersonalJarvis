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
class ProjectStateMemoryProposalCreated(Event):
    """One exact durable project-state proposal awaits a dedicated decision."""

    project_id: str = ""
    queue_item_id: int = 0
    candidate_id: int = 0
    transaction_id: str = ""
    proposal_digest: str = ""
    source_state_revision: str = ""
    files_affected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectStateMemoryApprovalAccepted(Event):
    """The user approved one exact durable project-state proposal."""

    project_id: str = ""
    queue_item_id: int = 0
    transaction_id: str = ""
    proposal_digest: str = ""


@dataclass(frozen=True, slots=True)
class ProjectStateMemoryApprovalRejected(Event):
    """The user rejected one exact durable project-state proposal."""

    project_id: str = ""
    queue_item_id: int = 0
    transaction_id: str = ""
    proposal_digest: str = ""


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
