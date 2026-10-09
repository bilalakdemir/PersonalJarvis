"""Read-only reconciliation of PM task execution with approved project state.

Canonical project files remain solely under ProjectStateStore governance.
This adapter performs no task transitions, approvals, dispatch, or writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .loader import load_project_context
from .registry import ProjectRegistry
from .task_journal import ProjectTaskJournal, TaskJournalError

AlignmentStatus = Literal[
    "ALIGNED", "UNINITIALIZED", "DIVERGED", "INVALID_CANONICAL"
]
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class TaskAlignment:
    project_id: str
    status: AlignmentStatus
    canonical_revision: str | None
    journal_revision: int
    canonical_task_id: str | None
    journal_task_id: str | None
    issue_codes: tuple[str, ...] = ()

    @property
    def aligned(self) -> bool:
        return self.status == "ALIGNED"


class ProjectTaskAlignmentReader:
    """Compare one project by canonical ID, without creating a second writer.

    An UNINITIALIZED journal must not be silently seeded from Markdown.
    Only ALIGNED means subsequent scoped task actions may be considered;
    permission checks and independent verification remain separate gates.
    """

    def __init__(self, registry: ProjectRegistry, journal: ProjectTaskJournal) -> None:
        self._registry = registry
        self._journal = journal

    def read(self, project_id: str) -> TaskAlignment:
        entry = self._registry.resolve_exact(project_id)
        if entry is None or entry.project_id != project_id or entry.status != "active":
            raise TaskJournalError("unknown, noncanonical or inactive project_id")

        loaded = load_project_context(entry)
        observed = self._journal.snapshot(project_id)
        revision = loaded.snapshot.state_revision if loaded.snapshot else None
        canonical_value = loaded.snapshot.current_task if loaded.snapshot else None
        canonical_id = None
        if canonical_value:
            candidate = canonical_value.split(maxsplit=1)[0]
            if _TASK_ID.fullmatch(candidate):
                canonical_id = candidate

        issue_codes = tuple(issue.code for issue in loaded.validation.issues)
        if not loaded.validation.valid or canonical_id is None:
            return TaskAlignment(
                project_id, "INVALID_CANONICAL", revision, observed.revision,
                canonical_id, observed.active_task,
                issue_codes or ("canonical_task_id_invalid",),
            )
        if not observed.tasks:
            status: AlignmentStatus = "UNINITIALIZED"
        elif observed.active_task != canonical_id:
            status = "DIVERGED"
        else:
            status = "ALIGNED"

        return TaskAlignment(
            project_id, status, revision, observed.revision,
            canonical_id, observed.active_task,
        )
