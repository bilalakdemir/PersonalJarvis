"""WebAuthn approval plumbing tests; all keys and SQLite files are disposable.

These tests exercise the *boundary* through the established py_webauthn
interface; crypto signature validation is performed by that library itself.
Production never accepts a mocked verifier or a test enrollment secret.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.memory.project_approval_passkeys as pa

NOW = 1_791_550_000_000
SECRET = "local-only-disposable-test-bootstrap-" + "x" * 20
PROJECT = "example"
QUEUE = 17
TRANSACTION = "tx-123"
DIGEST = "a" * 64
IDENTITY = b"verified-passkey-owner-id"


@pytest.fixture
def proof_store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AERION_PASSKEY_ENROLLMENT_SECRET", SECRET)
    db = tmp_path / "memory.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE memory_project_state_proposals(id INTEGER PRIMARY KEY)"
        )
        migration = (
            Path(__file__).parents[3]
            / "jarvis/memory/migrations/0014_project_state_approver_provenance.sql"
        )
        conn.executescript(migration.read_text(encoding="utf-8"))
    store = pa.ProjectApprovalPasskeys(
        db, rp_id="localhost", origin="http://localhost:8384", now_ms=lambda: NOW,
    )
    return store, db


def _enroll(store, monkeypatch):
    options = store.begin_enrollment(bootstrap_secret=SECRET)
    assert options["challenge"]
    calls = []

    def verified(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            credential_id=IDENTITY, credential_public_key=b"disposable-public-key",
            sign_count=0,
        )

    monkeypatch.setattr(pa, "verify_registration_response", verified)
    identity = store.finish_enrollment(
        bootstrap_secret=SECRET,
        challenge=options["challenge"],
        credential={"id": "test-credential"},
    )
    assert identity == pa._b64(IDENTITY)
    assert calls[0]["require_user_verification"] is True
    assert calls[0]["expected_origin"] == "http://localhost:8384"
    return options


def _approval(store, monkeypatch):
    options = store.begin_approval(
        project_id=PROJECT, queue_item_id=QUEUE,
        transaction_id=TRANSACTION, proposal_digest=DIGEST,
    )
    calls = []

    def verified(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            credential_id=IDENTITY, new_sign_count=1,
        )

    monkeypatch.setattr(pa, "verify_authentication_response", verified)
    credential = {"id": pa._b64(IDENTITY), "response": {"clientDataJSON": "dummy"}}
    return options, credential, calls


def test_enrollment_rejects_missing_or_invalid_bootstrap(proof_store, monkeypatch):
    store, _ = proof_store
    with pytest.raises(pa.PasskeyApprovalError, match="not authorized"):
        store.begin_enrollment(bootstrap_secret="")
    monkeypatch.delenv("AERION_PASSKEY_ENROLLMENT_SECRET")
    with pytest.raises(pa.PasskeyApprovalError, match="not authorized"):
        store.begin_enrollment(bootstrap_secret=SECRET)


def test_enrollment_requires_user_verification_and_is_one_time(proof_store, monkeypatch):
    store, _ = proof_store
    options = _enroll(store, monkeypatch)
    assert store.enrolled()
    with pytest.raises(pa.PasskeyApprovalError, match="already enrolled"):
        store.begin_enrollment(bootstrap_secret=SECRET)
    with pytest.raises(pa.PasskeyApprovalError, match="used"):
        store.finish_enrollment(
            bootstrap_secret=SECRET, challenge=options["challenge"],
            credential={"id": "test-credential"},
        )


def test_one_use_scope_bound_owner_assertion(proof_store, monkeypatch):
    store, db_path = proof_store
    _enroll(store, monkeypatch)
    opts, credential, calls = _approval(store, monkeypatch)
    grant = store.finish_approval(
        challenge=opts["challenge"], credential=credential,
        project_id=PROJECT, queue_item_id=QUEUE,
        transaction_id=TRANSACTION, proposal_digest=DIGEST,
    )
    assert grant.credential_id == pa._b64(IDENTITY)
    assert len(grant.assertion_digest) == 64
    assert calls[0]["require_user_verification"] is True
    assert calls[0]["expected_rp_id"] == "localhost"
    assert calls[0]["credential_current_sign_count"] == 0
    with pytest.raises(pa.PasskeyApprovalError, match="used"):
        store.finish_approval(
            challenge=opts["challenge"], credential=credential,
            project_id=PROJECT, queue_item_id=QUEUE,
            transaction_id=TRANSACTION, proposal_digest=DIGEST,
        )
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT sign_count FROM memory_pm_owner_passkeys"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("wrong", [
    {"project_id": "another-project"},
    {"queue_item_id": QUEUE + 1},
    {"transaction_id": "tx-wrong"},
    {"proposal_digest": "b" * 64},
])
def test_credential_cannot_approve_other_transaction(
    proof_store, monkeypatch, wrong,
):
    store, _ = proof_store
    _enroll(store, monkeypatch)
    opts, cred, _ = _approval(store, monkeypatch)
    scope = dict(
        project_id=PROJECT, queue_item_id=QUEUE,
        transaction_id=TRANSACTION, proposal_digest=DIGEST,
    )
    scope.update(wrong)
    with pytest.raises(pa.PasskeyApprovalError, match="exact project transaction"):
        store.finish_approval(
            challenge=opts["challenge"], credential=cred, **scope,
        )


def test_unregistered_credential_is_denied(proof_store, monkeypatch):
    store, _ = proof_store
    _enroll(store, monkeypatch)
    opts, cred, _ = _approval(store, monkeypatch)
    cred["id"] = pa._b64(b"unknown")
    with pytest.raises(pa.PasskeyApprovalError, match="unregistered"):
        store.finish_approval(
            challenge=opts["challenge"], credential=cred,
            project_id=PROJECT, queue_item_id=QUEUE,
            transaction_id=TRANSACTION, proposal_digest=DIGEST,
        )


def test_expired_challenge_is_rejected(proof_store, monkeypatch):
    store, db_path = proof_store
    _enroll(store, monkeypatch)
    opts, cred, _ = _approval(store, monkeypatch)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE memory_pm_passkey_challenges SET expires_ms=0 WHERE kind='approve'"
        )
    with pytest.raises(pa.PasskeyApprovalError, match="expired"):
        store.finish_approval(
            challenge=opts["challenge"], credential=cred,
            project_id=PROJECT, queue_item_id=QUEUE,
            transaction_id=TRANSACTION, proposal_digest=DIGEST,
        )


def test_restart_keeps_owner_key_without_accepting_replayed_challenge(
    proof_store, monkeypatch,
):
    store, db_path = proof_store
    _enroll(store, monkeypatch)
    opts, credential, _ = _approval(store, monkeypatch)
    store.finish_approval(
        challenge=opts["challenge"], credential=credential,
        project_id=PROJECT, queue_item_id=QUEUE,
        transaction_id=TRANSACTION, proposal_digest=DIGEST,
    )
    restarted = pa.ProjectApprovalPasskeys(
        db_path, rp_id="localhost", origin="http://localhost:8384",
        now_ms=lambda: NOW,
    )
    assert restarted.enrolled()
    with pytest.raises(pa.PasskeyApprovalError, match="used"):
        restarted.finish_approval(
            challenge=opts["challenge"], credential=credential,
            project_id=PROJECT, queue_item_id=QUEUE,
            transaction_id=TRANSACTION, proposal_digest=DIGEST,
        )


def test_invalid_origin_and_rp_are_rejected(proof_store):
    _, db = proof_store
    for rp, origin in (
        ("example.com", "http://example.com"),
        ("localhost", "https://evil.example"),
        ("", "https://localhost"),
    ):
        with pytest.raises(ValueError, match="pinned"):
            pa.ProjectApprovalPasskeys(db, rp_id=rp, origin=origin)
