"""Operator-only recovery of a PREVIOUSLY APPLIED, owner-WebAuthn-approved PM task.

This entrypoint never issues WebAuthn grants, signs QA receipts, changes canonical
project Markdown, or chooses trust anchors. Operators must first independently
pin the external reviewer's public-key digest and enforce reviewer-owned inbox ACLs.

Usage: python -m scripts.qa.recover_applied_completion --help
No journal mutation occurs unless --execute is passed explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import re
from pathlib import Path

from jarvis.memory.project_state_approval import ProjectStateProposalStore
from jarvis.memory.project_task_completion_bridge import ApprovedProjectTaskCompletionBridge
from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.qa_receipt_probe import verify_external_receipt
from jarvis.projects.registry import load_registry
from jarvis.projects.task_journal import ProjectTaskJournal
from jarvis.projects.task_reconciliation import ReconciliationBlocked

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RecoveryConfigurationError(ValueError):
    """External authorization or resource configuration is not valid."""


def _load_pinned_public_key(path: Path, pinned_sha256: str) -> bytes:
    """A digest pinned out of band is mandatory; never trust receipt-provided keys."""
    if (
        not isinstance(pinned_sha256, str)
        or _SHA256.fullmatch(pinned_sha256) is None
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise RecoveryConfigurationError("independent QA public key not configured")
    try:
        with path.open("rb") as stream:
            raw = stream.read(33)
    except OSError as exc:
        raise RecoveryConfigurationError("independent QA public key is not readable") from exc
    if len(raw) != 32 or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pinned_sha256):
        raise RecoveryConfigurationError("independent QA pinned trust anchor mismatch")
    return raw


async def recover_approved_completion(
    *,
    registry_file: Path,
    approval_db: Path,
    journal_db: Path,
    receipt_inbox: Path,
    public_key_file: Path,
    pinned_public_key_sha256: str,
    verifier_id: str,
    project_id: str,
    completed_task_id: str,
    next_task_id: str,
    queue_item_id: int,
    original_journal_revision: int,
    actor: str,
    execute: bool,
    clock_ms=None,
) -> dict[str, object]:
    """Explicit operator gate; no canonical writes or signing are possible.

    The independent trust relationship is deployed/administered OUTSIDE this
    code; passing a caller-created key does not establish QA independence.
    """
    if not execute:
        return {"status": "NOT_EXECUTED", "reason": "explicit --execute required"}
    if (
        type(queue_item_id) is not int
        or queue_item_id < 1
        or type(original_journal_revision) is not int
        or original_journal_revision < 1
        or not actor
        or not actor.strip()
        or not verifier_id
        or actor == verifier_id
    ):
        raise RecoveryConfigurationError("invalid operator or journal scope")
    if not approval_db.is_file() or not journal_db.is_file():
        raise RecoveryConfigurationError("existing approval and task journals required")
    if not registry_file.is_file():
        raise RecoveryConfigurationError("existing registered projects required")
    trusted_key = _load_pinned_public_key(public_key_file, pinned_public_key_sha256)
    source = DirectoryCompletionReceiptSource(receipt_inbox)
    registry = load_registry(registry_file)
    entry = registry.resolve_exact(project_id)
    if entry is None or entry.project_id != project_id or entry.status != "active":
        raise RecoveryConfigurationError("exact active project scope required")

    proposals = ProjectStateProposalStore(approval_db)
    try:
        record = await proposals.get(queue_item_id)
        if (
            record is None
            or record.project_id != project_id
            or record.status != "applied"
            or record.approved_via != "webauthn"
            or not record.approved_by_credential_id
            or not record.approval_assertion_digest
        ):
            raise ReconciliationBlocked("applied owner WebAuthn approval not present")
        result = verify_external_receipt(
            inbox=receipt_inbox,
            public_key_file=public_key_file,
            pinned_public_key_sha256=pinned_public_key_sha256,
            verifier_id=verifier_id,
            project_id=project_id,
            task_id=completed_task_id,
            canonical_revision=record.source_state_revision,
            journal_revision=original_journal_revision,
            proposal_digest=record.proposal_digest,
            clock_ms=clock_ms,
        )
        if result.status != "SIGNATURE_VALID":
            raise ReconciliationBlocked("independent signed receipt is not verified")
        journal = ProjectTaskJournal(journal_db, registry)
        bridge = ApprovedProjectTaskCompletionBridge(
            registry=registry,
            proposals=proposals,
            journal=journal,
            trusted_public_keys={verifier_id: trusted_key},
            receipt_source=source,
            clock_ms=clock_ms,
        )
        revision = await bridge.recover_applied(
            queue_item_id=queue_item_id,
            project_id=project_id,
            completed_task_id=completed_task_id,
            next_task_id=next_task_id,
            original_journal_revision=original_journal_revision,
            actor=actor,
        )
        return {
            "status": "RECOVERED",
            "project_id": project_id,
            "completed_task_id": completed_task_id,
            "next_task_id": next_task_id,
            "journal_revision": revision,
        }
    finally:
        await proposals.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed operator recovery; applied WebAuthn + external QA required"
    )
    parser.add_argument("--registry-file", type=Path, required=True)
    parser.add_argument("--approval-db", type=Path, required=True)
    parser.add_argument("--journal-db", type=Path, required=True)
    parser.add_argument("--receipt-inbox", type=Path, required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--pinned-public-key-sha256", required=True)
    parser.add_argument("--verifier-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--completed-task-id", required=True)
    parser.add_argument("--next-task-id", required=True)
    parser.add_argument("--queue-item-id", type=int, required=True)
    parser.add_argument("--original-journal-revision", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--execute", action="store_true")
    args = vars(parser.parse_args())
    try:
        result = asyncio.run(recover_approved_completion(**args))
    except (ReconciliationBlocked, RecoveryConfigurationError, ValueError, OSError) as exc:
        print(json.dumps({"status": "RECOVERY_BLOCKED", "error": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "RECOVERED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
