from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.projects import (
    AmbiguousProjectError,
    ProjectNotRegisteredError,
    ProjectRegistryError,
    load_project_context,
    load_registry,
)


def _project_text(name: str = "Example Project") -> str:
    return f"""# Project
{name}

## Main Goal
Build a deterministic managed-project state reader.

## Scope
Read-only project state.
"""


def _state_text(
    *,
    name: str = "Example Project",
    current: str | None = "N-09 — Project State Read Foundation",
) -> str:
    current_section = current or ""
    return f"""# Current Project State

Project: {name}

Phase:
Core Implementation

## Current Position
Ready.

## Last Completed
Core Implementation Plan

## CURRENT TASK
{current_section}

## Next Logical Step
Implement the read-only project-state foundation.

## Blockers
None.
"""


def _tasks_text(*currents: str) -> str:
    current_blocks = "\n\n".join(f"### CURRENT — {item}\n\nObjective:\nRead state." for item in currents)
    return f"""# Tasks

## NOW

{current_blocks}

## COMPLETED

### N-08 Core Implementation Plan

Status:
COMPLETED
"""


def _decisions_text(ids: tuple[str, ...] = ("D-001", "D-002")) -> str:
    return "\n\n---\n\n".join(
        f"""## {decision_id} — Decision {index}

Decision:
Keep behavior deterministic.

Reason:
It is testable.

Status:
ACTIVE"""
        for index, decision_id in enumerate(ids, start=1)
    )


def _write_project(
    root: Path,
    *,
    project_name: str = "Example Project",
    state_name: str | None = None,
    state_current: str | None = "N-09 — Project State Read Foundation",
    task_currents: tuple[str, ...] = ("N-09 Project State Read Foundation",),
    decision_ids: tuple[str, ...] = ("D-001", "D-002"),
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PROJECT.md").write_text(_project_text(project_name), encoding="utf-8")
    (root / "STATE.md").write_text(
        _state_text(name=state_name or project_name, current=state_current), encoding="utf-8"
    )
    (root / "TASKS.md").write_text(_tasks_text(*task_currents), encoding="utf-8")
    (root / "DECISIONS.md").write_text(_decisions_text(decision_ids), encoding="utf-8")
    (root / "BACKLOG.md").write_text("# Backlog\n\n## FUTURE\n\n- Later work\n", encoding="utf-8")


def _write_registry(path: Path, projects: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": 1, "projects": projects}), encoding="utf-8")


def _entry(root: Path, *, project_id: str = "example", aliases: list[str] | None = None,
           status: str = "active") -> dict[str, object]:
    return {
        "project_id": project_id,
        "project_name": "Example Project",
        "aliases": aliases or ["example"],
        "root_path": str(root),
        "status": status,
    }


def test_valid_registry_resolves_id_name_alias_and_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])

    registry = load_registry(registry_path)

    assert registry.resolve_exact("example") is not None
    assert registry.resolve_exact("Example Project") is not None
    assert registry.require_root(project_root).project_id == "example"


def test_missing_registry_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(ProjectRegistryError, match="not found"):
        load_registry(tmp_path / "missing.json")


def test_duplicate_project_id_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _entry(tmp_path / "one", project_id="same", aliases=["one"]),
            {**_entry(tmp_path / "two", project_id="same", aliases=["two"]), "project_name": "Two"},
        ],
    )

    with pytest.raises(ProjectRegistryError, match="duplicate project_id"):
        load_registry(registry_path)


def test_ambiguous_alias_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _entry(tmp_path / "one", project_id="one", aliases=["shared"]),
            {**_entry(tmp_path / "two", project_id="two", aliases=["shared"]), "project_name": "Two"},
        ],
    )

    with pytest.raises(AmbiguousProjectError, match="shared"):
        load_registry(registry_path)


def test_unregistered_project_root_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(tmp_path / "registered")])
    registry = load_registry(registry_path)

    with pytest.raises(ProjectNotRegisteredError, match="not registered"):
        registry.require_root(tmp_path / "other")


def test_missing_canonical_file_returns_invalid_load(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    (project_root / "BACKLOG.md").unlink()
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert result.snapshot is None
    assert not result.validation.valid
    assert any(issue.code == "canonical_file_missing" for issue in result.validation.issues)


def test_malformed_utf8_returns_invalid_load(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    (project_root / "STATE.md").write_bytes(b"\xff\xfe\x00")
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert result.snapshot is None
    assert any(issue.code == "canonical_file_not_utf8" for issue in result.validation.issues)


def test_active_project_with_zero_current_tasks_is_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, state_current=None, task_currents=())
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert not result.validation.valid
    assert any(issue.code == "active_current_task_count" for issue in result.validation.issues)


def test_two_current_tasks_are_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(
        project_root,
        task_currents=("N-09 Project State Read Foundation", "N-10 Project State Transactions"),
    )
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert any(issue.code == "active_current_task_count" for issue in result.validation.issues)


def test_state_and_tasks_current_mismatch_is_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, state_current="N-10 — Other Task")
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert any(issue.code == "current_task_mismatch" for issue in result.validation.issues)


def test_paused_project_allows_zero_current_tasks(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, state_current=None, task_currents=())
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root, status="paused")])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert result.validation.valid


def test_duplicate_decision_id_is_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, decision_ids=("D-001", "D-001"))
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert any(issue.code == "duplicate_decision_id" for issue in result.validation.issues)


def test_non_monotonic_decision_ids_are_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, decision_ids=("D-002", "D-001"))
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert any(issue.code == "decision_ids_not_monotonic" for issue in result.validation.issues)


def test_valid_snapshot_contains_authoritative_fields(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    result = load_project_context(entry)

    assert result.validation.valid
    assert result.snapshot is not None
    assert result.snapshot.project_id == "example"
    assert result.snapshot.project_name == "Example Project"
    assert result.snapshot.phase == "Core Implementation"
    assert result.snapshot.current_task == "N-09 — Project State Read Foundation"
    assert result.snapshot.last_completed == "Core Implementation Plan"
    assert [decision.decision_id for decision in result.snapshot.active_decisions] == ["D-001", "D-002"]
    assert result.snapshot.relevant_backlog_items == ()
    assert len(result.snapshot.state_revision) == 64


def test_state_revision_is_deterministic_and_changes_with_source_bytes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_entry(project_root)])
    entry = load_registry(registry_path).resolve_exact("example")
    assert entry is not None

    first = load_project_context(entry)
    second = load_project_context(entry)
    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.state_revision == second.snapshot.state_revision

    with (project_root / "BACKLOG.md").open("a", encoding="utf-8") as handle:
        handle.write("\n- New deferred idea\n")

    changed = load_project_context(entry)
    assert changed.snapshot is not None
    assert changed.snapshot.state_revision != first.snapshot.state_revision
