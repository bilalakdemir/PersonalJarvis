"""Owner-verified, transaction-scoped WebAuthn evidence for project approvals.

No signing key is stored by Jarvis; only authenticator public keys are retained.
Registration requires an independently provisioned local enrollment secret.
Verification never approves a proposal: it grants one short-lived durable
capability, consumed atomically by the existing approval lifecycle.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

_CHALLENGE_TTL_MS = 120_000
_GRANT_TTL_MS = 45_000


class PasskeyApprovalError(ValueError):
    """This identity/approval proof cannot safely proceed."""


@dataclass(frozen=True)
class VerifiedPasskeyGrant:
    """Returned ONLY after a verified WebAuthn response. Never sent to browser."""

    token: str
    credential_id: str
    assertion_digest: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class ProjectApprovalPasskeys:
    """One pinned owner credential and exact, single-use challenge/grant ledger.

    Requires the existing N-14 memory schema migrations to have run. All reads
    and writes are isolated to passkey tables; canonical Markdown is untouched.
    """

    def __init__(
        self, db_path: str | Path, *, rp_id: str, origin: str,
        now_ms=None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            not rp_id or parsed.hostname != rp_id
            or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or parsed.scheme not in ("http", "https")
            or (parsed.scheme == "http" and rp_id not in ("localhost", "127.0.0.1"))
        ):
            raise ValueError("passkey origin and RP ID must be pinned to HTTPS or localhost")
        self._db_path = Path(db_path)
        self._rp_id = rp_id
        self._origin = origin.rstrip("/")
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    @staticmethod
    def _enrollment_allowed(secret: str) -> None:
        configured = os.environ.get("AERION_PASSKEY_ENROLLMENT_SECRET", "")
        if (
            len(configured) < 24
            or not isinstance(secret, str)
            or not hmac.compare_digest(secret, configured)
        ):
            raise PasskeyApprovalError("owner passkey enrollment not authorized")

    def _new_challenge(self, kind: str, *, project_id=None, queue_item_id=None,
                       transaction_id=None, proposal_digest=None) -> bytes:
        challenge = secrets.token_bytes(32)
        now = self._now_ms()
        with self._connect() as db:
            db.execute(
                """INSERT INTO memory_pm_passkey_challenges
                (challenge_hash,kind,project_id,queue_item_id,
                 transaction_id,proposal_digest,expires_ms)
                VALUES (?,?,?,?,?,?,?)""",
                (_hash(_b64(challenge)), kind, project_id, queue_item_id,
                 transaction_id, proposal_digest, now + _CHALLENGE_TTL_MS),
            )
        return challenge

    def _pending(self, challenge: str, kind: str) -> tuple:
        if not isinstance(challenge, str) or not challenge:
            raise PasskeyApprovalError("missing challenge")
        with self._connect() as db:
            row = db.execute(
                """SELECT project_id,queue_item_id,transaction_id,
                          proposal_digest,expires_ms,used_ms
                   FROM memory_pm_passkey_challenges
                   WHERE challenge_hash=? AND kind=?""",
                (_hash(challenge), kind),
            ).fetchone()
        if not row or row[5] is not None or row[4] <= self._now_ms():
            raise PasskeyApprovalError("missing, used, or expired passkey challenge")
        return row

    def enrolled(self) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM memory_pm_owner_passkeys LIMIT 1"
            ).fetchone() is not None

    def begin_enrollment(self, *, bootstrap_secret: str) -> dict:
        self._enrollment_allowed(bootstrap_secret)
        if self.enrolled():
            raise PasskeyApprovalError("initial owner credential already enrolled")
        challenge = self._new_challenge("enroll")
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name="AERION",
            user_id=secrets.token_bytes(32),
            user_name="AERION owner",
            user_display_name="AERION Owner",
            challenge=challenge,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        return json.loads(options_to_json(options))

    def finish_enrollment(
        self, *, bootstrap_secret: str, challenge: str, credential: dict,
    ) -> str:
        self._enrollment_allowed(bootstrap_secret)
        self._pending(challenge, "enroll")
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=_unb64(challenge),
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError) as exc:
            raise PasskeyApprovalError("passkey enrollment verification failed") from exc
        identity = _b64(verified.credential_id)
        now = self._now_ms()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = db.execute(
                    """SELECT used_ms,expires_ms FROM memory_pm_passkey_challenges
                    WHERE challenge_hash=? AND kind='enroll'""",
                    (_hash(challenge),),
                ).fetchone()
                if (
                    current is None or current[0] is not None or current[1] <= now
                    or db.execute("SELECT 1 FROM memory_pm_owner_passkeys LIMIT 1").fetchone()
                ):
                    raise PasskeyApprovalError("enrollment consumed or owner already enrolled")
                db.execute(
                    """INSERT INTO memory_pm_owner_passkeys
                    (credential_id,public_key,sign_count,enrolled_ms)
                    VALUES (?,?,?,?)""",
                    (identity, verified.credential_public_key,
                     verified.sign_count, now),
                )
                db.execute(
                    "UPDATE memory_pm_passkey_challenges SET used_ms=? WHERE challenge_hash=?",
                    (now, _hash(challenge)),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return identity

    def begin_approval(
        self, *, project_id: str, queue_item_id: int,
        transaction_id: str, proposal_digest: str,
    ) -> dict:
        if (
            not project_id or type(queue_item_id) is not int or queue_item_id <= 0
            or not transaction_id or not proposal_digest
            or len(proposal_digest) != 64
        ):
            raise PasskeyApprovalError("invalid bound project approval identity")
        with self._connect() as db:
            rows = db.execute(
                "SELECT credential_id FROM memory_pm_owner_passkeys"
            ).fetchall()
        if len(rows) != 1:
            raise PasskeyApprovalError("no unique owner passkey is enrolled")
        challenge = self._new_challenge(
            "approve", project_id=project_id, queue_item_id=queue_item_id,
            transaction_id=transaction_id, proposal_digest=proposal_digest,
        )
        options = generate_authentication_options(
            rp_id=self._rp_id,
            challenge=challenge,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=_unb64(rows[0][0])),
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return json.loads(options_to_json(options))

    def finish_approval(
        self, *, challenge: str, credential: dict,
        project_id: str, queue_item_id: int,
        transaction_id: str, proposal_digest: str,
    ) -> VerifiedPasskeyGrant:
        pending = self._pending(challenge, "approve")
        if pending[:4] != (project_id, queue_item_id, transaction_id, proposal_digest):
            raise PasskeyApprovalError("passkey does not match exact project transaction")
        credential_id = credential.get("id") if isinstance(credential, dict) else None
        if not isinstance(credential_id, str):
            raise PasskeyApprovalError("missing credential identity")
        with self._connect() as db:
            row = db.execute(
                """SELECT public_key,sign_count FROM memory_pm_owner_passkeys
                   WHERE credential_id=?""",
                (credential_id,),
            ).fetchone()
        if row is None:
            raise PasskeyApprovalError("unregistered owner credential")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=_unb64(challenge),
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=row[0],
                credential_current_sign_count=row[1],
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError) as exc:
            raise PasskeyApprovalError("passkey assertion verification failed") from exc
        if _b64(verified.credential_id) != credential_id:
            raise PasskeyApprovalError("credential identity mismatch")
        token = _b64(secrets.token_bytes(32))
        assertion_digest = _hash(json.dumps(credential, sort_keys=True))
        now = self._now_ms()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                challenge_state = db.execute(
                    """SELECT used_ms,expires_ms FROM memory_pm_passkey_challenges
                    WHERE challenge_hash=? AND kind='approve'""",
                    (_hash(challenge),),
                ).fetchone()
                if (
                    challenge_state is None or challenge_state[0] is not None
                    or challenge_state[1] <= now
                ):
                    raise PasskeyApprovalError("approval challenge consumed or expired")
                changed = db.execute(
                    """UPDATE memory_pm_owner_passkeys SET sign_count=?
                    WHERE credential_id=? AND sign_count=?""",
                    (verified.new_sign_count, credential_id, row[1]),
                )
                if changed.rowcount != 1:
                    raise PasskeyApprovalError("owner credential counter changed")
                db.execute(
                    """INSERT INTO memory_pm_passkey_grants
                    (token_hash,credential_id,project_id,queue_item_id,
                     transaction_id,proposal_digest,assertion_digest,expires_ms)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (_hash(token), credential_id, project_id, queue_item_id,
                     transaction_id, proposal_digest, assertion_digest,
                     now + _GRANT_TTL_MS),
                )
                db.execute(
                    "UPDATE memory_pm_passkey_challenges SET used_ms=? WHERE challenge_hash=?",
                    (now, _hash(challenge)),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return VerifiedPasskeyGrant(
            token=token, credential_id=credential_id,
            assertion_digest=assertion_digest,
        )
