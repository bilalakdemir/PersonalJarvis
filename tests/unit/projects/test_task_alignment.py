"""Read-only PM alignment contracts against actual project loader and journal."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.projects.models import ProjectRegistryEntry
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.task_alignment import ProjectTaskAlignmentReader
from jarvis.projects.task_journal import ProjectTaskJournal, TaskJournalError


def _project(root: Path, name: str, active: str = "P-001") -> None:
    root.mkdir()
    (root / "PROJECT.md").write_text(
        f"# Project\n{name}\n\n## Main Goal\nDeliver a safe project.\n",
        encoding="utf-8",
    )
    (root / "STATE.md").write_text(
        f"# Current Project State\nProject: {name}\nPhase: Build\n"
        f"\n## CURRENT TASK\n{active} — Work on milestone\n"
        "\n## Last Completed\nNothing yet.\n"
        "\n## Next Logical Step\nValidate the result.\n"
        "\n## Blockers\nNone.\n",
        encoding="utf-8",
    )
    (root / "TASKS.md").write_text(
        f"# Tasks\n\n### CURRENT — {active} — Work on milestone\n",
        encoding="utf-8",
    )
    (root / "DECISIONS.md").write_text("# Decisions\n", encoding="utf-8")
    (root / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")


@pytest.fixture
def scope(tmp_path):
    one, two = tmp_path / "a", tmp_path / "b"
    _project(one, "Alpha")
    _project(two, "Beta")
    registry = ProjectRegistry(entries=(
        ProjectRegistryEntry("alpha", "Alpha", (), one, "active"),
        ProjectRegistryEntry("beta", "Beta", (), two, "active"),
    ))
    journal = ProjectTaskJournal(tmp_path / "journal.db", registry)
    return registry, journal, one, two


def _activate(journal: ProjectTaskJournal, project: str, task: str) -> None:
    revision = journal.snapshot(project).revision
    revision = journal.add_task(
        project, task, expected_revision=revision, actor="pm", reason="observed",
    )
    revision = journal.transition(
        project, task, "NEXT", expected_revision=revision,
        actor="pm", reason="plan",
    )
    journal.transition(
        project, task, "CURRENT", expected_revision=revision,
        actor="pm", reason="activate",
    )


def test_uninitialized_is_not_silent_alignment(scope):
    registry, journal, _, _ = scope
    report = ProjectTaskAlignmentReader(registry, journal).read("alpha")
    assert report.status == "UNINITIALIZED"
    assert not report.aligned
    assert report.canonical_task_id == "P-001"
    assert report.journal_revision == 0
    assert journal.audit("alpha") == ()


def test_aligned_and_durable_after_reopen(scope):
    registry, journal, _, _ = scope
    _activate(journal, "alpha", "P-001")
    reader = ProjectTaskAlignmentReader(
        registry, ProjectTaskJournal(journal.db_path, registry),
    )
    report = reader.read("alpha")
    assert report.aligned
    assert report.canonical_revision
    assert report.journal_task_id == report.canonical_task_id == "P-001"
    assert len(journal.audit("alpha")) == 3


def test_divergence_fails_closed_and_does_not_mutate(scope):
    registry, journal, root, _ = scope
    _activate(journal, "alpha", "P-001")
    previous_audit = journal.audit("alpha")
    # A legitimate canonical file update changes both canonical files.
    for name in ("STATE.md", "TASKS.md"):
        file = root / name
        file.write_text(file.read_text(encoding="utf-8").replace("P-001", "P-002"), encoding="utf-8")
    report = ProjectTaskAlignmentReader(registry, journal).read("alpha")
    assert report.status == "DIVERGED"
    assert not report.aligned
    assert report.canonical_task_id == "P-002"
    assert report.journal_task_id == "P-001"
    assert journal.audit("alpha") == previous_audit


def test_invalid_canonical_documents_fail_closed(scope):
    registry, journal, root, _ = scope
    _activate(journal, "alpha", "P-001")
    (root / "TASKS.md").write_text("### CURRENT — P-999 — Wrong task\n", encoding="utf-8")
    report = ProjectTaskAlignmentReader(registry, journal).read("alpha")
    assert report.status == "INVALID_CANONICAL"
    assert "current_task_mismatch" in report.issue_codes
    assert not report.aligned


def test_exact_project_scopes_do_not_leak_other_project(scope):
    registry, journal, _, _ = scope
    _activate(journal, "alpha", "P-001")
    reader = ProjectTaskAlignmentReader(registry, journal)
    other = reader.read("beta")
    assert other.status == "UNINITIALIZED"
    assert other.journal_task_id is None
    with pytest.raises(TaskJournalError):
        reader.read("Alpha")
    with pytest.raises(TaskJournalError):
        reader.read("nonexistent")
