"""Read-only independent receipt delivery tests; transient keys never leave tmp."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.signed_completion_verifier import (
    SignedCompletionReceipt,
    SignedCompletionVerifier,
)

NOW = 1_791_540_000_000
REV = "a" * 64
DIGEST = "b" * 64
LOOKUP = {
    "project_id": "aerion",
    "task_id": "PM-002",
    "canonical_revision": REV,
    "journal_revision": 8,
}


@pytest.fixture
def verified_inbox(tmp_path):
    reviewer = Ed25519PrivateKey.generate()
    public = reviewer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    source = DirectoryCompletionReceiptSource(tmp_path)
    unsigned = SignedCompletionReceipt(
        **LOOKUP, proposal_digest=DIGEST, verifier_id="independent-reviewer",
        evidence_ref="external:qa-results/678", issued_at_ms=NOW - 1000,
        expires_at_ms=NOW + 3600000, signature_hex="",
    )

    def publish(body=unsigned, *, signing_key=reviewer):
        # TEST-ONLY publication by a disposable independent crypto identity.
        signed = replace(body, signature_hex=signing_key.sign(body.signed_bytes()).hex())
        name = source.filename(**LOOKUP)
        (tmp_path / name).write_text(
            json.dumps(asdict(signed), sort_keys=True), encoding="utf-8",
        )
        return signed

    verifier = SignedCompletionVerifier(
        proposal_digest=DIGEST,
        trusted_public_keys={"independent-reviewer": public},
        source=source,
        clock_ms=lambda: NOW,
    )
    return source, verifier, publish, tmp_path, unsigned


def _verify(verifier, **overrides):
    return verifier.verify(**(LOOKUP | overrides))


def test_absent_receipt_fails_closed(verified_inbox):
    _, verifier, _, _, _ = verified_inbox
    assert _verify(verifier) is None


def test_external_signed_receipt_read_and_verified(verified_inbox):
    source, verifier, publish, _, _ = verified_inbox
    signed = publish()
    assert source(**LOOKUP) == signed
    evidence = _verify(verifier)
    assert evidence is not None
    assert evidence.verified and evidence.verifier_id == "independent-reviewer"
    assert evidence.evidence_ref == "external:qa-results/678"


def test_source_never_creates_or_modifies_files(verified_inbox):
    source, _, _, directory, _ = verified_inbox
    before = list(directory.iterdir())
    assert source(**LOOKUP) is None
    assert list(directory.iterdir()) == before
    assert not any(hasattr(source, action) for action in ("sign", "issue", "approve", "write"))


@pytest.mark.parametrize("scope", [
    {"project_id": "../aerion"},
    {"task_id": "PM/002"},
    {"canonical_revision": "bad"},
    {"journal_revision": -1},
    {"journal_revision": True},
    {"project_id": ""},
])
def test_invalid_identity_can_never_be_a_path(verified_inbox, scope):
    source, _, publish, directory, _ = verified_inbox
    publish()
    assert source(**(LOOKUP | scope)) is None
    assert len(list(directory.iterdir())) == 1


@pytest.mark.parametrize("scope", [
    {"project_id": "other-project"},
    {"task_id": "PM-003"},
    {"canonical_revision": "c" * 64},
    {"journal_revision": 9},
])
def test_different_valid_scope_cannot_reuse_receipt(verified_inbox, scope):
    source, verifier, publish, _, _ = verified_inbox
    publish()
    assert source(**(LOOKUP | scope)) is None
    assert _verify(verifier, **scope) is None


def test_wrong_key_fails_cryptographic_check(verified_inbox):
    _, verifier, publish, _, _ = verified_inbox
    impostor = Ed25519PrivateKey.generate()
    publish(signing_key=impostor)
    assert _verify(verifier) is None


def test_modified_payload_fails_cryptographic_check(verified_inbox):
    source, verifier, publish, folder, _ = verified_inbox
    publish()
    path = folder / source.filename(**LOOKUP)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["evidence_ref"] = "external:fake"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(verifier) is None


@pytest.mark.parametrize("payload", [
    "not json",
    "{}",
    "[]",
    '{"project_id":"a","project_id":"b"}',
    "[]" * 3000,
])
def test_corrupt_or_oversized_receipts_fail_closed(verified_inbox, payload):
    source, verifier, _, folder, _ = verified_inbox
    (folder / source.filename(**LOOKUP)).write_text(payload, encoding="utf-8")
    assert source(**LOOKUP) is None
    assert _verify(verifier) is None


def test_boolean_integer_metadata_is_rejected(verified_inbox):
    source, verifier, publish, folder, _ = verified_inbox
    signed = publish()
    fields = asdict(signed)
    fields["journal_revision"] = True
    (folder / source.filename(**LOOKUP)).write_text(json.dumps(fields), encoding="utf-8")
    assert source(**LOOKUP) is None
    assert _verify(verifier) is None


def test_missing_or_relative_inbox_refused(tmp_path):
    with pytest.raises(ValueError, match="preexist"):
        DirectoryCompletionReceiptSource(tmp_path / "missing")
    with pytest.raises(ValueError, match="preexist"):
        DirectoryCompletionReceiptSource(Path("relative-inbox"))


def test_symlinked_receipt_not_used(verified_inbox):
    source, verifier, publish, folder, _ = verified_inbox
    publish()
    file = folder / source.filename(**LOOKUP)
    original = folder / "separate.json"
    file.replace(original)
    try:
        file.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation restricted by host security policy")
    assert source(**LOOKUP) is None
    assert _verify(verifier) is None
