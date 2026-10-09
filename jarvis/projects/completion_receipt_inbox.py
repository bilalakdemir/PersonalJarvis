"""Read-only inbox for independently issued PM completion receipts.

The owner-controlled PM process never signs, issues or modifies attestations.
A different security principal must publish receipts to a separately ACL-managed
directory, and a trusted administrator pins ONLY the reviewer's public key.
This transport does not replace SignedCompletionVerifier or human approval.

Wire format: `sha256(canonical request tuple).json` holds one exact JSON
SignedCompletionReceipt object. Directory must be a distinct trusted mount.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

from .signed_completion_verifier import SignedCompletionReceipt

log = logging.getLogger(__name__)

_MAX_BYTES = 4096
_SCOPE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset(field.name for field in fields(SignedCompletionReceipt))
_TEXT_FIELDS = _FIELDS - {"journal_revision", "issued_at_ms", "expires_at_ms"}
_INT_FIELDS = {"journal_revision", "issued_at_ms", "expires_at_ms"}


def _deny_non_finite(_: str) -> Any:
    raise ValueError("non-finite receipt number")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt property")
        result[key] = value
    return result


class DirectoryCompletionReceiptSource:
    """Exact-identity reader; no writes, keys, signing or approval methods.

    The source directory must already exist and must be administered by an
    independent reviewer. A reader alone cannot guarantee the external OS
    permissions or that the actual reviewer exists; deployment must verify both.
    """

    def __init__(self, receipt_directory: str | Path) -> None:
        candidate = Path(receipt_directory).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("independent receipt directory must preexist as a real directory")
        self._directory = candidate.resolve(strict=True)

    @staticmethod
    def filename(
        *, project_id: str, task_id: str, canonical_revision: str,
        journal_revision: int,
    ) -> str:
        if (
            not isinstance(project_id, str) or not _SCOPE.fullmatch(project_id)
            or not isinstance(task_id, str) or not _SCOPE.fullmatch(task_id)
            or not isinstance(canonical_revision, str)
            or not _SHA256.fullmatch(canonical_revision)
            or type(journal_revision) is not int or journal_revision < 0
        ):
            raise ValueError("invalid task receipt lookup scope")
        scope = {
            "canonical_revision": canonical_revision,
            "journal_revision": journal_revision,
            "project_id": project_id,
            "task_id": task_id,
        }
        digest = hashlib.sha256(json.dumps(
            scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        return f"{digest}.json"

    def __call__(
        self, *, project_id: str, task_id: str, canonical_revision: str,
        journal_revision: int,
    ) -> SignedCompletionReceipt | None:
        try:
            name = self.filename(
                project_id=project_id, task_id=task_id,
                canonical_revision=canonical_revision,
                journal_revision=journal_revision,
            )
            file = self._directory / name
            if file.is_symlink() or not file.is_file():
                return None

            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            handle = os.open(file, flags)
            with os.fdopen(handle, "rb") as stream:
                content = stream.read(_MAX_BYTES + 1)
            if len(content) > _MAX_BYTES:
                return None
            parsed = json.loads(
                content.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                parse_constant=_deny_non_finite,
            )
            if not isinstance(parsed, dict) or set(parsed) != _FIELDS:
                return None
            if any(type(parsed[name]) is not str for name in _TEXT_FIELDS):
                return None
            if any(type(parsed[name]) is not int for name in _INT_FIELDS):
                return None
            if any((
                parsed["project_id"] != project_id,
                parsed["task_id"] != task_id,
                parsed["canonical_revision"] != canonical_revision,
                parsed["journal_revision"] != journal_revision,
            )):
                return None
            return SignedCompletionReceipt(**parsed)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            log.warning(
                "independent completion receipt inbox ignored invalid evidence (%s)",
                type(exc).__name__,
            )
            return None


__all__ = ["DirectoryCompletionReceiptSource"]
