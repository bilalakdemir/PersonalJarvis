"""All-or-nothing filesystem transaction for canonical project-state files."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import UUID

from .loader import (
    compute_state_revision_from_bytes,
    evaluate_project_documents,
    project_documents_from_bytes,
)
from .models import (
    CANONICAL_PROJECT_FILES,
    ProjectRegistryEntry,
    ProjectStateChangeProposal,
    ProjectStateTransactionResult,
    ProjectValidationResult,
)
from .proposal import proposal_digest, read_source_bytes


class ProjectStateTransactionError(RuntimeError):
    """Base error for governed project-state transaction failures."""


class ProjectStateStaleRevisionError(ProjectStateTransactionError):
    """The canonical state changed after the proposal was prepared."""


class ProjectStateReplayError(ProjectStateTransactionError):
    """A transaction identifier was already consumed or recorded."""


class ProjectStateRollbackError(ProjectStateTransactionError):
    """Rollback could not fully restore the pre-transaction canonical state."""


class ProjectStatePathError(ProjectStateTransactionError):
    """A canonical or engine-owned transaction path is unsafe."""


class ProjectStateValidationError(ProjectStateTransactionError):
    """The proposed or written resulting state failed validation."""


def _safe_engine_dir(root: Path, *parts: str) -> Path:
    current = root
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ProjectStatePathError(f"engine-owned path must not be a symlink: {current}")
    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectStatePathError(f"engine-owned path escapes project root: {current}") from exc
    return current


def _safe_target(entry: ProjectRegistryEntry, filename: str) -> Path:
    if filename not in CANONICAL_PROJECT_FILES:
        raise ProjectStatePathError(f"non-canonical project-state file: {filename!r}")
    root = entry.root_path.resolve(strict=False)
    target = root / filename
    if target.is_symlink():
        raise ProjectStatePathError(f"canonical project-state file must not be a symlink: {filename}")
    if target.resolve(strict=False).parent != root:
        raise ProjectStatePathError(f"canonical project-state path escapes project root: {filename}")
    return target


def _atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path: Path | None = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _manifest_write(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_bytes(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8"),
    )


def _validate_proposal_integrity(proposal: ProjectStateChangeProposal) -> None:
    try:
        UUID(proposal.transaction_id)
    except (ValueError, AttributeError) as exc:
        raise ProjectStateTransactionError("proposal transaction_id must be a UUID") from exc
    if proposal.proposal_digest != proposal_digest(
        ProjectStateChangeProposal(
            transaction_id=proposal.transaction_id,
            project_id=proposal.project_id,
            source_state_revision=proposal.source_state_revision,
            changes=proposal.changes,
            reason=proposal.reason,
            expected_current_task=proposal.expected_current_task,
            expected_effect=proposal.expected_effect,
            proposal_digest="",
        )
    ):
        raise ProjectStateTransactionError("proposal digest does not match proposal contents")
    seen: set[str] = set()
    for change in proposal.changes:
        if change.filename in seen:
            raise ProjectStateTransactionError(f"proposal repeats canonical file: {change.filename}")
        seen.add(change.filename)
        if change.filename not in CANONICAL_PROJECT_FILES:
            raise ProjectStatePathError(f"proposal contains non-canonical file: {change.filename}")
        actual = hashlib.sha256(change.proposed_content.encode("utf-8")).hexdigest()
        if actual != change.proposed_sha256:
            raise ProjectStateTransactionError(
                f"proposal content hash mismatch for {change.filename}"
            )


def _build_final_bytes(
    source: dict[str, bytes | None], proposal: ProjectStateChangeProposal
) -> dict[str, bytes]:
    replacements = {change.filename: change for change in proposal.changes}
    final: dict[str, bytes] = {}
    for filename in CANONICAL_PROJECT_FILES:
        change = replacements.get(filename)
        if change is not None:
            current = source[filename]
            current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
            if current_hash != change.previous_sha256:
                raise ProjectStateStaleRevisionError(
                    f"source file hash changed before commit: {filename}"
                )
            final[filename] = change.proposed_content.encode("utf-8")
        else:
            raw = source[filename]
            if raw is None:
                raise ProjectStateValidationError(
                    f"resulting state would still be missing canonical file: {filename}"
                )
            final[filename] = raw
    return final


def apply_project_state_transaction(
    entry: ProjectRegistryEntry,
    proposal: ProjectStateChangeProposal,
) -> ProjectStateTransactionResult:
    """Apply one already approval-bound proposal with rollback on any failure."""
    _validate_proposal_integrity(proposal)
    if proposal.project_id != entry.project_id:
        raise ProjectStateTransactionError(
            f"proposal project_id {proposal.project_id!r} does not match {entry.project_id!r}"
        )

    root = entry.root_path.resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ProjectStatePathError(f"registered project root is not a directory: {root}")
    for filename in CANONICAL_PROJECT_FILES:
        _safe_target(entry, filename)

    backup_root = _safe_engine_dir(root, ".jarvis", "state-backups")
    transaction_dir = _safe_engine_dir(backup_root, proposal.transaction_id)
    if transaction_dir.exists():
        raise ProjectStateReplayError(
            f"project-state transaction was already recorded: {proposal.transaction_id}"
        )

    source = read_source_bytes(entry)
    actual_revision = compute_state_revision_from_bytes(source)
    if actual_revision != proposal.source_state_revision:
        raise ProjectStateStaleRevisionError(
            "project state changed after approval; reload and prepare a new proposal"
        )

    final_bytes = _build_final_bytes(source, proposal)
    try:
        candidate_documents = project_documents_from_bytes(final_bytes)
    except UnicodeDecodeError as exc:
        raise ProjectStateValidationError("proposed resulting state is not UTF-8") from exc
    candidate = evaluate_project_documents(entry, candidate_documents)
    if not candidate.validation.valid or candidate.snapshot is None:
        codes = ", ".join(issue.code for issue in candidate.validation.issues) or "unknown"
        raise ProjectStateValidationError(f"proposed resulting state is invalid: {codes}")
    if candidate.snapshot.current_task != proposal.expected_current_task:
        raise ProjectStateValidationError(
            "proposal expected CURRENT task does not match the validated resulting state"
        )

    transaction_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = transaction_dir / "manifest.json"
    manifest: dict[str, object] = {
        "transaction_id": proposal.transaction_id,
        "project_id": proposal.project_id,
        "proposal_digest": proposal.proposal_digest,
        "source_state_revision": proposal.source_state_revision,
        "files": list(proposal.files_affected),
        "status": "PREPARED",
        "created_at_ns": time.time_ns(),
    }
    _manifest_write(manifest_path, manifest)

    backups: dict[str, bytes | None] = {}
    for change in proposal.changes:
        previous = source[change.filename]
        backups[change.filename] = previous
        if previous is not None:
            _atomic_write_bytes(transaction_dir / change.filename, previous)

    temp_paths: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        # Stage every new file completely before the first target is replaced.
        for change in proposal.changes:
            target = _safe_target(entry, change.filename)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f"{target.name}.{proposal.transaction_id}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "wb") as fh:
                fh.write(change.proposed_content.encode("utf-8"))
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            temp_paths[change.filename] = tmp_path

        for change in proposal.changes:
            target = _safe_target(entry, change.filename)
            staged = temp_paths[change.filename]
            os.replace(staged, target)
            temp_paths.pop(change.filename, None)
            replaced.append(change.filename)

        post_source = read_source_bytes(entry)
        post_documents = project_documents_from_bytes(
            {name: raw for name, raw in post_source.items() if raw is not None}
        )
        post = evaluate_project_documents(entry, post_documents)
        if not post.validation.valid or post.snapshot is None:
            codes = ", ".join(issue.code for issue in post.validation.issues) or "unknown"
            raise ProjectStateValidationError(f"post-write project state is invalid: {codes}")
        if post.snapshot.current_task != proposal.expected_current_task:
            raise ProjectStateValidationError(
                "post-write CURRENT task differs from the approved proposal"
            )

        manifest.update(
            {
                "status": "COMMITTED",
                "resulting_state_revision": post.snapshot.state_revision,
                "completed_at_ns": time.time_ns(),
            }
        )
        _manifest_write(manifest_path, manifest)
        return ProjectStateTransactionResult(
            transaction_id=proposal.transaction_id,
            project_id=entry.project_id,
            status="COMMITTED",
            source_state_revision=proposal.source_state_revision,
            resulting_state_revision=post.snapshot.state_revision,
            changed_files=proposal.files_affected,
            backup_dir=transaction_dir,
            validation=post.validation,
        )
    except Exception as exc:
        rollback_failures: list[str] = []
        for filename in reversed(replaced):
            target = _safe_target(entry, filename)
            previous = backups[filename]
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write_bytes(target, previous)
            except OSError as rollback_exc:
                rollback_failures.append(f"{filename}: {rollback_exc}")

        status = "FAILED" if rollback_failures else "ROLLED_BACK"
        error_text = str(exc)
        if rollback_failures:
            error_text += "; rollback failures: " + "; ".join(rollback_failures)
        manifest.update(
            {
                "status": status,
                "error": error_text,
                "completed_at_ns": time.time_ns(),
            }
        )
        try:
            _manifest_write(manifest_path, manifest)
        except OSError:
            pass

        if rollback_failures:
            raise ProjectStateRollbackError(
                f"project-state rollback was incomplete; manual recovery from {transaction_dir} is required: "
                + "; ".join(rollback_failures)
            ) from exc

        return ProjectStateTransactionResult(
            transaction_id=proposal.transaction_id,
            project_id=entry.project_id,
            status="ROLLED_BACK",
            source_state_revision=proposal.source_state_revision,
            resulting_state_revision=proposal.source_state_revision,
            changed_files=tuple(replaced),
            backup_dir=transaction_dir,
            validation=ProjectValidationResult(),
            error=error_text,
        )
    finally:
        for tmp_path in temp_paths.values():
            tmp_path.unlink(missing_ok=True)
