"""Read-only proposal construction for governed project-state changes."""
from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

from .loader import (
    compute_state_revision_from_bytes,
    evaluate_project_documents,
    project_documents_from_bytes,
)
from .models import (
    CANONICAL_PROJECT_FILES,
    ProjectFileChange,
    ProjectRegistryEntry,
    ProjectStateChangeProposal,
)


class ProjectStateProposalError(ValueError):
    """The requested change cannot become a safe, reviewable state proposal."""


def _normalise_transaction_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ProjectStateProposalError("transaction_id must be a UUID") from exc


def _canonical_target(entry: ProjectRegistryEntry, filename: str) -> Path:
    if filename not in CANONICAL_PROJECT_FILES:
        raise ProjectStateProposalError(f"non-canonical project-state file: {filename!r}")
    root = entry.root_path.resolve(strict=False)
    target = root / filename
    if target.is_symlink():
        raise ProjectStateProposalError(f"canonical project-state file must not be a symlink: {filename}")
    resolved = target.resolve(strict=False)
    if resolved.parent != root:
        raise ProjectStateProposalError(f"canonical project-state path escapes project root: {filename}")
    return target


def read_source_bytes(entry: ProjectRegistryEntry) -> dict[str, bytes | None]:
    """Read the exact canonical source state, preserving missing-file state."""
    root = entry.root_path.resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ProjectStateProposalError(f"registered project root is not a directory: {root}")
    result: dict[str, bytes | None] = {}
    for filename in CANONICAL_PROJECT_FILES:
        target = _canonical_target(entry, filename)
        try:
            result[filename] = target.read_bytes()
        except FileNotFoundError:
            result[filename] = None
        except OSError as exc:
            raise ProjectStateProposalError(f"cannot read {filename}: {exc}") from exc
    return result


def proposal_digest(proposal: ProjectStateChangeProposal) -> str:
    """Hash the exact approved proposal payload, including replacement text."""
    payload = {
        "transaction_id": proposal.transaction_id,
        "project_id": proposal.project_id,
        "source_state_revision": proposal.source_state_revision,
        "changes": [
            {
                "filename": change.filename,
                "previous_sha256": change.previous_sha256,
                "proposed_sha256": change.proposed_sha256,
                "proposed_content": change.proposed_content,
            }
            for change in proposal.changes
        ],
        "reason": proposal.reason,
        "expected_current_task": proposal.expected_current_task,
        "expected_effect": proposal.expected_effect,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_diff(filename: str, previous: bytes | None, proposed: str) -> str:
    if previous is None:
        before_lines: list[str] = []
        fromfile = "/dev/null"
    else:
        try:
            before_text = previous.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ""
        before_lines = before_text.splitlines(keepends=True)
        fromfile = f"a/{filename}"
    return "".join(
        difflib.unified_diff(
            before_lines,
            proposed.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=f"b/{filename}",
        )
    )


def _build_final_documents(
    source: Mapping[str, bytes | None],
    replacements: Mapping[str, str],
):
    final: dict[str, bytes] = {}
    for filename in CANONICAL_PROJECT_FILES:
        if filename in replacements:
            try:
                final[filename] = replacements[filename].encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ProjectStateProposalError(
                    f"proposed {filename} content is not UTF-8 encodable"
                ) from exc
            continue
        raw = source[filename]
        if raw is None:
            raise ProjectStateProposalError(
                f"resulting state would still be missing canonical file: {filename}"
            )
        final[filename] = raw
    try:
        return project_documents_from_bytes(final)
    except UnicodeDecodeError as exc:
        raise ProjectStateProposalError(
            "resulting canonical project state is not valid UTF-8"
        ) from exc


def build_project_state_proposal(
    entry: ProjectRegistryEntry,
    replacements: Mapping[str, str],
    *,
    reason: str,
    expected_current_task: str | None,
    expected_effect: str,
    transaction_id: str | None = None,
) -> ProjectStateChangeProposal:
    """Build and validate one exact proposal without mutating project state."""
    if not replacements:
        raise ProjectStateProposalError("project-state proposal must change at least one file")
    unknown = sorted(set(replacements) - set(CANONICAL_PROJECT_FILES))
    if unknown:
        raise ProjectStateProposalError(
            f"project-state proposal contains non-canonical files: {', '.join(unknown)}"
        )
    if not reason.strip():
        raise ProjectStateProposalError("proposal reason must not be empty")
    if not expected_effect.strip():
        raise ProjectStateProposalError("proposal expected_effect must not be empty")

    source = read_source_bytes(entry)
    source_revision = compute_state_revision_from_bytes(source)
    final_documents = _build_final_documents(source, replacements)
    evaluation = evaluate_project_documents(entry, final_documents)
    if not evaluation.validation.valid or evaluation.snapshot is None:
        codes = ", ".join(issue.code for issue in evaluation.validation.issues) or "unknown"
        raise ProjectStateProposalError(f"proposed resulting state is invalid: {codes}")
    if evaluation.snapshot.current_task != expected_current_task:
        raise ProjectStateProposalError(
            "expected CURRENT task does not match the proposed resulting state: "
            f"expected {expected_current_task!r}, got {evaluation.snapshot.current_task!r}"
        )

    changes: list[ProjectFileChange] = []
    for filename in CANONICAL_PROJECT_FILES:
        if filename not in replacements:
            continue
        previous = source[filename]
        proposed = replacements[filename]
        proposed_bytes = proposed.encode("utf-8")
        changes.append(
            ProjectFileChange(
                filename=filename,
                previous_sha256=(hashlib.sha256(previous).hexdigest() if previous is not None else None),
                proposed_sha256=hashlib.sha256(proposed_bytes).hexdigest(),
                proposed_content=proposed,
                unified_diff=_render_diff(filename, previous, proposed),
            )
        )

    unsigned = ProjectStateChangeProposal(
        transaction_id=_normalise_transaction_id(transaction_id),
        project_id=entry.project_id,
        source_state_revision=source_revision,
        changes=tuple(changes),
        reason=reason.strip(),
        expected_current_task=expected_current_task,
        expected_effect=expected_effect.strip(),
        proposal_digest="",
    )
    return ProjectStateChangeProposal(
        transaction_id=unsigned.transaction_id,
        project_id=unsigned.project_id,
        source_state_revision=unsigned.source_state_revision,
        changes=unsigned.changes,
        reason=unsigned.reason,
        expected_current_task=unsigned.expected_current_task,
        expected_effect=unsigned.expected_effect,
        proposal_digest=proposal_digest(unsigned),
    )
