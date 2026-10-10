"""Test-only ephemeral crypto fixtures: not real external QA credentials."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.qa_receipt_probe import main, verify_external_receipt
from jarvis.projects.signed_completion_verifier import SignedCompletionReceipt


@pytest.fixture
def signed_inbox(tmp_path):
    inbox = tmp_path / "external-inbox"
    inbox.mkdir()
    reviewer = Ed25519PrivateKey.generate()  # TEST-ONLY, never production QA
    public = reviewer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    public_file = tmp_path / "reviewer-public.raw"
    public_file.write_bytes(public)
    now = int(time.time() * 1000)
    record = SignedCompletionReceipt(
        project_id="aerion",
        task_id="PM-002",
        canonical_revision="a" * 64,
        journal_revision=6,
        proposal_digest="b" * 64,
        verifier_id="separate-reviewer",
        evidence_ref="external:qa-case-10",
        issued_at_ms=now - 1000,
        expires_at_ms=now + 3_600_000,
        signature_hex="",
    )
    source = DirectoryCompletionReceiptSource(inbox)
    path = inbox / source.filename(
        project_id=record.project_id, task_id=record.task_id,
        canonical_revision=record.canonical_revision,
        journal_revision=record.journal_revision,
    )

    def publish(unsigned=record):
        signed = replace(
            unsigned, signature_hex=reviewer.sign(unsigned.signed_bytes()).hex(),
        )
        path.write_text(json.dumps(asdict(signed)), encoding="utf-8")
        return signed

    args = {
        "inbox": inbox,
        "public_key_file": public_file,
        "pinned_public_key_sha256": hashlib.sha256(public).hexdigest(),
        "verifier_id": record.verifier_id,
        "project_id": record.project_id,
        "task_id": record.task_id,
        "canonical_revision": record.canonical_revision,
        "journal_revision": record.journal_revision,
        "proposal_digest": record.proposal_digest,
    }
    return args, publish, path, record


def test_absent_evidence_fails_closed(signed_inbox):
    args, _, _, _ = signed_inbox
    result = verify_external_receipt(**args)
    assert (result.exit_code, result.status) == (2, "RECEIPT_NOT_VERIFIED")


def test_valid_test_fixture_signature_matches_pinned_key_read_only(signed_inbox):
    args, publish, receipt_path, _ = signed_inbox
    publish()
    before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in (args["public_key_file"], receipt_path)
    }
    result = verify_external_receipt(**args)
    assert (result.exit_code, result.status) == (0, "SIGNATURE_VALID")
    after = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in before
    }
    assert before == after


def test_wrong_public_key_fingerprint_is_configuration_failure(signed_inbox):
    args, publish, _, _ = signed_inbox
    publish()
    result = verify_external_receipt(
        **(args | {"pinned_public_key_sha256": "c" * 64}),
    )
    assert result.exit_code == 3


def test_missing_or_truncated_public_key_is_not_trusted(signed_inbox):
    args, publish, _, _ = signed_inbox
    publish()
    args["public_key_file"].write_bytes(b"short")
    assert verify_external_receipt(**args).exit_code == 3
    args["public_key_file"].unlink()
    assert verify_external_receipt(**args).exit_code == 3


def test_missing_external_inbox_is_not_auto_created(signed_inbox):
    args, _, _, _ = signed_inbox
    missing = args["inbox"] / "missing"
    assert verify_external_receipt(**(args | {"inbox": missing})).exit_code == 3
    assert not missing.exists()


@pytest.mark.parametrize("changed", [
    {"project_id": "another"},
    {"task_id": "PM-003"},
    {"canonical_revision": "c" * 64},
    {"journal_revision": 7},
    {"proposal_digest": "d" * 64},
    {"verifier_id": "impostor"},
])
def test_cross_scope_and_cross_proposal_cannot_reuse_signature(signed_inbox, changed):
    args, publish, _, _ = signed_inbox
    publish()
    assert verify_external_receipt(**(args | changed)).exit_code == 2


def test_tampered_receipt_rejected(signed_inbox):
    args, publish, path, _ = signed_inbox
    signed = publish()
    fields = asdict(signed)
    fields["evidence_ref"] = "external:tampered"
    path.write_text(json.dumps(fields), encoding="utf-8")
    assert verify_external_receipt(**args).exit_code == 2


def test_expired_receipt_rejected(signed_inbox):
    args, publish, _, record = signed_inbox
    publish(replace(
        record,
        issued_at_ms=record.issued_at_ms - 10_000_000,
        expires_at_ms=record.issued_at_ms - 5_000_000,
    ))
    assert verify_external_receipt(**args).exit_code == 2


def test_corrupt_receipt_rejected(signed_inbox):
    args, _, path, _ = signed_inbox
    path.write_text('{"status": invalid', encoding="utf-8")
    assert verify_external_receipt(**args).exit_code == 2


def test_cli_output_uses_status_only_not_evidence_or_key(signed_inbox, capsys):
    args, publish, _, _ = signed_inbox
    publish()
    flags = []
    for key, value in args.items():
        flags.extend(("--" + key.replace("_", "-"), str(value)))
    code = main(flags)
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"status": "SIGNATURE_VALID"}
    assert "external:qa-case-10" not in captured.out
    assert captured.err == ""
