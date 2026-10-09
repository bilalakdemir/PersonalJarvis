"""Verify externally signed PM completion evidence, never mint it.

The Project Manager only holds pinned independent reviewers' Ed25519 PUBLIC keys.
The signing keys and the issuance workflow must remain outside PM authority.
No configured trust anchor or valid external receipt means verification fails.
Human approval of canonical file changes is a separate mandatory gate.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .task_reconciliation import CompletionEvidence

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERIFIER_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
_DOMAIN = b"AERION/PM/independent-completion/v1\x00"
_MAX_VALIDITY_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class SignedCompletionReceipt:
    """Exact attestation payload signed by an out-of-process reviewer."""

    project_id: str
    task_id: str
    canonical_revision: str
    journal_revision: int
    proposal_digest: str
    verifier_id: str
    evidence_ref: str
    issued_at_ms: int
    expires_at_ms: int
    signature_hex: str

    def signed_bytes(self) -> bytes:
        """Stable, domain-separated message. Signature itself is excluded."""
        fields = {
            "canonical_revision": self.canonical_revision,
            "evidence_ref": self.evidence_ref,
            "expires_at_ms": self.expires_at_ms,
            "issued_at_ms": self.issued_at_ms,
            "journal_revision": self.journal_revision,
            "project_id": self.project_id,
            "proposal_digest": self.proposal_digest,
            "task_id": self.task_id,
            "verifier_id": self.verifier_id,
        }
        return _DOMAIN + json.dumps(
            fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


class CompletionReceiptSource(Protocol):
    """Read independently issued evidence; no issue/approve operation here."""

    def __call__(
        self, *, project_id: str, task_id: str, canonical_revision: str,
        journal_revision: int,
    ) -> SignedCompletionReceipt | None: ...


class SignedCompletionVerifier:
    """Fail-closed CompletionVerifier for exact approved proposal identity.

    Public keys are pinned in trusted external configuration, never taken from
    the receipt. The receipt issuer is independent of the PM; these signatures
    cannot be created by this module. Without an external signer, deny.
    """

    def __init__(
        self, *, proposal_digest: str, trusted_public_keys: Mapping[str, bytes],
        source: CompletionReceiptSource,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(proposal_digest, str) or _SHA256.fullmatch(proposal_digest) is None:
            raise ValueError("approved proposal_digest must be a SHA-256 hex digest")
        if not trusted_public_keys:
            raise ValueError("at least one independently managed trust anchor is required")
        keys: dict[str, Ed25519PublicKey] = {}
        for reviewer, public_key in trusted_public_keys.items():
            if not isinstance(reviewer, str) or _VERIFIER_ID.fullmatch(reviewer) is None:
                raise ValueError("invalid trusted reviewer identity")
            if not isinstance(public_key, bytes):
                raise ValueError("trusted public keys must be raw bytes")
            keys[reviewer] = Ed25519PublicKey.from_public_bytes(public_key)
        self._keys = keys
        self._proposal_digest = proposal_digest
        self._source = source
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def verify(
        self, *, project_id: str, task_id: str,
        canonical_revision: str, journal_revision: int,
    ) -> CompletionEvidence | None:
        """Only a matching signed and time-bounded attestation can pass."""
        try:
            receipt = self._source(
                project_id=project_id,
                task_id=task_id,
                canonical_revision=canonical_revision,
                journal_revision=journal_revision,
            )
            if receipt is None or not isinstance(receipt, SignedCompletionReceipt):
                return None
            if (
                receipt.project_id != project_id
                or receipt.task_id != task_id
                or receipt.canonical_revision != canonical_revision
                or receipt.journal_revision != journal_revision
                or receipt.proposal_digest != self._proposal_digest
                or type(receipt.journal_revision) is not int
                or receipt.journal_revision < 0
                or _SHA256.fullmatch(receipt.canonical_revision) is None
                or receipt.verifier_id not in self._keys
                or not isinstance(receipt.evidence_ref, str)
                or not receipt.evidence_ref.strip()
                or len(receipt.evidence_ref) > 320
                or type(receipt.issued_at_ms) is not int
                or type(receipt.expires_at_ms) is not int
                or not isinstance(receipt.signature_hex, str)
                or len(receipt.signature_hex) != 128
            ):
                return None
            now_ms = self._clock_ms()
            if (
                type(now_ms) is not int
                or receipt.issued_at_ms > now_ms
                or receipt.expires_at_ms < now_ms
                or receipt.expires_at_ms <= receipt.issued_at_ms
                or receipt.expires_at_ms - receipt.issued_at_ms > _MAX_VALIDITY_MS
            ):
                return None
            signature = bytes.fromhex(receipt.signature_hex)
            self._keys[receipt.verifier_id].verify(signature, receipt.signed_bytes())
            return CompletionEvidence(
                project_id=project_id,
                task_id=task_id,
                canonical_revision=canonical_revision,
                journal_revision=journal_revision,
                verifier_id=receipt.verifier_id,
                evidence_ref=receipt.evidence_ref,
                verified=True,
            )
        except (InvalidSignature, ValueError, TypeError, AttributeError):
            return None
