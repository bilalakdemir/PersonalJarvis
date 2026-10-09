"""Real py_webauthn cryptography with disposable in-memory ES256 test keys.

No mock registration/authentication verifier: COSE, rpId, challenge, origin,
UV flag, and ECDSA signature are all checked by actual py_webauthn.
Test-only authenticator simulator is not shipped to production.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

from jarvis.memory.project_approval_passkeys import (
    PasskeyApprovalError, ProjectApprovalPasskeys, _b64,
)

NOW = 1_791_550_000_000
SECRET = "disposable-fixture-secret-that-is-never-production"
RP = "localhost"
ORIGIN = "http://localhost:8384"


def _credential_data(private, credential_id):
    pub = private.public_key().public_numbers()
    cose = {1: 2, 3: -7, -1: 1, -2: pub.x.to_bytes(32, "big"),
            -3: pub.y.to_bytes(32, "big")}
    return (bytes(16) + struct.pack("!H", len(credential_id)) +
            credential_id + cbor2.dumps(cose))


def _auth_data(flags: int, counter: int, extension: bytes = b""):
    return hashlib.sha256(RP.encode()).digest() + bytes([flags]) + struct.pack(
        "!I", counter
    ) + extension


def _client_data(kind: str, challenge: str, origin: str = ORIGIN):
    return json.dumps(
        {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
        separators=(",", ":"),
    ).encode("utf-8")


def _registration(private, credential_id: bytes, challenge: str, *, user_verified=True):
    client = _client_data("webauthn.create", challenge)
    flags = 0x01 | 0x40 | (0x04 if user_verified else 0)
    auth_data = _auth_data(flags, 0, _credential_data(private, credential_id))
    att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
    return {
        "id": _b64(credential_id), "rawId": _b64(credential_id), "type": "public-key",
        "response": {
            "clientDataJSON": _b64(client),
            "attestationObject": _b64(att_obj),
        },
        "clientExtensionResults": {},
    }


def _assertion(private, credential_id, challenge, *, user_verified=True,
               wrong_origin=False, incorrect_signature=False, counter=1):
    client = _client_data(
        "webauthn.get", challenge,
        origin=("http://evil.example" if wrong_origin else ORIGIN),
    )
    flags = 0x01 | (0x04 if user_verified else 0)
    auth = _auth_data(flags, counter)
    signed = auth + hashlib.sha256(client).digest()
    signer = ec.generate_private_key(ec.SECP256R1()) if incorrect_signature else private
    signature = signer.sign(signed, ec.ECDSA(hashes.SHA256()))
    return {
        "id": _b64(credential_id), "rawId": _b64(credential_id), "type": "public-key",
        "response": {
            "authenticatorData": _b64(auth), "clientDataJSON": _b64(client),
            "signature": _b64(signature),
            "userHandle": None,
        },
        "clientExtensionResults": {},
    }


@pytest.fixture
def real_store(tmp_path: Path, monkeypatch):
    dbpath = tmp_path / "cryptographic.sqlite3"
    monkeypatch.setenv("AERION_PASSKEY_ENROLLMENT_SECRET", SECRET)
    migration = (
        Path(__file__).parents[3] /
        "jarvis/memory/migrations/0014_project_state_approver_provenance.sql"
    )
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE memory_project_state_proposals(id INTEGER PRIMARY KEY)")
        db.executescript(migration.read_text(encoding="utf-8"))
    store = ProjectApprovalPasskeys(
        dbpath, rp_id=RP, origin=ORIGIN, now_ms=lambda: NOW,
    )
    private = ec.generate_private_key(ec.SECP256R1())
    credential_id = b"independent-test-owner-credential"
    enrollment = store.begin_enrollment(bootstrap_secret=SECRET)
    store.finish_enrollment(
        bootstrap_secret=SECRET, challenge=enrollment["challenge"],
        credential=_registration(private, credential_id, enrollment["challenge"]),
    )
    return store, private, credential_id


def _begin(store):
    opts = store.begin_approval(
        project_id="alpha", queue_item_id=9,
        transaction_id="tx-abc", proposal_digest="f" * 64,
    )
    return opts["challenge"]


def _finish(store, challenge, assertion):
    return store.finish_approval(
        challenge=challenge, credential=assertion,
        project_id="alpha", queue_item_id=9,
        transaction_id="tx-abc", proposal_digest="f" * 64,
    )


def test_real_es256_enrollment_and_approval(real_store):
    store, signer, cid = real_store
    challenge = _begin(store)
    verified = _finish(store, challenge, _assertion(signer, cid, challenge))
    assert verified.credential_id == _b64(cid)
    assert len(verified.token) > 30
    with pytest.raises(PasskeyApprovalError, match="used"):
        _finish(store, challenge, _assertion(signer, cid, challenge))


@pytest.mark.parametrize("change", [
    {"wrong_origin": True},
    {"user_verified": False},
    {"incorrect_signature": True},
])
def test_real_webauthn_rejects_tampered_origin_uv_and_signature(real_store, change):
    store, signer, cid = real_store
    challenge = _begin(store)
    with pytest.raises(PasskeyApprovalError, match="verification failed"):
        _finish(store, challenge, _assertion(signer, cid, challenge, **change))


def test_replayed_credential_counter_refused(real_store):
    store, signer, cid = real_store
    c1 = _begin(store)
    _finish(store, c1, _assertion(signer, cid, c1, counter=1))
    c2 = _begin(store)
    with pytest.raises(PasskeyApprovalError, match="verification failed"):
        _finish(store, c2, _assertion(signer, cid, c2, counter=1))


def test_registration_requires_uv_even_with_valid_es256_key(tmp_path, monkeypatch):
    dbpath = tmp_path / "non_uv.sqlite3"
    monkeypatch.setenv("AERION_PASSKEY_ENROLLMENT_SECRET", SECRET)
    migration = Path(__file__).parents[3] / (
        "jarvis/memory/migrations/0014_project_state_approver_provenance.sql"
    )
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE memory_project_state_proposals(id INTEGER PRIMARY KEY)")
        db.executescript(migration.read_text(encoding="utf-8"))
    store = ProjectApprovalPasskeys(
        dbpath, rp_id=RP, origin=ORIGIN, now_ms=lambda: NOW,
    )
    challenge = store.begin_enrollment(bootstrap_secret=SECRET)["challenge"]
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(PasskeyApprovalError, match="enrollment verification failed"):
        store.finish_enrollment(
            bootstrap_secret=SECRET, challenge=challenge,
            credential=_registration(key, b"not-verified", challenge,
                                     user_verified=False),
        )
