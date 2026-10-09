"""Real approval lifecycle + signed QA journal catch-up on temporary projects."""
from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.memory.project_state_approval import ProjectStateProposalStore
from jarvis.memory.project_task_completion_bridge import (
    ApprovedProjectTaskCompletionBridge,
)
from jarvis.projects.signed_completion_verifier import SignedCompletionReceipt
from jarvis.projects.task_journal import ProjectTaskJournal
from jarvis.projects.task_reconciliation import ReconciliationBlocked

from tests.unit.memory.test_project_state_approval import _setup, _registry

NOW_MS = 1_791_540_000_000


async def _fixture(tmp_path, *, apply=True):
    root, candidate_journal, queue, item, lifecycle, db_path = await _setup(tmp_path)
    registry = _registry(root)
    journal = ProjectTaskJournal(tmp_path / "task-journal.sqlite3", registry)
    revision = journal.add_task(
        "example", "N-10", expected_revision=0, actor="pm", reason="planned")
    revision = journal.add_task(
        "example", "N-11", expected_revision=revision, actor="pm", reason="planned")
    revision = journal.transition(
        "example", "N-10", "NEXT", expected_revision=revision,
        actor="pm", reason="planned")
    revision = journal.transition(
        "example", "N-10", "CURRENT", expected_revision=revision,
        actor="pm", reason="active")
    revision = journal.transition(
        "example", "N-11", "NEXT", expected_revision=revision,
        actor="pm", reason="planned")
    old_revision = journal.transition(
        "example", "N-10", "VERIFYING", expected_revision=revision,
        actor="pm", reason="pending verification")
    stored = await lifecycle.prepare(item.id)
    if apply:
        applied = await lifecycle.approve_and_apply(
            queue_item_id=item.id,
            transaction_id=stored.transaction_id,
            proposal_digest=stored.proposal_digest,
        )
        assert applied.status == "COMMITTED"
    proposals = ProjectStateProposalStore(db_path)
    signer = Ed25519PrivateKey.generate()
    key = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    receipt = SignedCompletionReceipt(
        project_id="example",
        task_id="N-10",
        canonical_revision=stored.source_state_revision,
        journal_revision=old_revision,
        proposal_digest=stored.proposal_digest,
        verifier_id="independent-qa",
        evidence_ref="qa:verified-evidence",
        issued_at_ms=NOW_MS - 1000,
        expires_at_ms=NOW_MS + 3600000,
        signature_hex="",
    )
    receipt = replace(
        receipt, signature_hex=signer.sign(receipt.signed_bytes()).hex()
    )

    def gateway(*, proposal_store=proposals, proof=receipt, trusted=None, clock=NOW_MS):
        return ApprovedProjectTaskCompletionBridge(
            registry=registry, proposals=proposal_store,
            journal=journal, trusted_public_keys=trusted if trusted is not None
                else {"independent-qa": key},
            receipt_source=lambda **_: proof,
            clock_ms=lambda: clock,
        )

    async def shutdown():
        await proposals.close()
        await lifecycle.close()
        await queue.close()
        candidate_journal.close()

    return item, stored, journal, old_revision, gateway, shutdown


async def _recover(bridge, item_id, revision, **changes):
    data = dict(queue_item_id=item_id, project_id="example",
                completed_task_id="N-10", next_task_id="N-11",
                original_journal_revision=revision, actor="pm")
    return await bridge.recover_applied(**(data | changes))


@pytest.mark.asyncio
async def test_real_applied_approval_and_signed_evidence_recover_journal(tmp_path):
    item, _, journal, revision, gateway, close = await _fixture(tmp_path)
    try:
        final = await _recover(gateway(), item.id, revision)
        assert journal.snapshot("example").active_task == "N-11"
        assert any(x.new_state == "DONE" and x.actor == "independent-qa"
                   for x in journal.audit("example"))
        assert final == journal.snapshot("example").revision
        assert await _recover(gateway(), item.id, revision) == final
    finally:
        await close()


@pytest.mark.asyncio
async def test_unapproved_proposal_cannot_mutate_task_journal(tmp_path):
    item, _, journal, revision, gateway, close = await _fixture(tmp_path, apply=False)
    try:
        with pytest.raises(ReconciliationBlocked, match="approved and applied"):
            await _recover(gateway(), item.id, revision)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await close()


@pytest.mark.asyncio
async def test_wrong_external_signature_fails_closed(tmp_path):
    item, stored, journal, revision, gateway, close = await _fixture(tmp_path)
    try:
        signer = Ed25519PrivateKey.generate()
        other = signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        with pytest.raises(ReconciliationBlocked, match="verification"):
            await _recover(gateway(trusted={"independent-qa": other}),
                           item.id, revision)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await close()


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"project_id": "wrong-project"},
    {"completed_task_id": "N-11"},
    {"next_task_id": "N-10"},
    {"actor": "independent-qa"},
    {"original_journal_revision": 900},
    {"queue_item_id": 9000},
])
async def test_wrong_identity_or_audit_context_is_blocked(tmp_path, params):
    item, _, journal, revision, gateway, close = await _fixture(tmp_path)
    try:
        with pytest.raises((ReconciliationBlocked, ValueError)):
            await _recover(gateway(), item.id, revision, **params)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await close()


@pytest.mark.asyncio
async def test_applied_record_missing_decision_metadata_is_blocked(tmp_path):
    item, _, journal, revision, gateway, close = await _fixture(tmp_path)
    try:
        original_gateway = gateway()
        stored = await original_gateway._proposals.get(item.id)
        assert stored is not None
        class FaultyRecordStore:
            async def get(self, item_id):
                assert item_id == item.id
                return replace(stored, decision_ms=None)
        with pytest.raises(ReconciliationBlocked, match="approved and applied"):
            await _recover(gateway(proposal_store=FaultyRecordStore()),
                           item.id, revision)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await close()


@pytest.mark.asyncio
async def test_rejected_or_expired_signed_receipt_is_not_acceptance(tmp_path):
    item, stored, journal, revision, gateway, close = await _fixture(tmp_path)
    try:
        original_gateway = gateway()
        # The external receipt is valid for only its exact approved proposal.
        verifier = original_gateway._source(
            project_id="example", task_id="N-10",
            canonical_revision=stored.source_state_revision,
            journal_revision=revision,
        )
        bad = replace(verifier, expires_at_ms=NOW_MS - 1)
        with pytest.raises(ReconciliationBlocked, match="verification"):
            await _recover(gateway(proof=bad), item.id, revision)
        assert journal.snapshot("example").active_task == "N-10"
    finally:
        await close()
