"""CLI/recovery integration: only a real applied owner proof + pinned QA receipt advances.

All test signers are disposable; they are NOT production QA identities.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.task_reconciliation import ReconciliationBlocked
from scripts.qa.recover_applied_completion import (
    RecoveryConfigurationError,
    recover_approved_completion,
)
from tests.unit.memory.test_project_task_completion_bridge import NOW_MS, _fixture


async def _setup(tmp_path: Path, *, apply: bool = True) -> tuple:
    item, stored, journal, revision, gateway, shutdown = await _fixture(tmp_path, apply=apply)
    instance = gateway()
    root = instance._registry.entries[0].root_path
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [
                    {
                        "project_id": "example",
                        "project_name": "Example Project",
                        "root_path": str(root),
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    key = instance._trusted_keys["independent-qa"]
    pubfile = tmp_path / "qa.pub"
    pubfile.write_bytes(key)
    inbox = tmp_path / "published"
    inbox.mkdir()
    receipt = instance._source(
        project_id="example",
        task_id="N-10",
        canonical_revision=stored.source_state_revision,
        journal_revision=revision,
    )
    name = DirectoryCompletionReceiptSource.filename(
        project_id="example",
        task_id="N-10",
        canonical_revision=stored.source_state_revision,
        journal_revision=revision,
    )
    (inbox / name).write_text(json.dumps(asdict(receipt)), encoding="utf-8")
    kwargs = dict(
        registry_file=registry_file,
        approval_db=instance._proposals._db_path,
        journal_db=journal.db_path,
        receipt_inbox=inbox,
        public_key_file=pubfile,
        pinned_public_key_sha256=hashlib.sha256(key).hexdigest(),
        verifier_id="independent-qa",
        project_id="example",
        completed_task_id="N-10",
        next_task_id="N-11",
        queue_item_id=item.id,
        original_journal_revision=revision,
        actor="pm",
        execute=True,
        clock_ms=lambda: NOW_MS,
    )
    return journal, kwargs, shutdown


@pytest.mark.asyncio
async def test_recovery_cli_exact_applied_signed_task_then_idempotent(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        output = await recover_approved_completion(**kwargs)
        assert output["status"] == "RECOVERED"
        assert journal.snapshot("example").active_task == "N-11"
        assert output["journal_revision"] == journal.snapshot("example").revision
        again = await recover_approved_completion(**kwargs)
        assert again == output
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_explicit_execution_is_required(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        result = await recover_approved_completion(**(kwargs | {"execute": False}))
        assert result["status"] == "NOT_EXECUTED"
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_requires_real_applied_webauthn(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path, apply=False)
    try:
        with pytest.raises(ReconciliationBlocked, match="WebAuthn"):
            await recover_approved_completion(**kwargs)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_missing_receipt(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        next(kwargs["receipt_inbox"].iterdir()).unlink()
        with pytest.raises(ReconciliationBlocked, match="signed receipt"):
            await recover_approved_completion(**kwargs)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_mismatched_pinned_key(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        with pytest.raises(RecoveryConfigurationError, match="mismatch"):
            await recover_approved_completion(**(kwargs | {"pinned_public_key_sha256": "0" * 64}))
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_self_verification(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        with pytest.raises(RecoveryConfigurationError, match="invalid operator"):
            await recover_approved_completion(**(kwargs | {"actor": "independent-qa"}))
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_missing_existing_journal(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        with pytest.raises(RecoveryConfigurationError, match="existing approval"):
            await recover_approved_completion(
                **(kwargs | {"journal_db": tmp_path / "missing.sqlite3"})
            )
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_tampered_receipt(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        receipt_path = next(kwargs["receipt_inbox"].iterdir())
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload["evidence_ref"] = "tampered:reference"
        receipt_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ReconciliationBlocked, match="signed receipt"):
            await recover_approved_completion(**kwargs)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_recovery_cli_rejects_expired_receipt(tmp_path):
    journal, kwargs, shutdown = await _setup(tmp_path)
    try:
        # Time advances beyond the genuine receipt's expiry; no re-signing.
        with pytest.raises(ReconciliationBlocked, match="signed receipt"):
            await recover_approved_completion(**(kwargs | {"clock_ms": lambda: NOW_MS + 3_600_001}))
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await shutdown()
