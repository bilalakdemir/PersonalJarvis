"""Private PM execution journal, subordinate to approved canonical project files.

This journal does not write PROJECT.md, STATE.md, TASKS.md, DECISIONS.md,
or BACKLOG.md. Those remain governed by projects.state_store.ProjectStateStore.
No mission dispatch, approvals, or persistent-memory promotion happen here.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from .registry import ProjectRegistry

TaskState = Literal[
    "BACKLOG", "NEXT", "CURRENT", "VERIFYING", "DONE", "BLOCKED", "CANCELLED"
]
_ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("BACKLOG", "NEXT"), ("NEXT", "CURRENT"), ("CURRENT", "VERIFYING"),
    ("VERIFYING", "DONE"), ("VERIFYING", "CURRENT"),
    ("CURRENT", "BLOCKED"), ("BLOCKED", "NEXT"), ("CURRENT", "CANCELLED"),
})
_ACTIVE = ("CURRENT", "VERIFYING")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}\Z")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_project_revisions (
    project_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
);
CREATE TABLE IF NOT EXISTS pm_task_entries (
    project_id TEXT NOT NULL REFERENCES pm_project_revisions(project_id),
    task_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('BACKLOG','NEXT','CURRENT','VERIFYING','DONE','BLOCKED','CANCELLED')
    ),
    PRIMARY KEY (project_id, task_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS pm_one_active_task_per_project
    ON pm_task_entries(project_id)
    WHERE status IN ('CURRENT', 'VERIFYING');
CREATE TABLE IF NOT EXISTS pm_task_audit (
    project_id TEXT NOT NULL REFERENCES pm_project_revisions(project_id),
    revision INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    prior_state TEXT,
    new_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_ref TEXT,
    recorded_ms INTEGER NOT NULL,
    PRIMARY KEY (project_id, revision)
);
"""


class TaskJournalError(ValueError):
    """A task operation was not allowed by the journal contract."""


class TaskRevisionConflict(TaskJournalError):
    """The caller's observed project revision is stale."""


class TaskActiveConflict(TaskJournalError):
    """Another task is CURRENT or VERIFYING for this project."""


@dataclass(frozen=True, slots=True)
class TaskEntry:
    task_id: str
    status: TaskState


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    project_id: str
    revision: int
    tasks: tuple[TaskEntry, ...]
    active_task: str | None


@dataclass(frozen=True, slots=True)
class TaskAuditEntry:
    project_id: str
    revision: int
    task_id: str
    prior_state: str | None
    new_state: TaskState
    actor: str
    reason: str
    evidence_ref: str | None


def _required(value: str, label: str, limit: int = 320) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise TaskJournalError(f"{label} must be a non-empty bounded string")
    return value.strip()


class ProjectTaskJournal:
    """Atomic operational task transitions; not a second canonical-file writer.

    The existing registry is the project-identity authority. Every mutation
    requires an optimistic project revision. SQLite BEGIN IMMEDIATE serializes
    writers *across processes*, and the partial UNIQUE index enforces one
    CURRENT/VERIFYING task even if an application check regresses.
    """

    def __init__(self, db_path: Path, registry: ProjectRegistry) -> None:
        self.db_path = Path(db_path)
        self._registry = registry
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, isolation_level=None, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def _project(self, project_id: str) -> str:
        # Do not accept a project alias as a write scope.
        project_id = _required(project_id, "project_id", 128)
        entry = self._registry.resolve_exact(project_id)
        if entry is None or entry.project_id != project_id:
            raise TaskJournalError("unknown or noncanonical project_id")
        if entry.status != "active":
            raise TaskJournalError("project is not active")
        return entry.project_id

    @staticmethod
    def _revision(db: sqlite3.Connection, project_id: str) -> int:
        db.execute(
            "INSERT OR IGNORE INTO pm_project_revisions(project_id, revision) VALUES (?, 0)",
            (project_id,),
        )
        row = db.execute(
            "SELECT revision FROM pm_project_revisions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _check(expected: int, actual: int) -> None:
        if type(expected) is not int or expected != actual:
            raise TaskRevisionConflict(
                f"stale project revision: expected {expected!r}, actual {actual}"
            )

    @staticmethod
    def _record(
        db: sqlite3.Connection, project_id: str, revision: int,
        task_id: str, before: str | None, after: str,
        actor: str, reason: str, evidence_ref: str | None,
    ) -> None:
        db.execute(
            """INSERT INTO pm_task_audit
               (project_id,revision,task_id,prior_state,new_state,
                actor,reason,evidence_ref,recorded_ms)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (project_id, revision + 1, task_id, before, after, actor,
             reason, evidence_ref, int(time.time() * 1000)),
        )
        db.execute(
            "UPDATE pm_project_revisions SET revision=? WHERE project_id=?",
            (revision + 1, project_id),
        )

    def add_task(
        self, project_id: str, task_id: str, *,
        expected_revision: int, actor: str, reason: str,
    ) -> int:
        project_id = self._project(project_id)
        task_id = _required(task_id, "task_id", 128)
        if _ID.fullmatch(task_id) is None:
            raise TaskJournalError("task_id contains unsupported characters")
        actor = _required(actor, "actor", 128)
        reason = _required(reason, "reason")
        with self._write() as db:
            revision = self._revision(db, project_id)
            self._check(expected_revision, revision)
            if db.execute(
                "SELECT 1 FROM pm_task_entries WHERE project_id=? AND task_id=?",
                (project_id, task_id),
            ).fetchone():
                raise TaskJournalError("task already exists")
            db.execute(
                "INSERT INTO pm_task_entries(project_id,task_id,status) VALUES (?,?,?)",
                (project_id, task_id, "BACKLOG"),
            )
            self._record(db, project_id, revision, task_id, None, "BACKLOG",
                         actor, reason, None)
            return revision + 1

    def transition(
        self, project_id: str, task_id: str, new_state: TaskState, *,
        expected_revision: int, actor: str, reason: str,
        evidence_ref: str | None = None,
    ) -> int:
        project_id = self._project(project_id)
        task_id = _required(task_id, "task_id", 128)
        actor = _required(actor, "actor", 128)
        reason = _required(reason, "reason")
        evidence_ref = _required(evidence_ref, "evidence_ref", 512) if evidence_ref is not None else None
        with self._write() as db:
            revision = self._revision(db, project_id)
            self._check(expected_revision, revision)
            row = db.execute(
                "SELECT status FROM pm_task_entries WHERE project_id=? AND task_id=?",
                (project_id, task_id),
            ).fetchone()
            if row is None:
                raise TaskJournalError("task not found in this project")
            before = str(row[0])
            if (before, new_state) not in _ALLOWED:
                raise TaskJournalError(f"invalid task transition: {before} -> {new_state}")
            if new_state == "DONE" and evidence_ref is None:
                raise TaskJournalError("independent verification evidence is required")
            if new_state in _ACTIVE:
                other = db.execute(
                    """SELECT task_id FROM pm_task_entries
                       WHERE project_id=? AND status IN ('CURRENT','VERIFYING')
                       AND task_id<>?""",
                    (project_id, task_id),
                ).fetchone()
                if other is not None:
                    raise TaskActiveConflict("a different task is already active")
            try:
                db.execute(
                    "UPDATE pm_task_entries SET status=? WHERE project_id=? AND task_id=?",
                    (new_state, project_id, task_id),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskActiveConflict("a different task is already active") from exc
            self._record(db, project_id, revision, task_id, before, new_state,
                         actor, reason, evidence_ref)
            return revision + 1

    def snapshot(self, project_id: str) -> TaskSnapshot:
        project_id = self._project(project_id)
        with self._connection() as db:
            # Hold one SQLite read snapshot across revision and task queries.
            # Otherwise concurrent commits could expose mismatched revisions.
            db.execute("BEGIN")
            try:
                row = db.execute(
                    "SELECT revision FROM pm_project_revisions WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                tasks = tuple(
                    TaskEntry(task_id=t, status=s)
                    for t, s in db.execute(
                        "SELECT task_id,status FROM pm_task_entries WHERE project_id=? ORDER BY task_id",
                        (project_id,),
                    )
                )
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        active = [task.task_id for task in tasks if task.status in _ACTIVE]
        return TaskSnapshot(project_id, int(row[0]) if row is not None else 0,
                            tasks, active[0] if active else None)

    def audit(self, project_id: str) -> tuple[TaskAuditEntry, ...]:
        project_id = self._project(project_id)
        with self._connection() as db:
            rows = db.execute(
                """SELECT project_id,revision,task_id,prior_state,new_state,
                          actor,reason,evidence_ref
                   FROM pm_task_audit WHERE project_id=? ORDER BY revision""",
                (project_id,),
            ).fetchall()
        return tuple(TaskAuditEntry(*row) for row in rows)
