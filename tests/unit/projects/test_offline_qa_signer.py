"""Security contract for reviewer-operated offline QA receipts.

Test keys are disposable; these tests do not establish an actual independent reviewer.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.signed_completion_verifier import SignedCompletionVerifier
from scripts.qa.issue_completion_receipt import (
    Refused,
    issue_reviewed_receipt,
    read_review_template,
)

NOW = int(time.time() * 1000)


def _template() -> dict:
    return {
        "project_id": "aerion",
        "task_id": "PM-002",
        "canonical_revision": "a" * 64,
        "journal_revision": 5,
        "proposal_digest": "b" * 64,
        "verifier_id": "external-reviewer",
        "evidence_ref": "external:reviewed-evidence-123",
        "issued_at_ms": NOW - 1000,
        "expires_at_ms": NOW + 60_000,
    }


def _write(root: Path, obj: dict) -> Path:
    path = root / "unsigned.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_exact_reviewed_template_is_accepted(tmp_path: Path) -> None:
    obj = read_review_template(
        _write(tmp_path, _template()),
        verifier_id="external-reviewer",
        now_ms=NOW,
    )
    assert obj.signature_hex == ""
    assert obj.signed_bytes().startswith(b"AERION/PM/independent-completion/v1")


@pytest.mark.parametrize(
    "change",
    [
        {"journal_revision": True},
        {"journal_revision": -1},
        {"canonical_revision": "x"},
        {"proposal_digest": "c" * 63},
        {"verifier_id": "untrusted"},
        {"evidence_ref": ""},
        {"expires_at_ms": NOW - 10},
        {"issued_at_ms": NOW + 50},
        {"expires_at_ms": NOW + 8 * 24 * 60 * 60 * 1000},
        {"signature_hex": "forged"},
        {"task_id": "../injected"},
    ],
)
def test_bad_or_unreviewed_claim_never_signs(tmp_path: Path, change: dict) -> None:
    with pytest.raises(Refused):
        read_review_template(
            _write(tmp_path, _template() | change),
            verifier_id="external-reviewer",
            now_ms=NOW,
        )


def test_duplicate_properties_cannot_change_review_scope(tmp_path: Path) -> None:
    path = _write(tmp_path, _template())
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"project_id":',
            '"project_id": "injected", "project_id":',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(Refused):
        read_review_template(path, verifier_id="external-reviewer", now_ms=NOW)


def test_missing_independent_signing_key_fails_closed(tmp_path: Path) -> None:
    inbox = tmp_path / "external-inbox"
    inbox.mkdir()
    with pytest.raises(Refused):
        issue_reviewed_receipt(
            template=_write(tmp_path, _template()),
            key_file=tmp_path / "absent.key",
            reviewer_id="external-reviewer",
            receipt_inbox=inbox,
            confirmed_evidence_ref=_template()["evidence_ref"],
            now_ms=NOW,
        )
    assert list(inbox.iterdir()) == []


def test_evidence_confirmation_is_required_before_key_read(tmp_path: Path) -> None:
    inbox = tmp_path / "external-inbox"
    inbox.mkdir()
    with pytest.raises(Refused, match="confirm exact"):
        issue_reviewed_receipt(
            template=_write(tmp_path, _template()),
            key_file=tmp_path / "absent.key",
            reviewer_id="external-reviewer",
            receipt_inbox=inbox,
            confirmed_evidence_ref="unreviewed:evidence",
            now_ms=NOW,
        )
    assert list(inbox.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="secure QA signing is POSIX-only")
def test_disposable_signature_verifies_and_duplicate_is_blocked(tmp_path: Path) -> None:
    # The real reviewer must provision and exclusively own their external key.
    # This temporary test key is NOT an accepted QA trust anchor.
    reviewer = Ed25519PrivateKey.generate()
    private = reviewer.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key = tmp_path / "disposable-reviewer.pem"
    key.write_bytes(private)
    key.chmod(0o600)
    inbox = tmp_path / "reviewer-owned-inbox"
    inbox.mkdir()
    args = {
        "template": _write(tmp_path, _template()),
        "key_file": key,
        "reviewer_id": "external-reviewer",
        "receipt_inbox": inbox,
        "confirmed_evidence_ref": _template()["evidence_ref"],
        "now_ms": NOW,
    }
    path = issue_reviewed_receipt(**args)
    assert path.is_file()
    source = DirectoryCompletionReceiptSource(inbox)
    public = reviewer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    verified = SignedCompletionVerifier(
        proposal_digest="b" * 64,
        trusted_public_keys={"external-reviewer": public},
        source=source,
        clock_ms=lambda: NOW,
    ).verify(
        project_id="aerion",
        task_id="PM-002",
        canonical_revision="a" * 64,
        journal_revision=5,
    )
    assert verified is not None and verified.verified
    with pytest.raises(Refused, match="duplicate"):
        issue_reviewed_receipt(**args)
    assert len(list(inbox.iterdir())) == 1


@pytest.mark.skipif(os.name != "posix", reason="secure QA signing is POSIX-only")
def test_insecure_reviewer_key_mode_is_rejected(tmp_path: Path) -> None:
    reviewer = Ed25519PrivateKey.generate()
    key = tmp_path / "insecure.pem"
    key.write_bytes(
        reviewer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o644)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    with pytest.raises(Refused, match="exclusively own"):
        issue_reviewed_receipt(
            template=_write(tmp_path, _template()),
            key_file=key,
            reviewer_id="external-reviewer",
            receipt_inbox=inbox,
            confirmed_evidence_ref=_template()["evidence_ref"],
            now_ms=NOW,
        )
    assert list(inbox.iterdir()) == []
