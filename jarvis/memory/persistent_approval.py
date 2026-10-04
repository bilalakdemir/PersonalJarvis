"""Durable approval queue for governed persistent-memory candidates."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import aiosqlite

from jarvis.core.memory_events import (
    MemoryPromotionApproved,
    MemoryPromotionProposed,
    MemoryPromotionRejected,
)
from jarvis.core.protocols import EventPublisher

from .migration_runner import run_migrations

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
DEFAULT_APPROVAL_RETENTION_DAYS = 30
_MS_PER_DAY = 86_400_000

PersistentApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "applied",
    "expired",
    "failed",
]


class PersistentMemoryApprovalError(RuntimeError):
    """A persistent-memory approval cannot advance safely."""


class PersistentMemoryApprovalIdentityError(PersistentMemoryApprovalError):
    """The decision does not bind to the exact proposed memory item."""


@dataclass(frozen=True, slots=True)
class PersistentMemoryApproval:
    id: int
    candidate_id: int
    content_sha256: str
    governance_class: str
    proposal_digest: str
    created_ms: int
    updated_ms: int
    expires_ms: int
    status: PersistentApprovalStatus
    decision_ms: int | None
    applied_ms: int | None
    failure_reason: str | None


class PersistentMemoryApprovalQueue:
    """Stores exact candidate identities until approve/reject/expiry."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        bus: EventPublisher | None = None,
        clock=time.time,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._bus = bus
        self._clock = clock
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._conn is not None:
            return
        conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        await run_migrations(conn)
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.open()
        assert self._conn is not None
        return self._conn

    async def _publish(self, event) -> None:
        if self._bus is not None:
            await self._bus.publish(event)

    async def propose(
        self,
        *,
        candidate_id: int,
        content: str,
        governance_class: str,
        retention_days: int = DEFAULT_APPROVAL_RETENTION_DAYS,
    ) -> PersistentMemoryApproval:
        candidate = int(candidate_id)
        if candidate <= 0:
            raise ValueError("candidate_id must be positive")
        body = str(content or "").strip()
        if not body:
            raise ValueError("persistent-memory proposal content must not be empty")
        klass = str(governance_class or "").strip().lower()
        if not klass:
            raise ValueError("governance_class must not be empty")
        days = int(retention_days)
        if days <= 0:
            raise ValueError("retention_days must be greater than zero")

        content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        digest = _proposal_digest(candidate, content_sha, klass)
        now_ms = int(self._clock() * 1000)
        expires_ms = now_ms + days * _MS_PER_DAY
        conn = await self._ensure_open()

        cur = await conn.execute(
            """
            INSERT INTO memory_persistent_approvals (
                candidate_id, content_sha256, governance_class,
                proposal_digest, created_ms, updated_ms, expires_ms, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(candidate_id) DO NOTHING
            """,
            (
                candidate,
                content_sha,
                klass,
                digest,
                now_ms,
                now_ms,
                expires_ms,
            ),
        )
        inserted = cur.rowcount == 1
        await cur.close()
        item = await self.get(candidate)
        if item is None:
            raise PersistentMemoryApprovalError(
                "persistent-memory proposal was not durably stored"
            )
        if (
            item.content_sha256 != content_sha
            or item.governance_class != klass
            or item.proposal_digest != digest
        ):
            raise PersistentMemoryApprovalIdentityError(
                "candidate is already bound to different persistent-memory content"
            )
        if inserted and item.status == "pending":
            await self._publish(
                MemoryPromotionProposed(
                    source_layer="memory",
                    candidate_id=item.candidate_id,
                    proposal_digest=item.proposal_digest,
                    governance_class=item.governance_class,
                    expires_ms=item.expires_ms,
                )
            )
        return item

    async def get(self, candidate_id: int) -> PersistentMemoryApproval | None:
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_persistent_approvals
            WHERE candidate_id = ?
            LIMIT 1
            """,
            (int(candidate_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_item(row) if row is not None else None

    async def pending(self, *, limit: int = 100) -> list[PersistentMemoryApproval]:
        """Return unexpired approvals still waiting for an exact user decision."""
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_persistent_approvals
            WHERE status = 'pending' AND expires_ms > ?
            ORDER BY created_ms, id
            LIMIT ?
            """,
            (now_ms, max(1, int(limit))),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_item(row) for row in rows]

    async def replay_pending(self, *, limit: int = 100) -> int:
        """Re-emit metadata-only approval cards after a process restart."""
        items = await self.pending(limit=limit)
        for item in items:
            await self._publish(
                MemoryPromotionProposed(
                    source_layer="memory",
                    candidate_id=item.candidate_id,
                    proposal_digest=item.proposal_digest,
                    governance_class=item.governance_class,
                    expires_ms=item.expires_ms,
                )
            )
        return len(items)

    async def approve(
        self,
        *,
        candidate_id: int,
        proposal_digest: str,
    ) -> PersistentMemoryApproval:
        item = await self._decide(
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
            target="approved",
        )
        await self._publish(
            MemoryPromotionApproved(
                source_layer="memory",
                candidate_id=item.candidate_id,
                proposal_digest=item.proposal_digest,
            )
        )
        return item

    async def reject(
        self,
        *,
        candidate_id: int,
        proposal_digest: str,
        reason: str = "user-rejected",
    ) -> PersistentMemoryApproval:
        item = await self._decide(
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
            target="rejected",
            failure_reason=reason,
        )
        await self._publish(
            MemoryPromotionRejected(
                source_layer="memory",
                candidate_id=item.candidate_id,
                proposal_digest=item.proposal_digest,
                reason=str(reason or "user-rejected")[:240],
            )
        )
        return item

    async def _decide(
        self,
        *,
        candidate_id: int,
        proposal_digest: str,
        target: Literal["approved", "rejected"],
        failure_reason: str | None = None,
    ) -> PersistentMemoryApproval:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            item = await self._get_locked(conn, int(candidate_id))
            if item is None:
                raise PersistentMemoryApprovalError(
                    "persistent-memory proposal not found"
                )
            _require_digest(item, proposal_digest)
            if item.expires_ms <= now_ms and item.status == "pending":
                await conn.execute(
                    """
                    UPDATE memory_persistent_approvals
                    SET status = 'expired', updated_ms = ?
                    WHERE candidate_id = ? AND status = 'pending'
                    """,
                    (now_ms, item.candidate_id),
                )
                await conn.execute("COMMIT")
                raise PersistentMemoryApprovalError(
                    "persistent-memory proposal has expired"
                )
            if item.status == target:
                await conn.execute("COMMIT")
                return item
            if item.status != "pending":
                raise PersistentMemoryApprovalError(
                    f"persistent-memory proposal is terminal: {item.status}"
                )
            await conn.execute(
                """
                UPDATE memory_persistent_approvals
                SET status = ?, decision_ms = ?, updated_ms = ?,
                    failure_reason = ?
                WHERE candidate_id = ? AND status = 'pending'
                """,
                (
                    target,
                    now_ms,
                    now_ms,
                    (
                        str(failure_reason or "")[:1000]
                        if failure_reason is not None
                        else None
                    ),
                    item.candidate_id,
                ),
            )
            result = await self._get_locked(conn, item.candidate_id)
            assert result is not None
            await conn.execute("COMMIT")
            return result
        except PersistentMemoryApprovalError:
            if conn.in_transaction:
                await conn.execute("ROLLBACK")
            raise
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def mark_applied(
        self,
        candidate_id: int,
    ) -> PersistentMemoryApproval | None:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute(
            """
            UPDATE memory_persistent_approvals
            SET status = 'applied', applied_ms = ?, updated_ms = ?,
                failure_reason = NULL
            WHERE candidate_id = ? AND status = 'approved'
            """,
            (now_ms, now_ms, int(candidate_id)),
        )
        return await self.get(candidate_id)

    async def mark_failed(
        self,
        candidate_id: int,
        reason: str,
    ) -> PersistentMemoryApproval | None:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute(
            """
            UPDATE memory_persistent_approvals
            SET status = 'failed', updated_ms = ?, failure_reason = ?
            WHERE candidate_id = ? AND status = 'approved'
            """,
            (now_ms, str(reason or "persistent write failed")[:1000], int(candidate_id)),
        )
        return await self.get(candidate_id)

    async def expire_due(self, *, limit: int = 500) -> tuple[int, ...]:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        cur = await conn.execute(
            """
            SELECT candidate_id
            FROM memory_persistent_approvals
            WHERE status = 'pending' AND expires_ms <= ?
            ORDER BY expires_ms, id
            LIMIT ?
            """,
            (now_ms, max(1, int(limit))),
        )
        rows = await cur.fetchall()
        await cur.close()
        ids = tuple(int(row["candidate_id"]) for row in rows)
        if ids:
            placeholders = ",".join("?" for _ in ids)
            await conn.execute(
                "UPDATE memory_persistent_approvals "
                "SET status = 'expired', updated_ms = ? "
                f"WHERE candidate_id IN ({placeholders}) AND status = 'pending'",  # noqa: S608
                (now_ms, *ids),
            )
            for candidate_id in ids:
                item = await self.get(candidate_id)
                if item is not None:
                    await self._publish(
                        MemoryPromotionRejected(
                            source_layer="memory",
                            candidate_id=item.candidate_id,
                            proposal_digest=item.proposal_digest,
                            reason="retention-expired",
                        )
                    )
        return ids

    async def _get_locked(
        self,
        conn: aiosqlite.Connection,
        candidate_id: int,
    ) -> PersistentMemoryApproval | None:
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_persistent_approvals
            WHERE candidate_id = ?
            LIMIT 1
            """,
            (candidate_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_item(row) if row is not None else None


def _proposal_digest(
    candidate_id: int,
    content_sha256: str,
    governance_class: str,
) -> str:
    payload = json.dumps(
        {
            "candidate_id": int(candidate_id),
            "content_sha256": content_sha256,
            "governance_class": governance_class,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_digest(
    item: PersistentMemoryApproval,
    digest: str,
) -> None:
    if str(digest or "") != item.proposal_digest:
        raise PersistentMemoryApprovalIdentityError(
            "approval digest does not match exact persistent-memory proposal"
        )


def _row_to_item(row: aiosqlite.Row) -> PersistentMemoryApproval:
    return PersistentMemoryApproval(
        id=int(row["id"]),
        candidate_id=int(row["candidate_id"]),
        content_sha256=str(row["content_sha256"]),
        governance_class=str(row["governance_class"]),
        proposal_digest=str(row["proposal_digest"]),
        created_ms=int(row["created_ms"]),
        updated_ms=int(row["updated_ms"]),
        expires_ms=int(row["expires_ms"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        decision_ms=(
            int(row["decision_ms"])
            if row["decision_ms"] is not None
            else None
        ),
        applied_ms=(
            int(row["applied_ms"])
            if row["applied_ms"] is not None
            else None
        ),
        failure_reason=(
            str(row["failure_reason"])
            if row["failure_reason"] is not None
            else None
        ),
    )


__all__ = [
    "DEFAULT_APPROVAL_RETENTION_DAYS",
    "PersistentApprovalStatus",
    "PersistentMemoryApproval",
    "PersistentMemoryApprovalError",
    "PersistentMemoryApprovalIdentityError",
    "PersistentMemoryApprovalQueue",
]
