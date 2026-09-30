"""Read, parse, validate, and snapshot canonical managed-project state."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import (
    CANONICAL_PROJECT_FILES,
    ParsedProjectState,
    ProjectContextSnapshot,
    ProjectDecision,
    ProjectDocuments,
    ProjectLoadResult,
    ProjectRegistryEntry,
    ProjectValidationIssue,
    ProjectValidationResult,
)
from .validator import validate_project_state

_CURRENT_HEADING_RE = re.compile(r"^###\s+CURRENT(?:\s+[—–-])?\s+(.+?)\s*$", re.IGNORECASE)
_DECISION_HEADING_RE = re.compile(
    r"^##\s+(D-\d+)\s+[—–-]\s+(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_LABEL_RE_TEMPLATE = r"^\s*{label}\s*:\s*(.*?)\s*$"


def _section(text: str, *, level: int, title: str) -> str:
    heading = re.compile(
        rf"^{'#' * level}\s+{re.escape(title)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = heading.search(text)
    if match is None:
        return ""
    start = match.end()
    next_heading = re.compile(rf"^#{{1,{level}}}\s+.+$", re.MULTILINE).search(text, start)
    end = next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _first_content_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _label_value(text: str, label: str) -> str:
    pattern = re.compile(_LABEL_RE_TEMPLATE.format(label=re.escape(label)), re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        return ""
    inline = match.group(1).strip()
    if inline:
        return inline
    remainder = text[match.end():]
    for line in remainder.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return ""
        return stripped
    return ""


def _current_tasks(tasks_text: str) -> tuple[str, ...]:
    return tuple(
        match.group(1).strip()
        for line in tasks_text.splitlines()
        if (match := _CURRENT_HEADING_RE.match(line)) is not None
    )


def _field_from_block(block: str, field_name: str) -> str:
    pattern = re.compile(_LABEL_RE_TEMPLATE.format(label=re.escape(field_name)), re.IGNORECASE | re.MULTILINE)
    match = pattern.search(block)
    if match is None:
        return ""
    inline = match.group(1).strip()
    if inline:
        return inline
    start = match.end()
    next_field = re.compile(r"^[A-Za-z][A-Za-z ]*:\s*", re.MULTILINE).search(block, start)
    end = next_field.start() if next_field else len(block)
    return block[start:end].strip()


def _decisions(decisions_text: str) -> tuple[ProjectDecision, ...]:
    matches = list(_DECISION_HEADING_RE.finditer(decisions_text))
    parsed: list[ProjectDecision] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(decisions_text)
        block = decisions_text[start:end].strip()
        parsed.append(
            ProjectDecision(
                decision_id=match.group(1).upper(),
                title=match.group(2).strip(),
                decision=_field_from_block(block, "Decision"),
                reason=_field_from_block(block, "Reason"),
                status=_field_from_block(block, "Status").upper(),
            )
        )
    return tuple(parsed)


def compute_state_revision(documents: ProjectDocuments) -> str:
    """Hash exact source bytes in canonical filename order for optimistic checks."""
    digest = hashlib.sha256()
    for filename in CANONICAL_PROJECT_FILES:
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(documents.bytes_for(filename))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_canonical_file(root: Path, filename: str) -> tuple[str, bytes]:
    root_resolved = root.resolve(strict=False)
    path = root_resolved / filename
    resolved_path = path.resolve(strict=False)
    if resolved_path.parent != root_resolved:
        raise ValueError(f"canonical file escapes project root: {filename}")
    raw = path.read_bytes()
    return raw.decode("utf-8-sig"), raw


def _read_documents(entry: ProjectRegistryEntry) -> tuple[ProjectDocuments | None, ProjectValidationResult]:
    values: dict[str, tuple[str, bytes]] = {}
    issues: list[ProjectValidationIssue] = []
    for filename in CANONICAL_PROJECT_FILES:
        try:
            values[filename] = _read_canonical_file(entry.root_path, filename)
        except FileNotFoundError:
            issues.append(
                ProjectValidationIssue(
                    code="canonical_file_missing",
                    message=f"required canonical project file is missing: {filename}",
                    filename=filename,
                )
            )
        except UnicodeDecodeError:
            issues.append(
                ProjectValidationIssue(
                    code="canonical_file_not_utf8",
                    message=f"canonical project file is not readable UTF-8: {filename}",
                    filename=filename,
                )
            )
        except (OSError, ValueError) as exc:
            issues.append(
                ProjectValidationIssue(
                    code="canonical_file_unreadable",
                    message=f"canonical project file cannot be read safely: {filename}: {exc}",
                    filename=filename,
                )
            )

    if issues:
        return None, ProjectValidationResult(issues=tuple(issues))

    return ProjectDocuments(
        project=values["PROJECT.md"][0],
        state=values["STATE.md"][0],
        tasks=values["TASKS.md"][0],
        decisions=values["DECISIONS.md"][0],
        backlog=values["BACKLOG.md"][0],
        raw_project=values["PROJECT.md"][1],
        raw_state=values["STATE.md"][1],
        raw_tasks=values["TASKS.md"][1],
        raw_decisions=values["DECISIONS.md"][1],
        raw_backlog=values["BACKLOG.md"][1],
    ), ProjectValidationResult()


def _parse(documents: ProjectDocuments) -> ParsedProjectState:
    project_block = _section(documents.project, level=1, title="Project")
    state_current = _section(documents.state, level=2, title="CURRENT TASK")
    decisions = _decisions(documents.decisions)
    return ParsedProjectState(
        project_name=_first_content_line(project_block),
        state_project_name=_label_value(documents.state, "Project"),
        main_goal=_section(documents.project, level=2, title="Main Goal"),
        phase=_label_value(documents.state, "Phase"),
        state_current_task=_first_content_line(state_current) or None,
        task_current_tasks=_current_tasks(documents.tasks),
        last_completed=_first_content_line(_section(documents.state, level=2, title="Last Completed")),
        next_step=_section(documents.state, level=2, title="Next Logical Step"),
        blockers=_section(documents.state, level=2, title="Blockers"),
        decisions=decisions,
    )


def load_project_context(entry: ProjectRegistryEntry) -> ProjectLoadResult:
    """Load one registered project's canonical state without writing anything."""
    documents, read_validation = _read_documents(entry)
    if documents is None:
        return ProjectLoadResult(snapshot=None, validation=read_validation)

    parsed = _parse(documents)
    validation = validate_project_state(entry, documents, parsed)
    revision = compute_state_revision(documents)
    snapshot = ProjectContextSnapshot(
        project_id=entry.project_id,
        project_name=parsed.project_name or entry.project_name,
        root_path=entry.root_path,
        main_goal=parsed.main_goal,
        phase=parsed.phase,
        current_task=parsed.state_current_task,
        last_completed=parsed.last_completed,
        next_step=parsed.next_step,
        blockers=parsed.blockers,
        active_decisions=tuple(
            decision for decision in parsed.decisions if decision.status == "ACTIVE"
        ),
        # Relevance is request-scoped and belongs to the later Chief-Agent context
        # integration slice. N-09 intentionally proves no backlog item relevant.
        relevant_backlog_items=(),
        state_revision=revision,
    )
    return ProjectLoadResult(snapshot=snapshot, validation=validation)
