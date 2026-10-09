"""Isolated task journal contract; only temporary SQLite files are modified."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.projects.models import ProjectRegistryEntry
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.task_journal import (
    ProjectTaskJournal,
    TaskActiveConflict,
    TaskJournalError,
    TaskRevisionConflict,
)


@pytest.fixture
def setup(tmp_path):
    roots = [tmp_path / "alpha", tmp_path / "beta"]
    for path in roots:
        path.mkdir()
    registry = ProjectRegistry(
        entries=tuple(
            ProjectRegistryEntry(
                project_id=key, project_name=key.title(), aliases=(),
                root_path=root, status="active",
            )
            for key, root in zip(("alpha", "beta"), roots)
        )
    )
    return tmp_path / "journal.db", registry


def test_one_active_and_durable_audit_across_restart(setup):
    path, registry = setup
    journal = ProjectTaskJournal(path, registry)
    assert journal.snapshot("alpha").revision == 0
    rev = journal.add_task("alpha", "PM-001", expected_revision=0,
                           actor="pm", reason="proposal")
    rev = journal.add_task("alpha", "PM-002", expected_revision=rev,
                           actor="pm", reason="proposal")
    rev = journal.transition("alpha", "PM-001", "NEXT",
                             expected_revision=rev, actor="pm", reason="scheduled")
    rev = journal.transition("alpha", "PM-001", "CURRENT",
                             expected_revision=rev, actor="pm", reason="accepted")
    rev = journal.transition("alpha", "PM-002", "NEXT",
                             expected_revision=rev, actor="pm", reason="scheduled")
    with pytest.raises(TaskActiveConflict):
        journal.transition("alpha", "PM-002", "CURRENT",
                           expected_revision=rev, actor="pm", reason="unsafe")
    assert journal.snapshot("alpha").revision == rev
    rev = journal.transition("alpha", "PM-001", "VERIFYING",
                             expected_revision=rev, actor="reviewer", reason="test")
    with pytest.raises(TaskJournalError, match="verification evidence"):
        journal.transition("alpha", "PM-001", "DONE", expected_revision=rev,
                           actor="reviewer", reason="verified")
    rev = journal.transition("alpha", "PM-001", "DONE", expected_revision=rev,
                             actor="reviewer", reason="verified", evidence_ref="ci:123")
    rev = journal.transition("alpha", "PM-002", "CURRENT",
                             expected_revision=rev, actor="pm", reason="next")
    reopened = ProjectTaskJournal(path, registry)
    snapshot = reopened.snapshot("alpha")
    assert snapshot.active_task == "PM-002"
    assert snapshot.revision == rev
    events = reopened.audit("alpha")
    assert len(events) == rev
    assert [e.revision for e in events] == list(range(1, rev + 1))
    assert events[-2].evidence_ref == "ci:123"


def test_unknown_cross_project_and_stale_revision_fail_closed(setup):
    path, registry = setup
    journal = ProjectTaskJournal(path, registry)
    with pytest.raises(TaskJournalError, match="unknown"):
        journal.snapshot("unknown")
    with pytest.raises(TaskJournalError, match="noncanonical"):
        journal.add_task("Alpha", "task", expected_revision=0,
                         actor="pm", reason="no alias")
    a = journal.add_task("alpha", "T-1", expected_revision=0,
                         actor="pm", reason="create")
    b = journal.add_task("beta", "T-1", expected_revision=0,
                         actor="pm", reason="create")
    assert a == b == 1
    with pytest.raises(TaskRevisionConflict):
        journal.add_task("alpha", "T-2", expected_revision=0,
                         actor="pm", reason="stale")
    with pytest.raises(TaskJournalError, match="task not found"):
        journal.transition("beta", "T-2", "NEXT", expected_revision=b,
                           actor="pm", reason="nonexistent")
    assert journal.snapshot("beta").active_task is None
    assert [e.task_id for e in journal.audit("beta")] == ["T-1"]
    with pytest.raises(TaskJournalError, match="invalid task transition"):
        journal.transition("alpha", "T-1", "DONE", expected_revision=a,
                           actor="pm", reason="skip")


def test_simultaneous_promotions_have_one_winner(setup):
    path, registry = setup
    journal = ProjectTaskJournal(path, registry)
    revision = 0
    for task in ("A", "B"):
        revision = journal.add_task("alpha", task, expected_revision=revision,
                                    actor="pm", reason="draft")
        revision = journal.transition("alpha", task, "NEXT",
                                      expected_revision=revision, actor="pm",
                                      reason="queue")
    def promote(task):
        worker = ProjectTaskJournal(path, registry)
        try:
            return worker.transition("alpha", task, "CURRENT",
                                     expected_revision=revision, actor="pm",
                                     reason="one current")
        except (TaskRevisionConflict, TaskActiveConflict):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(promote, ("A", "B")))
    assert sum(x is not None for x in results) == 1
    assert journal.snapshot("alpha").active_task in ("A", "B")
    assert len([task for task in journal.snapshot("alpha").tasks
                if task.status == "CURRENT"]) == 1


def test_block_and_retry_same_task_with_no_skip(setup):
    path, registry = setup
    journal = ProjectTaskJournal(path, registry)
    revision = journal.add_task("alpha", "A", expected_revision=0,
                                actor="pm", reason="draft")
    for status in ("NEXT", "CURRENT", "BLOCKED", "NEXT", "CURRENT",
                   "VERIFYING", "CURRENT"):
        revision = journal.transition(
            "alpha", "A", status, expected_revision=revision,
            actor="pm", reason="test re-entry",
        )
    assert journal.snapshot("alpha").active_task == "A"
    assert journal.snapshot("alpha").revision == revision
    assert journal.audit("alpha")[-1].new_state == "CURRENT"
