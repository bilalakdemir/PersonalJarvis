"""Read-only domain models for managed Personal Jarvis projects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProjectStatus = Literal["active", "paused", "completed", "archived"]

CANONICAL_PROJECT_FILES: tuple[str, ...] = (
    "PROJECT.md",
    "STATE.md",
    "TASKS.md",
    "DECISIONS.md",
    "BACKLOG.md",
)


@dataclass(frozen=True, slots=True)
class ProjectRegistryEntry:
    """One managed project registered with Personal Jarvis."""

    project_id: str
    project_name: str
    aliases: tuple[str, ...]
    root_path: Path
    status: ProjectStatus
    created_at: str | None = None
    last_opened_at: str | None = None
    archived_at: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectDecision:
    """One parsed decision block from ``DECISIONS.md``."""

    decision_id: str
    title: str
    decision: str
    reason: str
    status: str


@dataclass(frozen=True, slots=True)
class ProjectDocuments:
    """Exact canonical project-file contents loaded from one project root."""

    project: str
    state: str
    tasks: str
    decisions: str
    backlog: str
    raw_project: bytes
    raw_state: bytes
    raw_tasks: bytes
    raw_decisions: bytes
    raw_backlog: bytes

    def text_for(self, filename: str) -> str:
        mapping = {
            "PROJECT.md": self.project,
            "STATE.md": self.state,
            "TASKS.md": self.tasks,
            "DECISIONS.md": self.decisions,
            "BACKLOG.md": self.backlog,
        }
        return mapping[filename]

    def bytes_for(self, filename: str) -> bytes:
        mapping = {
            "PROJECT.md": self.raw_project,
            "STATE.md": self.raw_state,
            "TASKS.md": self.raw_tasks,
            "DECISIONS.md": self.raw_decisions,
            "BACKLOG.md": self.raw_backlog,
        }
        return mapping[filename]


@dataclass(frozen=True, slots=True)
class ParsedProjectState:
    """Structural fields extracted from the five canonical Markdown files."""

    project_name: str
    state_project_name: str
    main_goal: str
    phase: str
    state_current_task: str | None
    task_current_tasks: tuple[str, ...]
    last_completed: str
    next_step: str
    blockers: str
    decisions: tuple[ProjectDecision, ...]


@dataclass(frozen=True, slots=True)
class ProjectValidationIssue:
    """One explicit project-state validation failure."""

    code: str
    message: str
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectValidationResult:
    """Validation outcome for one read-only project-state load."""

    issues: tuple[ProjectValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class ProjectContextSnapshot:
    """Derived context; never a second source of project truth."""

    project_id: str
    project_name: str
    root_path: Path
    main_goal: str
    phase: str
    current_task: str | None
    last_completed: str
    next_step: str
    blockers: str
    active_decisions: tuple[ProjectDecision, ...]
    relevant_backlog_items: tuple[str, ...]
    state_revision: str


@dataclass(frozen=True, slots=True)
class ProjectLoadResult:
    """Read-only load result, including invalid-state diagnostics."""

    snapshot: ProjectContextSnapshot | None
    validation: ProjectValidationResult
