from __future__ import annotations

import json
from pathlib import Path

from jarvis.brain.project_context import (
    ProjectContextResolutionStatus,
    ProjectContextResolver,
    ProjectTurnContext,
    render_project_context,
)


def _project_text(name: str) -> str:
    return f"""# Project
{name}

## Main Goal
Build the project safely.

## Scope
Canonical test scope.
"""


def _state_text(name: str, current: str = "N-12 — Structural Project Context") -> str:
    return f"""# Current Project State

Project: {name}

Phase:
Core Implementation

## Current Position
Ready.

## Last Completed
N-11 — Capability & Governance Enforcement

## CURRENT TASK
{current}

## Next Logical Step
Finish N-12.

## Blockers
None.
"""


def _tasks_text(current: str = "N-12 Structural Project Context") -> str:
    return f"""# Tasks

## NOW

### CURRENT — {current}

Objective:
Wire canonical project context.

## COMPLETED

### N-11 Capability & Governance Enforcement

Status:
COMPLETED
"""


def _decisions_text() -> str:
    return """## D-001 — Canonical State Wins

Decision:
Canonical project state is authoritative.

Reason:
Conversation is not project truth.

Status:
ACTIVE
"""


def _write_project(root: Path, name: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PROJECT.md").write_text(_project_text(name), encoding="utf-8")
    (root / "STATE.md").write_text(_state_text(name), encoding="utf-8")
    (root / "TASKS.md").write_text(_tasks_text(), encoding="utf-8")
    (root / "DECISIONS.md").write_text(_decisions_text(), encoding="utf-8")
    (root / "BACKLOG.md").write_text(
        "# Backlog\n\n## FUTURE\n\n- Later work\n",
        encoding="utf-8",
    )


def _entry(
    root: Path,
    *,
    project_id: str,
    project_name: str,
    aliases: list[str] | None = None,
) -> dict[str, object]:
    return {
        "project_id": project_id,
        "project_name": project_name,
        "aliases": aliases or [],
        "root_path": str(root),
        "status": "active",
    }


def _write_registry(path: Path, projects: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "projects": projects}),
        encoding="utf-8",
    )


def _two_projects(tmp_path: Path) -> tuple[Path, Path, Path]:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    registry = tmp_path / "registry.json"
    _write_project(alpha, "Alpha Revenue Engine")
    _write_project(beta, "Beta Flight Desk")
    _write_registry(
        registry,
        [
            _entry(
                alpha,
                project_id="alpha",
                project_name="Alpha Revenue Engine",
                aliases=["revenue engine"],
            ),
            _entry(
                beta,
                project_id="beta",
                project_name="Beta Flight Desk",
                aliases=["flight desk"],
            ),
        ],
    )
    return alpha, beta, registry


def test_explicit_project_id_resolves_canonical_snapshot(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "continue the work",
        explicit_project="alpha",
    )

    assert result.status is ProjectContextResolutionStatus.RESOLVED
    assert result.project_id == "alpha"
    assert result.matched_by == "explicit"
    assert result.snapshot is not None
    assert result.snapshot.project_name == "Alpha Revenue Engine"
    assert result.snapshot.current_task == "N-12 — Structural Project Context"


def test_explicit_project_name_in_turn_resolves(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "Please continue Alpha Revenue Engine today."
    )

    assert result.status is ProjectContextResolutionStatus.RESOLVED
    assert result.project_id == "alpha"
    assert result.matched_by == "explicit"


def test_exact_registry_alias_resolves(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "Continue the revenue engine work."
    )

    assert result.status is ProjectContextResolutionStatus.RESOLVED
    assert result.project_id == "alpha"
    assert result.matched_by == "alias"


def test_active_project_continuation_resolves_only_when_explicitly_supplied(
    tmp_path: Path,
) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "keep going",
        active_project_id="beta",
        continuation=True,
    )

    assert result.status is ProjectContextResolutionStatus.RESOLVED
    assert result.project_id == "beta"
    assert result.matched_by == "active-continuation"


def test_no_project_does_not_guess_from_generic_turn(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "What time should I leave tomorrow?"
    )

    assert result.status is ProjectContextResolutionStatus.NO_PROJECT
    assert result.snapshot is None


def test_multiple_explicit_projects_are_ambiguous(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "Compare Alpha Revenue Engine with Beta Flight Desk."
    )

    assert result.status is ProjectContextResolutionStatus.AMBIGUOUS
    assert result.snapshot is None


def test_missing_registry_is_unavailable(tmp_path: Path) -> None:
    result = ProjectContextResolver(
        tmp_path / "missing.json"
    ).resolve("continue alpha")

    assert result.status is ProjectContextResolutionStatus.UNAVAILABLE
    assert result.snapshot is None
    assert result.detail is not None
    assert "not found" in result.detail


def test_invalid_registry_is_unavailable(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text("{not-json", encoding="utf-8")

    result = ProjectContextResolver(registry).resolve("continue alpha")

    assert result.status is ProjectContextResolutionStatus.UNAVAILABLE
    assert result.snapshot is None


def test_invalid_canonical_state_is_not_recovered_from_conversation(
    tmp_path: Path,
) -> None:
    alpha, _, registry = _two_projects(tmp_path)
    (alpha / "BACKLOG.md").unlink()

    result = ProjectContextResolver(registry).resolve(
        "Alpha Revenue Engine was on N-12 and everything was valid before."
    )

    assert result.status is ProjectContextResolutionStatus.UNAVAILABLE
    assert result.project_id == "alpha"
    assert result.snapshot is None
    assert result.detail is not None
    assert "canonical_file_missing" in result.detail


def test_high_confidence_match_is_unique_and_deterministic(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    resolver = ProjectContextResolver(registry)

    first = resolver.resolve(
        "For revenue, continue the engine work on priorities."
    )
    second = resolver.resolve(
        "For revenue, continue the engine work on priorities."
    )

    assert first.status is ProjectContextResolutionStatus.RESOLVED
    assert first.project_id == "alpha"
    assert first.matched_by == "high-confidence"
    assert first == second


def test_explicit_project_beats_active_continuation_and_conversational_context(
    tmp_path: Path,
) -> None:
    _, _, registry = _two_projects(tmp_path)
    result = ProjectContextResolver(registry).resolve(
        "We were discussing Beta Flight Desk.",
        explicit_project="alpha",
        active_project_id="beta",
        continuation=True,
    )

    assert result.status is ProjectContextResolutionStatus.RESOLVED
    assert result.project_id == "alpha"
    assert result.matched_by == "explicit"
    assert result.snapshot is not None
    assert result.snapshot.project_name == "Alpha Revenue Engine"


def test_rendered_resolved_context_is_authoritative_and_turn_scoped(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    resolution = ProjectContextResolver(registry).resolve(
        "Alpha Revenue Engine"
    )

    block = render_project_context(resolution)

    assert "[PROJECT CONTEXT — AUTHORITATIVE CANONICAL STATE]" in block
    assert "Project ID: alpha" in block
    assert "CURRENT task: N-12 — Structural Project Context" in block
    assert "canonical project state overrides conflicting conversation history" in block


def test_ambiguous_render_blocks_history_or_memory_fallback(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    resolution = ProjectContextResolver(registry).resolve(
        "Compare Alpha Revenue Engine with Beta Flight Desk."
    )

    block = render_project_context(resolution)

    assert resolution.status is ProjectContextResolutionStatus.AMBIGUOUS
    assert "No project has been selected" in block
    assert "Do not choose a project from conversation history or memory" in block


def test_conversation_scoped_active_projects_do_not_leak(tmp_path: Path) -> None:
    _, _, registry = _two_projects(tmp_path)
    turns = ProjectTurnContext(ProjectContextResolver(registry))

    alpha_first = turns.resolve_turn(
        "Work on Alpha Revenue Engine.",
        conversation_id="conversation-alpha",
    )
    beta_first = turns.resolve_turn(
        "Work on Beta Flight Desk.",
        conversation_id="conversation-beta",
    )
    alpha_next = turns.resolve_turn(
        "keep going",
        conversation_id="conversation-alpha",
    )
    beta_next = turns.resolve_turn(
        "continue",
        conversation_id="conversation-beta",
    )

    assert alpha_first.project_id == "alpha"
    assert beta_first.project_id == "beta"
    assert alpha_next.project_id == "alpha"
    assert beta_next.project_id == "beta"
    assert alpha_next.matched_by == "active-continuation"
    assert beta_next.matched_by == "active-continuation"


def test_no_conversation_id_does_not_reuse_another_active_project(
    tmp_path: Path,
) -> None:
    _, _, registry = _two_projects(tmp_path)
    turns = ProjectTurnContext(ProjectContextResolver(registry))
    turns.resolve_turn(
        "Work on Alpha Revenue Engine.",
        conversation_id="conversation-alpha",
    )

    result = turns.resolve_turn("keep going")

    assert result.status is ProjectContextResolutionStatus.NO_PROJECT
    assert result.snapshot is None


def test_invalid_canonical_state_stays_unavailable_on_active_continuation(
    tmp_path: Path,
) -> None:
    alpha, _, registry = _two_projects(tmp_path)
    turns = ProjectTurnContext(ProjectContextResolver(registry))
    first = turns.resolve_turn(
        "Work on Alpha Revenue Engine.",
        conversation_id="conversation-alpha",
    )
    assert first.status is ProjectContextResolutionStatus.RESOLVED

    (alpha / "BACKLOG.md").unlink()
    follow_up = turns.resolve_turn(
        "continue",
        conversation_id="conversation-alpha",
    )

    assert follow_up.status is ProjectContextResolutionStatus.UNAVAILABLE
    assert follow_up.project_id == "alpha"
    assert follow_up.snapshot is None


def test_ambiguous_turn_does_not_silently_replace_active_project(
    tmp_path: Path,
) -> None:
    _, _, registry = _two_projects(tmp_path)
    turns = ProjectTurnContext(ProjectContextResolver(registry))
    turns.resolve_turn(
        "Work on Alpha Revenue Engine.",
        conversation_id="conversation-alpha",
    )

    ambiguous = turns.resolve_turn(
        "Compare Alpha Revenue Engine with Beta Flight Desk.",
        conversation_id="conversation-alpha",
    )
    resumed = turns.resolve_turn(
        "continue",
        conversation_id="conversation-alpha",
    )

    assert ambiguous.status is ProjectContextResolutionStatus.AMBIGUOUS
    assert resumed.status is ProjectContextResolutionStatus.RESOLVED
    assert resumed.project_id == "alpha"
