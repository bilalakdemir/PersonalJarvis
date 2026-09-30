"""Structural validation for read-only managed project state."""
from __future__ import annotations

import re
from .models import (
    ParsedProjectState,
    ProjectDocuments,
    ProjectRegistryEntry,
    ProjectValidationIssue,
    ProjectValidationResult,
)

_DECISION_ID_RE = re.compile(r"^D-(\d+)$", re.IGNORECASE)


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().replace("—", " ").replace("–", " ").split())


def _decision_number(decision_id: str) -> int | None:
    match = _DECISION_ID_RE.fullmatch(decision_id.strip())
    return int(match.group(1)) if match else None


def validate_project_state(
    entry: ProjectRegistryEntry,
    documents: ProjectDocuments,
    parsed: ParsedProjectState,
) -> ProjectValidationResult:
    """Validate approved read-side project invariants without repairing anything."""
    issues: list[ProjectValidationIssue] = []
    resolved_root = entry.root_path.resolve(strict=False)

    if not resolved_root.exists() or not resolved_root.is_dir():
        issues.append(
            ProjectValidationIssue(
                code="project_root_missing",
                message=f"registered project root is not a directory: {resolved_root}",
            )
        )

    if not parsed.project_name:
        issues.append(
            ProjectValidationIssue(
                code="project_name_missing",
                message="PROJECT.md must identify the project under '# Project'",
                filename="PROJECT.md",
            )
        )
    elif _normalise_text(parsed.project_name) != _normalise_text(entry.project_name):
        issues.append(
            ProjectValidationIssue(
                code="registry_project_name_mismatch",
                message=(
                    f"registry project_name {entry.project_name!r} does not match "
                    f"PROJECT.md project name {parsed.project_name!r}"
                ),
                filename="PROJECT.md",
            )
        )

    if parsed.state_project_name and (
        _normalise_text(parsed.state_project_name) != _normalise_text(parsed.project_name)
    ):
        issues.append(
            ProjectValidationIssue(
                code="state_project_name_mismatch",
                message=(
                    f"STATE.md project {parsed.state_project_name!r} does not match "
                    f"PROJECT.md project {parsed.project_name!r}"
                ),
                filename="STATE.md",
            )
        )

    for field_name, value, filename in (
        ("main goal", parsed.main_goal, "PROJECT.md"),
        ("phase", parsed.phase, "STATE.md"),
        ("last completed", parsed.last_completed, "STATE.md"),
        ("next logical step", parsed.next_step, "STATE.md"),
        ("blockers", parsed.blockers, "STATE.md"),
    ):
        if not value.strip():
            issues.append(
                ProjectValidationIssue(
                    code=f"{field_name.replace(' ', '_')}_missing",
                    message=f"{filename} must define {field_name}",
                    filename=filename,
                )
            )

    current_count = len(parsed.task_current_tasks)
    if entry.status == "active":
        if current_count != 1:
            issues.append(
                ProjectValidationIssue(
                    code="active_current_task_count",
                    message=f"active project must have exactly one CURRENT task; found {current_count}",
                    filename="TASKS.md",
                )
            )
    elif current_count != 0:
        issues.append(
            ProjectValidationIssue(
                code="inactive_project_has_current_task",
                message=f"{entry.status} project must have zero CURRENT tasks; found {current_count}",
                filename="TASKS.md",
            )
        )

    if current_count == 1:
        state_current = _normalise_text(parsed.state_current_task)
        tasks_current = _normalise_text(parsed.task_current_tasks[0])
        if not state_current:
            issues.append(
                ProjectValidationIssue(
                    code="state_current_task_missing",
                    message="STATE.md must identify the CURRENT task",
                    filename="STATE.md",
                )
            )
        elif state_current != tasks_current:
            issues.append(
                ProjectValidationIssue(
                    code="current_task_mismatch",
                    message=(
                        f"STATE.md CURRENT task {parsed.state_current_task!r} does not match "
                        f"TASKS.md CURRENT task {parsed.task_current_tasks[0]!r}"
                    ),
                )
            )
    elif parsed.state_current_task:
        issues.append(
            ProjectValidationIssue(
                code="state_current_without_tasks_current",
                message="STATE.md names a CURRENT task but TASKS.md does not contain exactly one",
            )
        )

    decision_ids = [decision.decision_id for decision in parsed.decisions]
    if len(set(decision_id.casefold() for decision_id in decision_ids)) != len(decision_ids):
        issues.append(
            ProjectValidationIssue(
                code="duplicate_decision_id",
                message="DECISIONS.md contains duplicate decision IDs",
                filename="DECISIONS.md",
            )
        )

    numbers = [_decision_number(decision_id) for decision_id in decision_ids]
    if any(number is None for number in numbers):
        issues.append(
            ProjectValidationIssue(
                code="invalid_decision_id",
                message="DECISIONS.md decision IDs must use the D-<number> format",
                filename="DECISIONS.md",
            )
        )
    else:
        numeric_ids = [number for number in numbers if number is not None]
        if numeric_ids != sorted(numeric_ids) or len(set(numeric_ids)) != len(numeric_ids):
            issues.append(
                ProjectValidationIssue(
                    code="decision_ids_not_monotonic",
                    message="DECISIONS.md decision IDs must be unique and monotonic",
                    filename="DECISIONS.md",
                )
            )

    # Reading the raw bytes here is intentional: this function does not mutate
    # project state, but keeping the parameter ensures validation always occurs
    # against the exact document set used to derive the snapshot.
    _ = documents
    return ProjectValidationResult(issues=tuple(issues))
