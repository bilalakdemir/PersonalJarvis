"""Durable approval/apply lifecycle for governed project-state memory proposals."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import aiosqlite

from jarvis.core.project_state_events import (
    ProjectStateMemoryApprovalAccepted,
    ProjectStateMemoryApprovalRejected,
    ProjectStateMemoryProposalCreated,
)
from jarvis.core.protocols import EventPublisher
from jarvis.projects.loader import load_project_context
from jarvis.projects.models import (
    ProjectFileChange,
    ProjectStateApproval,
    ProjectStateChangeProposal,
    ProjectStateTransactionResult,
    ProjectValidationResult,
)
from jarvis.projects.proposal import proposal_digest
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.state_store import ProjectStateStore

from .migration_runner import run_migrations
from .project_state_planner import ProjectStateMemoryPlanner
from .project_state_proposal import ProjectMemoryProposalComposer
from .promotion_queue import MemoryPromotionQueue
from .wiki.journal import CandidateJournal

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
log = logging.getLogger(__name__)

ProjectStateProposalStatus = Literal[
    "awaiting-approval",
    "approved",
    "rejected",
    "applied",
    "expired",
    "failed",
]


class ProjectStateProposalLifecycleError(RuntimeError):
    """The durable project-state proposal lifecycle cannot advance safely."""


class ProjectStateProposalIdentityError(ProjectStateProposalLifecycleError):
    """An approval/rejection did not bind to the exact stored proposal."""


class ProjectStateProposalExpiredError(ProjectStateProposalLifecycleError):
    """The exact stored proposal expired before a terminal decision."""


class ProjectStateProposalStaleError(ProjectStateProposalLifecycleError):
    """The canonical project state moved after the proposal was prepared."""


@dataclass(frozen=True, slots=True)
class StoredProjectStateProposal:
    id: int
    queue_item_id: int
    candidate_id: int
    project_id: str
    source_state_revision: str
    current_task: str | None
    transaction_id: str
    proposal_digest: str
    proposal: ProjectStateChangeProposal
    created_ms: int
    updated_ms: int
    expires_ms: int
    status: ProjectStateProposalStatus
    decision_ms: int | None
    applied_ms: int | None
    resulting_state_revision: str | None
    failure_reason: str | None


class ProjectStateProposalStore:
    """Durable exact proposal record plus project-state queue lifecycle."""

    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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

    async def expire_due(self, *, limit: int = 500) -> tuple[int, ...]:
        """Expire approval-pending project proposals and their queue items."""

        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await conn.execute(
                """
                SELECT queue_item_id
                FROM memory_project_state_proposals
                WHERE status IN ('awaiting-approval', 'approved')
                  AND expires_ms <= ?
                ORDER BY expires_ms, id
                LIMIT ?
                """,
                (now_ms, max(1, int(limit))),
            )
            rows = await cur.fetchall()
            await cur.close()
            ids = tuple(int(row["queue_item_id"]) for row in rows)
            for queue_item_id in ids:
                row = await _fetch_proposal_row(conn, queue_item_id)
                if row is None:
                    continue
                await _expire_locked(conn, _row_to_stored(row), now_ms)
            await conn.execute("COMMIT")
            return ids
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def persist(
        self,
        *,
        queue_item_id: int,
        proposal: ProjectStateChangeProposal,
    ) -> StoredProjectStateProposal:
        """Persist one exact proposal and atomically claim the queue item."""

        _validate_proposal(proposal)
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            queue_row = await _fetch_queue_row(conn, queue_item_id)
            if queue_row is None:
                raise ProjectStateProposalLifecycleError(
                    "promotion queue item not found"
                )
            if queue_row["authority"] != "project-state":
                raise ProjectStateProposalLifecycleError(
                    "promotion queue item is not project-state authority"
                )
            if queue_row["relation"] != "execution-status":
                raise ProjectStateProposalLifecycleError(
                    "unsupported project-memory relation"
                )
            if int(queue_row["expires_ms"]) <= now_ms:
                await _set_queue_status(
                    conn,
                    queue_item_id=queue_item_id,
                    expected=("pending", "proposal-created"),
                    target="expired",
                    now_ms=now_ms,
                )
                await conn.execute("COMMIT")
                raise ProjectStateProposalExpiredError(
                    "promotion queue item has expired"
                )
            if str(queue_row["project_id"] or "") != proposal.project_id:
                raise ProjectStateProposalIdentityError(
                    "proposal project_id does not match queue item"
                )
            if (
                str(queue_row["source_state_revision"] or "")
                != proposal.source_state_revision
            ):
                raise ProjectStateProposalIdentityError(
                    "proposal source revision does not match queue item"
                )
            queue_status = str(queue_row["status"])
            if queue_status not in {"pending", "proposal-created"}:
                raise ProjectStateProposalLifecycleError(
                    f"promotion queue item cannot create proposal from {queue_status}"
                )

            payload = _proposal_to_json(proposal)
            await conn.execute(
                """
                INSERT INTO memory_project_state_proposals (
                    queue_item_id,
                    candidate_id,
                    project_id,
                    source_state_revision,
                    current_task,
                    transaction_id,
                    proposal_digest,
                    proposal_json,
                    created_ms,
                    updated_ms,
                    expires_ms,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting-approval')
                ON CONFLICT(queue_item_id) DO NOTHING
                """,
                (
                    int(queue_item_id),
                    int(queue_row["candidate_id"]),
                    proposal.project_id,
                    proposal.source_state_revision,
                    (
                        str(queue_row["current_task"])
                        if queue_row["current_task"] is not None
                        else None
                    ),
                    proposal.transaction_id,
                    proposal.proposal_digest,
                    payload,
                    now_ms,
                    now_ms,
                    int(queue_row["expires_ms"]),
                ),
            )
            stored_row = await _fetch_proposal_row(conn, queue_item_id)
            if stored_row is None:
                raise ProjectStateProposalLifecycleError(
                    "proposal persistence did not produce a durable row"
                )
            stored = _row_to_stored(stored_row)
            if (
                stored.transaction_id != proposal.transaction_id
                or stored.proposal_digest != proposal.proposal_digest
                or _proposal_to_json(stored.proposal) != payload
            ):
                raise ProjectStateProposalIdentityError(
                    "queue item is already bound to a different exact proposal"
                )
            if queue_status == "pending":
                await _set_queue_status(
                    conn,
                    queue_item_id=queue_item_id,
                    expected=("pending",),
                    target="proposal-created",
                    now_ms=now_ms,
                )
            await conn.execute("COMMIT")
            return stored
        except ProjectStateProposalExpiredError:
            raise
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def get(
        self,
        queue_item_id: int,
    ) -> StoredProjectStateProposal | None:
        conn = await self._ensure_open()
        row = await _fetch_proposal_row(conn, queue_item_id)
        return _row_to_stored(row) if row is not None else None

    async def active(self, *, limit: int = 100) -> list[StoredProjectStateProposal]:
        """Return unexpired proposals awaiting a decision or an approved apply."""
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_project_state_proposals
            WHERE status IN ('awaiting-approval', 'approved')
              AND expires_ms > ?
            ORDER BY created_ms, id
            LIMIT ?
            """,
            (now_ms, max(1, int(limit))),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_stored(row) for row in rows]

    async def approve_identity(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest_value: str,
    ) -> StoredProjectStateProposal:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _fetch_proposal_row(conn, queue_item_id)
            if row is None:
                raise ProjectStateProposalLifecycleError(
                    "stored project-state proposal not found"
                )
            stored = _row_to_stored(row)
            _require_identity(stored, transaction_id, proposal_digest_value)
            if stored.expires_ms <= now_ms and stored.status not in {
                "applied",
                "rejected",
                "failed",
            }:
                await _expire_locked(conn, stored, now_ms)
                await conn.execute("COMMIT")
                raise ProjectStateProposalExpiredError(
                    "project-state proposal has expired"
                )
            if stored.status in {"rejected", "failed", "expired"}:
                raise ProjectStateProposalLifecycleError(
                    f"proposal is terminal: {stored.status}"
                )
            if stored.status == "awaiting-approval":
                await conn.execute(
                    """
                    UPDATE memory_project_state_proposals
                    SET status = 'approved', decision_ms = ?, updated_ms = ?
                    WHERE queue_item_id = ? AND status = 'awaiting-approval'
                    """,
                    (now_ms, now_ms, int(queue_item_id)),
                )
            row = await _fetch_proposal_row(conn, queue_item_id)
            assert row is not None
            await conn.execute("COMMIT")
            return _row_to_stored(row)
        except ProjectStateProposalExpiredError:
            raise
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def recover_pending(self, *, limit: int = 100) -> tuple[int, int]:
        """Restore durable approval visibility and resume approved applies.

        Returns a pair of replayed and resumed counts. Awaiting proposals are
        republished as metadata-only cards. Already-approved proposals reuse
        their exact durable identity and continue through approve_and_apply.
        """
        replayed = 0
        resumed = 0
        for stored in await self._proposals.active(limit=limit):
            if stored.status == "awaiting-approval":
                await self._publish(
                    ProjectStateMemoryProposalCreated(
                        source_layer="memory",
                        project_id=stored.project_id,
                        queue_item_id=stored.queue_item_id,
                        candidate_id=stored.candidate_id,
                        transaction_id=stored.transaction_id,
                        proposal_digest=stored.proposal_digest,
                        source_state_revision=stored.source_state_revision,
                        files_affected=stored.proposal.files_affected,
                    )
                )
                replayed += 1
                continue
            if stored.status == "approved":
                try:
                    await self.approve_and_apply(
                        queue_item_id=stored.queue_item_id,
                        transaction_id=stored.transaction_id,
                        proposal_digest=stored.proposal_digest,
                    )
                    resumed += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "project-state approved proposal %d recovery failed: %s",
                        stored.queue_item_id,
                        exc,
                    )
        return replayed, resumed

    async def reject(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest_value: str,
    ) -> StoredProjectStateProposal:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _fetch_proposal_row(conn, queue_item_id)
            if row is None:
                raise ProjectStateProposalLifecycleError(
                    "stored project-state proposal not found"
                )
            stored = _row_to_stored(row)
            _require_identity(stored, transaction_id, proposal_digest_value)
            if stored.expires_ms <= now_ms and stored.status not in {
                "applied",
                "rejected",
                "failed",
            }:
                await _expire_locked(conn, stored, now_ms)
                await conn.execute("COMMIT")
                raise ProjectStateProposalExpiredError(
                    "project-state proposal has expired"
                )
            if stored.status == "rejected":
                await conn.execute("COMMIT")
                return stored
            if stored.status != "awaiting-approval":
                raise ProjectStateProposalLifecycleError(
                    f"proposal cannot be rejected from {stored.status}"
                )
            await conn.execute(
                """
                UPDATE memory_project_state_proposals
                SET status = 'rejected', decision_ms = ?, updated_ms = ?
                WHERE queue_item_id = ? AND status = 'awaiting-approval'
                """,
                (now_ms, now_ms, int(queue_item_id)),
            )
            await _set_queue_status(
                conn,
                queue_item_id=queue_item_id,
                expected=("proposal-created",),
                target="rejected",
                now_ms=now_ms,
            )
            row = await _fetch_proposal_row(conn, queue_item_id)
            assert row is not None
            await conn.execute("COMMIT")
            return _row_to_stored(row)
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def mark_applied(
        self,
        *,
        queue_item_id: int,
        resulting_state_revision: str,
    ) -> StoredProjectStateProposal:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _fetch_proposal_row(conn, queue_item_id)
            if row is None:
                raise ProjectStateProposalLifecycleError(
                    "stored project-state proposal not found"
                )
            stored = _row_to_stored(row)
            if stored.status == "applied":
                await conn.execute("COMMIT")
                return stored
            if stored.status != "approved":
                raise ProjectStateProposalLifecycleError(
                    f"proposal cannot be applied from {stored.status}"
                )
            await conn.execute(
                """
                UPDATE memory_project_state_proposals
                SET status = 'applied', applied_ms = ?, updated_ms = ?,
                    resulting_state_revision = ?, failure_reason = NULL
                WHERE queue_item_id = ? AND status = 'approved'
                """,
                (
                    now_ms,
                    now_ms,
                    str(resulting_state_revision or ""),
                    int(queue_item_id),
                ),
            )
            await _set_queue_status(
                conn,
                queue_item_id=queue_item_id,
                expected=("proposal-created",),
                target="approved",
                now_ms=now_ms,
            )
            row = await _fetch_proposal_row(conn, queue_item_id)
            assert row is not None
            await conn.execute("COMMIT")
            return _row_to_stored(row)
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def mark_failed(
        self,
        *,
        queue_item_id: int,
        reason: str,
    ) -> StoredProjectStateProposal:
        conn = await self._ensure_open()
        now_ms = int(self._clock() * 1000)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _fetch_proposal_row(conn, queue_item_id)
            if row is None:
                raise ProjectStateProposalLifecycleError(
                    "stored project-state proposal not found"
                )
            stored = _row_to_stored(row)
            if stored.status in {"applied", "rejected", "failed", "expired"}:
                await conn.execute("COMMIT")
                return stored
            await conn.execute(
                """
                UPDATE memory_project_state_proposals
                SET status = 'failed', updated_ms = ?, failure_reason = ?
                WHERE queue_item_id = ?
                """,
                (now_ms, _safe_failure(reason), int(queue_item_id)),
            )
            await _set_queue_status(
                conn,
                queue_item_id=queue_item_id,
                expected=("proposal-created",),
                target="failed",
                now_ms=now_ms,
            )
            row = await _fetch_proposal_row(conn, queue_item_id)
            assert row is not None
            await conn.execute("COMMIT")
            return _row_to_stored(row)
        except Exception:
            await conn.execute("ROLLBACK")
            raise


class ProjectStateMemoryApprovalLifecycle:
    """Full governed candidate -> proposal -> explicit decision -> apply path."""

    def __init__(
        self,
        *,
        queue: MemoryPromotionQueue,
        journal: CandidateJournal,
        registry: ProjectRegistry,
        db_path: str | Path,
        planner: ProjectStateMemoryPlanner,
        bus: EventPublisher | None = None,
        clock=time.time,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._planner = planner
        self._composer = ProjectMemoryProposalComposer(
            queue=queue,
            journal=journal,
            registry=registry,
        )
        self._proposals = ProjectStateProposalStore(db_path, clock=clock)
        self._project_store = ProjectStateStore(registry, bus=bus)
        self._bus = bus

    async def close(self) -> None:
        await self._proposals.close()

    async def _publish(self, event) -> None:
        if self._bus is not None:
            await self._bus.publish(event)

    async def prepare(
        self,
        queue_item_id: int,
    ) -> StoredProjectStateProposal:
        """Generate and persist one exact proposal; never mutate project files."""

        existing = await self._proposals.get(queue_item_id)
        if existing is not None:
            return existing
        plan = await self._planner.plan(queue_item_id)
        proposal = await self._composer.compose(queue_item_id, plan)
        stored = await self._proposals.persist(
            queue_item_id=queue_item_id,
            proposal=proposal,
        )
        await self._publish(
            ProjectStateMemoryProposalCreated(
                source_layer="memory",
                project_id=stored.project_id,
                queue_item_id=stored.queue_item_id,
                candidate_id=stored.candidate_id,
                transaction_id=stored.transaction_id,
                proposal_digest=stored.proposal_digest,
                source_state_revision=stored.source_state_revision,
                files_affected=stored.proposal.files_affected,
            )
        )
        return stored

    async def reject(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest: str,
    ) -> StoredProjectStateProposal:
        """Reject exactly one stored proposal; generic tool approvals are unrelated."""

        stored = await self._proposals.reject(
            queue_item_id=queue_item_id,
            transaction_id=transaction_id,
            proposal_digest_value=proposal_digest,
        )
        await self._publish(
            ProjectStateMemoryApprovalRejected(
                source_layer="memory",
                project_id=stored.project_id,
                queue_item_id=stored.queue_item_id,
                transaction_id=stored.transaction_id,
                proposal_digest=stored.proposal_digest,
            )
        )
        return stored

    async def approve_and_apply(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest: str,
    ) -> ProjectStateTransactionResult:
        """Apply only the exact explicitly approved durable proposal."""

        stored = await self._proposals.approve_identity(
            queue_item_id=queue_item_id,
            transaction_id=transaction_id,
            proposal_digest_value=proposal_digest,
        )
        await self._publish(
            ProjectStateMemoryApprovalAccepted(
                source_layer="memory",
                project_id=stored.project_id,
                queue_item_id=stored.queue_item_id,
                transaction_id=stored.transaction_id,
                proposal_digest=stored.proposal_digest,
            )
        )

        recovered = await asyncio.to_thread(
            _recover_committed_transaction,
            self._registry,
            stored,
        )
        if recovered is not None:
            await self._proposals.mark_applied(
                queue_item_id=queue_item_id,
                resulting_state_revision=recovered.resulting_state_revision or "",
            )
            return recovered

        try:
            await self._revalidate(stored)
            result = await self._project_store.apply_approved(
                stored.proposal,
                ProjectStateApproval(
                    transaction_id=stored.transaction_id,
                    proposal_digest=stored.proposal_digest,
                ),
            )
        except Exception as exc:
            await self._proposals.mark_failed(
                queue_item_id=queue_item_id,
                reason=str(exc),
            )
            raise

        if result.status != "COMMITTED" or not result.resulting_state_revision:
            await self._proposals.mark_failed(
                queue_item_id=queue_item_id,
                reason=result.error
                or f"project-state transaction ended {result.status}",
            )
            raise ProjectStateProposalLifecycleError(
                f"project-state transaction did not commit: {result.status}"
            )

        await self._proposals.mark_applied(
            queue_item_id=queue_item_id,
            resulting_state_revision=result.resulting_state_revision,
        )
        return result

    async def _revalidate(
        self,
        stored: StoredProjectStateProposal,
    ) -> None:
        entry = self._registry.resolve_exact(stored.project_id)
        if (
            entry is None
            or entry.project_id.casefold() != stored.project_id.casefold()
        ):
            raise ProjectStateProposalStaleError(
                "stored proposal project is no longer registered canonically"
            )
        loaded = await asyncio.to_thread(load_project_context, entry)
        if not loaded.validation.valid or loaded.snapshot is None:
            raise ProjectStateProposalStaleError(
                "canonical project state is unavailable or invalid"
            )
        snapshot = loaded.snapshot
        if snapshot.state_revision != stored.source_state_revision:
            raise ProjectStateProposalStaleError(
                "canonical project revision changed after proposal creation"
            )
        if snapshot.current_task != stored.current_task:
            raise ProjectStateProposalStaleError(
                "canonical CURRENT task changed after proposal creation"
            )


async def _fetch_queue_row(
    conn: aiosqlite.Connection,
    queue_item_id: int,
):
    cur = await conn.execute(
        "SELECT * FROM memory_promotion_queue WHERE id = ? LIMIT 1",
        (int(queue_item_id),),
    )
    row = await cur.fetchone()
    await cur.close()
    return row


async def _fetch_proposal_row(
    conn: aiosqlite.Connection,
    queue_item_id: int,
):
    cur = await conn.execute(
        """
        SELECT *
        FROM memory_project_state_proposals
        WHERE queue_item_id = ?
        LIMIT 1
        """,
        (int(queue_item_id),),
    )
    row = await cur.fetchone()
    await cur.close()
    return row


async def _set_queue_status(
    conn: aiosqlite.Connection,
    *,
    queue_item_id: int,
    expected: tuple[str, ...],
    target: str,
    now_ms: int,
) -> None:
    placeholders = ",".join("?" for _ in expected)
    cur = await conn.execute(
        f"""
        UPDATE memory_promotion_queue
        SET status = ?, updated_ms = ?
        WHERE id = ? AND status IN ({placeholders})
        """,
        (target, int(now_ms), int(queue_item_id), *expected),
    )
    if cur.rowcount != 1:
        current = await _fetch_queue_row(conn, queue_item_id)
        if current is not None and str(current["status"]) == target:
            return
        raise ProjectStateProposalLifecycleError(
            "promotion queue lifecycle changed concurrently"
        )


async def _expire_locked(
    conn: aiosqlite.Connection,
    stored: StoredProjectStateProposal,
    now_ms: int,
) -> None:
    await conn.execute(
        """
        UPDATE memory_project_state_proposals
        SET status = 'expired', updated_ms = ?
        WHERE queue_item_id = ?
          AND status IN ('awaiting-approval', 'approved')
        """,
        (now_ms, stored.queue_item_id),
    )
    await _set_queue_status(
        conn,
        queue_item_id=stored.queue_item_id,
        expected=("proposal-created",),
        target="expired",
        now_ms=now_ms,
    )


def _require_identity(
    stored: StoredProjectStateProposal,
    transaction_id: str,
    digest: str,
) -> None:
    if transaction_id != stored.transaction_id:
        raise ProjectStateProposalIdentityError(
            "approval transaction_id does not match stored proposal"
        )
    if digest != stored.proposal_digest:
        raise ProjectStateProposalIdentityError(
            "approval digest does not match stored proposal"
        )


def _proposal_to_json(proposal: ProjectStateChangeProposal) -> str:
    payload = {
        "transaction_id": proposal.transaction_id,
        "project_id": proposal.project_id,
        "source_state_revision": proposal.source_state_revision,
        "changes": [
            {
                "filename": change.filename,
                "previous_sha256": change.previous_sha256,
                "proposed_sha256": change.proposed_sha256,
                "proposed_content": change.proposed_content,
                "unified_diff": change.unified_diff,
            }
            for change in proposal.changes
        ],
        "reason": proposal.reason,
        "expected_current_task": proposal.expected_current_task,
        "expected_effect": proposal.expected_effect,
        "proposal_digest": proposal.proposal_digest,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _proposal_from_json(value: str) -> ProjectStateChangeProposal:
    try:
        payload = json.loads(value)
        changes = tuple(
            ProjectFileChange(
                filename=str(row["filename"]),
                previous_sha256=(
                    str(row["previous_sha256"])
                    if row["previous_sha256"] is not None
                    else None
                ),
                proposed_sha256=str(row["proposed_sha256"]),
                proposed_content=str(row["proposed_content"]),
                unified_diff=str(row["unified_diff"]),
            )
            for row in payload["changes"]
        )
        proposal = ProjectStateChangeProposal(
            transaction_id=str(payload["transaction_id"]),
            project_id=str(payload["project_id"]),
            source_state_revision=str(payload["source_state_revision"]),
            changes=changes,
            reason=str(payload["reason"]),
            expected_current_task=(
                str(payload["expected_current_task"])
                if payload["expected_current_task"] is not None
                else None
            ),
            expected_effect=str(payload["expected_effect"]),
            proposal_digest=str(payload["proposal_digest"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectStateProposalLifecycleError(
            "stored project-state proposal payload is malformed"
        ) from exc
    _validate_proposal(proposal)
    return proposal


def _validate_proposal(proposal: ProjectStateChangeProposal) -> None:
    try:
        UUID(proposal.transaction_id)
    except (ValueError, AttributeError) as exc:
        raise ProjectStateProposalIdentityError(
            "project-state proposal transaction_id must be a UUID"
        ) from exc
    unsigned = ProjectStateChangeProposal(
        transaction_id=proposal.transaction_id,
        project_id=proposal.project_id,
        source_state_revision=proposal.source_state_revision,
        changes=proposal.changes,
        reason=proposal.reason,
        expected_current_task=proposal.expected_current_task,
        expected_effect=proposal.expected_effect,
        proposal_digest="",
    )
    if proposal_digest(unsigned) != proposal.proposal_digest:
        raise ProjectStateProposalIdentityError(
            "project-state proposal digest does not match exact payload"
        )


def _row_to_stored(row: aiosqlite.Row) -> StoredProjectStateProposal:
    proposal = _proposal_from_json(str(row["proposal_json"]))
    if proposal.transaction_id != str(row["transaction_id"]):
        raise ProjectStateProposalIdentityError(
            "stored transaction_id does not match proposal payload"
        )
    if proposal.proposal_digest != str(row["proposal_digest"]):
        raise ProjectStateProposalIdentityError(
            "stored proposal_digest does not match proposal payload"
        )
    if proposal.project_id != str(row["project_id"]):
        raise ProjectStateProposalIdentityError(
            "stored project_id does not match proposal payload"
        )
    if proposal.source_state_revision != str(row["source_state_revision"]):
        raise ProjectStateProposalIdentityError(
            "stored source revision does not match proposal payload"
        )
    return StoredProjectStateProposal(
        id=int(row["id"]),
        queue_item_id=int(row["queue_item_id"]),
        candidate_id=int(row["candidate_id"]),
        project_id=str(row["project_id"]),
        source_state_revision=str(row["source_state_revision"]),
        current_task=(
            str(row["current_task"])
            if row["current_task"] is not None
            else None
        ),
        transaction_id=str(row["transaction_id"]),
        proposal_digest=str(row["proposal_digest"]),
        proposal=proposal,
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
        resulting_state_revision=(
            str(row["resulting_state_revision"])
            if row["resulting_state_revision"] is not None
            else None
        ),
        failure_reason=(
            str(row["failure_reason"])
            if row["failure_reason"] is not None
            else None
        ),
    )


def _safe_failure(reason: str) -> str:
    text = str(reason or "").strip()
    return text[:2000] if text else "project-state proposal failed"


def _recover_committed_transaction(
    registry: ProjectRegistry,
    stored: StoredProjectStateProposal,
) -> ProjectStateTransactionResult | None:
    entry = registry.resolve_exact(stored.project_id)
    if (
        entry is None
        or entry.project_id.casefold() != stored.project_id.casefold()
    ):
        return None
    root = entry.root_path.resolve(strict=False)
    engine_dir = root / ".jarvis"
    backup_root = engine_dir / "state-backups"
    transaction_dir = backup_root / stored.transaction_id
    manifest_path = transaction_dir / "manifest.json"
    if (
        engine_dir.is_symlink()
        or backup_root.is_symlink()
        or transaction_dir.is_symlink()
        or manifest_path.is_symlink()
    ):
        return None
    if (
        transaction_dir.resolve(strict=False).parent
        != backup_root.resolve(strict=False)
    ):
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        log.debug(
            "Project-state committed-manifest recovery unavailable: %s",
            exc,
        )
        return None
    if (
        payload.get("status") != "COMMITTED"
        or payload.get("transaction_id") != stored.transaction_id
        or payload.get("project_id") != stored.project_id
        or payload.get("proposal_digest") != stored.proposal_digest
    ):
        return None
    resulting = str(payload.get("resulting_state_revision") or "")
    if not resulting:
        return None
    return ProjectStateTransactionResult(
        transaction_id=stored.transaction_id,
        project_id=stored.project_id,
        status="COMMITTED",
        source_state_revision=stored.source_state_revision,
        resulting_state_revision=resulting,
        changed_files=stored.proposal.files_affected,
        backup_dir=transaction_dir,
        validation=ProjectValidationResult(),
    )


__all__ = [
    "ProjectStateMemoryApprovalLifecycle",
    "ProjectStateProposalExpiredError",
    "ProjectStateProposalIdentityError",
    "ProjectStateProposalLifecycleError",
    "ProjectStateProposalStaleError",
    "ProjectStateProposalStatus",
    "ProjectStateProposalStore",
    "StoredProjectStateProposal",
]
