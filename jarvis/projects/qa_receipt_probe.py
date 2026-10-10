"""Read-only operator diagnostic for externally issued PM completion receipts.

This module DOES NOT approve proposals, issue signatures, write canonical
project state, advance the journal, or establish reviewer independence.
An out-of-band administrator must first verify the identity and custody of
the external reviewer's signing key, independently pin its public-key digest,
and provision the inbox read-only to the Project Manager.

Run: python -m jarvis.projects.qa_receipt_probe --help
Exit: 0 signed receipt matches; 2 absent/invalid evidence; 3 bad trust setup.
Never treat a test fixture or a caller-chosen key as independent QA approval.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .completion_receipt_inbox import DirectoryCompletionReceiptSource
from .signed_completion_verifier import SignedCompletionVerifier

log = logging.getLogger(__name__)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    exit_code: int
    status: str


def verify_external_receipt(
    *, inbox: Path, public_key_file: Path, pinned_public_key_sha256: str,
    verifier_id: str, project_id: str, task_id: str,
    canonical_revision: str, journal_revision: int, proposal_digest: str,
    clock_ms: Callable[[], int] | None = None,
) -> ProbeResult:
    """Read-only diagnostic; pinned digest is supplied from independent custody.

    Only reports cryptographic equivalence to the configured trust anchor.
    Never certifies whether the key owner is organizationally independent.
    """
    if (
        not isinstance(pinned_public_key_sha256, str)
        or _SHA256.fullmatch(pinned_public_key_sha256) is None
        or not isinstance(public_key_file, Path)
        or not public_key_file.is_absolute()
        or public_key_file.is_symlink()
        or not public_key_file.is_file()
    ):
        return ProbeResult(3, "TRUST_CONFIGURATION_INVALID")

    try:
        with public_key_file.open("rb") as stream:
            public_key = stream.read(33)
        if len(public_key) != 32 or not hmac.compare_digest(
            hashlib.sha256(public_key).hexdigest(), pinned_public_key_sha256,
        ):
            return ProbeResult(3, "TRUST_CONFIGURATION_INVALID")

        source = DirectoryCompletionReceiptSource(inbox)
        verifier = SignedCompletionVerifier(
            proposal_digest=proposal_digest,
            trusted_public_keys={verifier_id: public_key},
            source=source,
            clock_ms=clock_ms,
        )
        evidence = verifier.verify(
            project_id=project_id, task_id=task_id,
            canonical_revision=canonical_revision,
            journal_revision=journal_revision,
        )
        if evidence is None:
            return ProbeResult(2, "RECEIPT_NOT_VERIFIED")
        return ProbeResult(0, "SIGNATURE_VALID")
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        log.warning(
            "read-only QA receipt probe rejected invalid trust setup (%s)",
            type(exc).__name__,
        )
        return ProbeResult(3, "TRUST_CONFIGURATION_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only receipt signature diagnostic. "
            "External reviewer custody and user approval are separate gates."
        ),
    )
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--pinned-public-key-sha256", required=True)
    parser.add_argument("--verifier-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--canonical-revision", required=True)
    parser.add_argument("--journal-revision", type=int, required=True)
    parser.add_argument("--proposal-digest", required=True)
    args = parser.parse_args(argv)
    result = verify_external_receipt(
        inbox=args.inbox,
        public_key_file=args.public_key_file,
        pinned_public_key_sha256=args.pinned_public_key_sha256,
        verifier_id=args.verifier_id,
        project_id=args.project_id,
        task_id=args.task_id,
        canonical_revision=args.canonical_revision,
        journal_revision=args.journal_revision,
        proposal_digest=args.proposal_digest,
    )
    print(json.dumps({"status": result.status}, separators=(",", ":")))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
