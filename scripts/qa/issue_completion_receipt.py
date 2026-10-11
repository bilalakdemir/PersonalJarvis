"""Offline reviewer-owned signing utility for independent PM completion evidence.

This tool is run by an EXTERNAL POSIX reviewer identity, never by AERION PM.
It does not generate keys or automatically approve any completion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import time
from dataclasses import asdict, fields, replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.projects.completion_receipt_inbox import DirectoryCompletionReceiptSource
from jarvis.projects.signed_completion_verifier import SignedCompletionReceipt

ALL_FIELDS = {f.name for f in fields(SignedCompletionReceipt)}
UNSIGNED_FIELDS = ALL_FIELDS - {"signature_hex"}
INTEGER_FIELDS = {"journal_revision", "issued_at_ms", "expires_at_ms"}
SCOPE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
WEEK_MS = 7 * 24 * 60 * 60 * 1000


class Refused(ValueError):
    """Reject invalid external reviewer custody or reviewed evidence."""


def _unique_pairs(pairs):
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise Refused("duplicate JSON property")
        parsed[key] = value
    return parsed


def read_review_template(path: Path, *, verifier_id: str, now_ms: int) -> SignedCompletionReceipt:
    """Require a strictly typed, full, independently reviewed unsigned payload."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise Refused("missing, symlinked or oversized review template")
    try:
        obj = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(Refused("nonfinite JSON")),
        )
    except (ValueError, UnicodeError) as exc:
        raise Refused("malformed review template") from exc
    if not isinstance(obj, dict) or set(obj) != UNSIGNED_FIELDS:
        raise Refused("review template must contain exactly the unsigned receipt fields")
    if any(type(obj[k]) is not int for k in INTEGER_FIELDS):
        raise Refused("invalid integer revision/timestamp")
    if any(type(obj[k]) is not str for k in UNSIGNED_FIELDS - INTEGER_FIELDS):
        raise Refused("invalid string receipt field")
    if (
        SCOPE.fullmatch(verifier_id) is None
        or obj["verifier_id"] != verifier_id
        or SCOPE.fullmatch(obj["project_id"]) is None
        or SCOPE.fullmatch(obj["task_id"]) is None
        or SHA256.fullmatch(obj["canonical_revision"]) is None
        or SHA256.fullmatch(obj["proposal_digest"]) is None
        or obj["journal_revision"] < 0
        or not obj["evidence_ref"].strip()
        or len(obj["evidence_ref"]) > 320
        or obj["issued_at_ms"] > now_ms
        or obj["expires_at_ms"] < now_ms
        or obj["expires_at_ms"] <= obj["issued_at_ms"]
        or obj["expires_at_ms"] - obj["issued_at_ms"] > WEEK_MS
    ):
        raise Refused("review scope or timing invalid")
    return SignedCompletionReceipt(**obj, signature_hex="")


def _read_external_key(file: Path) -> Ed25519PrivateKey:
    """Demand independently owned POSIX key custody; fail closed on Windows."""
    if os.name != "posix":
        raise Refused("external QA signing requires separately managed POSIX account")
    if not file.is_absolute() or file.is_symlink() or not file.is_file():
        raise Refused("signer key must be an absolute regular non-symlink file")
    if file.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise Refused("signing key may not live in AERION repository")
    mode = file.stat()
    if mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise Refused("reviewer must exclusively own key file (mode 0600)")
    if mode.st_size > 8192:
        raise Refused("oversized private key")
    fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_ino != mode.st_ino
            or opened.st_dev != mode.st_dev
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise Refused("key custody changed during inspection")
        key_bytes = stream.read(8193)
    if len(key_bytes) > 8192:
        raise Refused("oversized private key")
    try:
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except (ValueError, TypeError) as exc:
        raise Refused("invalid private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise Refused("key must be Ed25519")
    return key


def issue_reviewed_receipt(
    *,
    template: Path,
    key_file: Path,
    reviewer_id: str,
    receipt_inbox: Path,
    confirmed_evidence_ref: str,
    now_ms: int | None = None,
) -> Path:
    """Sign an exact QA claim only after the separate reviewer inspected evidence."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    record = read_review_template(template, verifier_id=reviewer_id, now_ms=now)
    if confirmed_evidence_ref != record.evidence_ref:
        raise Refused("reviewer must confirm exact externally inspected evidence")
    key = _read_external_key(key_file)
    if (
        not receipt_inbox.is_absolute()
        or receipt_inbox.is_symlink()
        or not receipt_inbox.is_dir()
        or receipt_inbox.resolve().is_relative_to(Path(__file__).resolve().parents[2])
    ):
        raise Refused("external reviewer-controlled inbox must preexist")
    directory = receipt_inbox.stat()
    if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) & 0o022:
        raise Refused("reviewer inbox must not be writable by another identity")
    filename = DirectoryCompletionReceiptSource.filename(
        project_id=record.project_id,
        task_id=record.task_id,
        canonical_revision=record.canonical_revision,
        journal_revision=record.journal_revision,
    )
    signed = replace(record, signature_hex=key.sign(record.signed_bytes()).hex())
    result = json.dumps(asdict(signed), sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(result) > 4096:
        raise Refused("receipt exceeds inbox reader limit")
    target = receipt_inbox / filename
    try:
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o640,
        )
    except FileExistsError as exc:
        raise Refused("duplicate completion receipt refused") from exc
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(result)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Reviewer-owned offline Ed25519 QA receipt signer")
    parser.add_argument("--reviewed-template", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--receipt-inbox", type=Path, required=True)
    parser.add_argument("--confirm-evidence-ref", required=True)
    args = parser.parse_args()
    try:
        file = issue_reviewed_receipt(
            template=args.reviewed_template,
            key_file=args.key_file,
            reviewer_id=args.reviewer_id,
            receipt_inbox=args.receipt_inbox,
            confirmed_evidence_ref=args.confirm_evidence_ref,
        )
    except (Refused, OSError) as exc:
        print("QA_RECEIPT_REFUSED: " + type(exc).__name__)
        return 2
    print("QA_RECEIPT_ISSUED: " + file.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
