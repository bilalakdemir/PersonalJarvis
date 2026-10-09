"""Real ES256 WebAuthn + durable project approval integration (temp files only).

No mock verification functions. The test authenticator is ephemeral, held only
by pytest, and not part of production. No owner credentials are enrolled.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from jarvis.memory.project_approval_passkeys import (
    ProjectApprovalPasskeys, PasskeyApprovalError,
)
from jarvis.memory.project_state_approval import (
    ProjectStateProposalLifecycleError, ProjectStateProposalStore,
)
from tests.unit.memory.test_project_passkey_crypto import (
    _registration, _assertion, RP, ORIGIN, SECRET,
)
from tests.unit.memory.test_project_state_approval import _setup

from cryptography.hazmat.primitives.asymmetric import ec


async def _prepared(tmp_path, monkeypatch):
    root, journal, queue, item, lifecycle, db_path = await _setup(tmp_path)
    stored = await lifecycle.prepare(item.id)
    monkeypatch.setenv("AERION_PASSKEY_ENROLLMENT_SECRET", SECRET)
    store = ProjectApprovalPasskeys(db_path, rp_id=RP, origin=ORIGIN)
    private = ec.generate_private_key(ec.SECP256R1())
    credential_id = b"disposable-owner-approval-credential"
    enrollment = store.begin_enrollment(bootstrap_secret=SECRET)
    store.finish_enrollment(
        bootstrap_secret=SECRET, challenge=enrollment["challenge"],
        credential=_registration(private, credential_id, enrollment["challenge"]),
    )
    return root, journal, queue, item, lifecycle, db_path, stored, store, private, credential_id


async def _close(journal, queue, lifecycle):
    await lifecycle.close()
    await queue.close()
    journal.close()


def _proof(store, proposal, item, signer, cred_id):
    options = store.begin_approval(
        project_id="example", queue_item_id=item.id,
        transaction_id=proposal.transaction_id,
        proposal_digest=proposal.proposal_digest,
    )
    return store.finish_approval(
        challenge=options["challenge"],
        credential=_assertion(signer, cred_id, options["challenge"]),
        project_id="example", queue_item_id=item.id,
        transaction_id=proposal.transaction_id,
        proposal_digest=proposal.proposal_digest,
    )


@pytest.mark.asyncio
async def test_signed_owner_decision_consumed_and_provenance_is_durable(
    tmp_path, monkeypatch,
):
    root, journal, queue, item, lifecycle, db_path, proposal, store, signer, cid = (
        await _prepared(tmp_path, monkeypatch)
    )
    try:
        assert (root / "STATE.md").exists()
        grant = _proof(store, proposal, item, signer, cid)
        approved = await lifecycle.approve_and_apply(
            queue_item_id=item.id,
            transaction_id=proposal.transaction_id,
            proposal_digest=proposal.proposal_digest,
            owner_passkey_grant=grant.token,
            require_owner_passkey=True,
        )
        assert approved.status == "COMMITTED"
        durable = ProjectStateProposalStore(db_path)
        record = await durable.get(item.id)
        assert record.status == "applied"
        assert record.approved_via == "webauthn"
        assert record.approved_by_credential_id == grant.credential_id
        assert record.approval_assertion_digest == grant.assertion_digest
        with sqlite3.connect(db_path) as db:
            used = db.execute(
                "SELECT consumed_ms FROM memory_pm_passkey_grants LIMIT 1"
            ).fetchone()[0]
        assert used is not None
        await durable.close()

        # Reopened approval lifecycle can only replay the exact prior decision,
        # not mint a new owner assertion for an unrelated proposal.
        recheck = await lifecycle.approve_and_apply(
            queue_item_id=item.id,
            transaction_id=proposal.transaction_id,
            proposal_digest=proposal.proposal_digest,
            require_owner_passkey=True,
        )
        assert recheck.status == "COMMITTED"
    finally:
        await _close(journal, queue, lifecycle)


@pytest.mark.asyncio
async def test_no_human_passkey_grant_cannot_apply_new_project_decision(
    tmp_path, monkeypatch,
):
    root, journal, queue, item, lifecycle, db_path, proposal, *_ = (
        await _prepared(tmp_path, monkeypatch)
    )
    try:
        old_state = (root / "STATE.md").read_bytes()
        with pytest.raises(ProjectStateProposalLifecycleError, match="WebAuthn"):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=proposal.transaction_id,
                proposal_digest=proposal.proposal_digest,
                require_owner_passkey=True,
            )
        assert (root / "STATE.md").read_bytes() == old_state
        row = await lifecycle._proposals.get(item.id)
        assert row.status == "awaiting-approval"
    finally:
        await _close(journal, queue, lifecycle)


@pytest.mark.asyncio
async def test_verified_grant_rejects_wrong_proposal_without_consuming_it(
    tmp_path, monkeypatch,
):
    root, journal, queue, item, lifecycle, db_path, proposal, store, signer, cid = (
        await _prepared(tmp_path, monkeypatch)
    )
    try:
        grant = _proof(store, proposal, item, signer, cid)
        with pytest.raises(ProjectStateProposalLifecycleError):
            await lifecycle.approve_and_apply(
                queue_item_id=item.id,
                transaction_id=proposal.transaction_id,
                proposal_digest="0" * 64,
                owner_passkey_grant=grant.token,
                require_owner_passkey=True,
            )
        with sqlite3.connect(db_path) as db:
            row = db.execute(
                "SELECT consumed_ms FROM memory_pm_passkey_grants LIMIT 1"
            ).fetchone()
        assert row[0] is None
        assert (root / "STATE.md").exists()
    finally:
        await _close(journal, queue, lifecycle)
