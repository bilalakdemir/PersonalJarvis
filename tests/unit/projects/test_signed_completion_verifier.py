"""Verifier accepts externally signed task evidence; no test keys leave tmp."""
from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.projects.signed_completion_verifier import (
    SignedCompletionReceipt,
    SignedCompletionVerifier,
)

NOW = 1_791_540_000_000
REV = "a" * 64
DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64


@pytest.fixture
def approved_receipt():
    reviewer = Ed25519PrivateKey.generate()
    public = reviewer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fields = dict(
        project_id="aerion",
        task_id="PM-002",
        canonical_revision=REV,
        journal_revision=8,
        proposal_digest=DIGEST,
        verifier_id="independent-reviewer",
        evidence_ref="github:workflow/37914730341",
        issued_at_ms=NOW - 1000,
        expires_at_ms=NOW + 3_600_000,
        signature_hex="",
    )

    def sign(**changes):
        body = SignedCompletionReceipt(**(fields | changes))
        return replace(body, signature_hex=reviewer.sign(body.signed_bytes()).hex())

    receipt = sign()
    def verifier(given=receipt, *, proposal_digest=DIGEST, public_keys=None, clock=NOW):
        return SignedCompletionVerifier(
            proposal_digest=proposal_digest,
            trusted_public_keys=public_keys if public_keys is not None else
                {"independent-reviewer": public},
            source=lambda **_: given,
            clock_ms=lambda: clock,
        )
    return receipt, sign, verifier


def verify(adapter, **scope):
    return adapter.verify(project_id=scope.get("project_id", "aerion"),
                          task_id=scope.get("task_id", "PM-002"),
                          canonical_revision=scope.get("canonical_revision", REV),
                          journal_revision=scope.get("journal_revision", 8))


def test_valid_independent_signature_yields_scoped_completion(approved_receipt):
    _, _, verifier = approved_receipt
    evidence = verify(verifier())
    assert evidence is not None and evidence.verified
    assert evidence.verifier_id == "independent-reviewer"
    assert evidence.project_id == "aerion"
    assert evidence.journal_revision == 8


def test_unsigned_receipt_does_not_pass(approved_receipt):
    receipt, _, verifier = approved_receipt
    assert verify(verifier(replace(receipt, signature_hex="0" * 128))) is None


@pytest.mark.parametrize("alter", [
    {"evidence_ref": "github:fake"},
    {"task_id": "PM-003"},
    {"project_id": "someone-else"},
    {"journal_revision": 9},
    {"canonical_revision": "d" * 64},
    {"proposal_digest": OTHER_DIGEST},
    {"verifier_id": "pm"},
    {"issued_at_ms": NOW - 6000},
    {"expires_at_ms": NOW + 500},
])
def test_any_unsigned_mutation_rejected(approved_receipt, alter):
    receipt, _, verifier = approved_receipt
    assert verify(verifier(replace(receipt, **alter))) is None


@pytest.mark.parametrize("scope", [
    {"project_id": "not-aerion"},
    {"task_id": "PM-003"},
    {"canonical_revision": "f" * 64},
    {"journal_revision": 9},
])
def test_reviewer_attestation_cannot_cross_scope(approved_receipt, scope):
    _, _, verifier = approved_receipt
    assert verify(verifier(), **scope) is None


def test_correctly_signed_wrong_proposal_denied(approved_receipt):
    _, sign, verifier = approved_receipt
    assert verify(verifier(sign(proposal_digest=OTHER_DIGEST))) is None
    assert verify(verifier(proposal_digest=OTHER_DIGEST)) is None


def test_expired_future_and_overlong_receipts_denied(approved_receipt):
    _, sign, verifier = approved_receipt
    assert verify(verifier(sign(issued_at_ms=NOW - 10_000,
                                expires_at_ms=NOW - 1))) is None
    assert verify(verifier(sign(issued_at_ms=NOW + 1))) is None
    assert verify(verifier(sign(issued_at_ms=NOW - 1,
                                expires_at_ms=NOW + 8 * 86400000))) is None


def test_wrong_trusted_key_fails_closed(approved_receipt):
    receipt, _, verifier = approved_receipt
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert verify(verifier(receipt, public_keys={"independent-reviewer": other})) is None


def test_no_trust_anchor_is_configuration_error(approved_receipt):
    _, _, verifier = approved_receipt
    with pytest.raises(ValueError, match="trust anchor"):
        verifier(public_keys={})


def test_invalid_signature_encoding_rejected(approved_receipt):
    receipt, _, verifier = approved_receipt
    assert verify(verifier(replace(receipt, signature_hex="z" * 128))) is None


def test_receipt_has_no_auto_approval_or_issuance(approved_receipt):
    _, _, verifier = approved_receipt
    adapter = verifier()
    assert not hasattr(adapter, "approve")
    assert not hasattr(adapter, "sign")
    assert not hasattr(adapter, "issue")
