"""Durable approval/project-route queue for governed memory promotion."""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import aiosqlite

from jarvis.core.project_state_events import ProjectStateMemoryProposalRequested

from .migration_runner import run_migrations
from .wiki.secret_guard import contains_secret

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

PromotionAuthority = Literal["persistent-memory", "project-state"]
PromotionQueueStatus = Literal[
    "pending",
    "proposal-created",
    "approved",
    "rejected",
    "expired",
    "failed",
]

DEFAULT_PROMOTION_RETENTION_DAYS = 30
_MS_PER_DAY = 86_400_000
_REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MemoryPromotionQueueItem:
    id: int
    created_ms: int
    updated_ms: int
    expires_ms: int
    candidate_id: int
    authority: PromotionAuthority
    project_id: str | None
    source_state_revision: str | None
    current_task: str | None
    relation: str
    status: PromotionQueueStatus


class MemoryPromotionQueue:
    """Durable, idempotent staging for governed promotion work."""

    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._conn is not None:
            return

        conn = await aiosqlite.connect(
            self._db_path,
            isolation_level=None,
        )
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

    async def enqueue_project_state(
        self,
        *,
        candidate_id: int,
        project_id: str,
        source_state_revision: str,
        current_task: str | None,
        relation: str,
        retention_days: int = DEFAULT_PROMOTION_RETENTION_DAYS,
    ) -> MemoryPromotionQueueItem:
        candidate = int(candidate_id)
        if candidate <= 0:
            raise ValueError("candidate_id must be positive")

        safe_project = _safe_text(project_id, "project_id", 240)
        safe_relation = _safe_text(relation, "relation", 80)

        revision = str(source_state_revision or "").strip().lower()
        if _REVISION_RE.fullmatch(revision) is None:
            raise ValueError(
                "source_state_revision must be a SHA-256 revision"
            )

        safe_task = _optional_safe_text(
            current_task,
            "current_task",
            1000,
        )

        days = int(retention_days)
        if days <= 0:
            raise ValueError(
                "retention_days must be greater than zero"
            )

        dedupe_key = hashlib.sha256(
            (
                f"project-state\0{candidate}\0{safe_project}\0"
                f"{revision}\0{safe_relation}"
            ).encode("utf-8")
        ).hexdigest()

        now_ms = int(self._clock() * 1000)
        expires_ms = now_ms + days * _MS_PER_DAY
        conn = await self._ensure_open()

        await conn.execute(
            """
            INSERT INTO memory_promotion_queue (
                dedupe_key,
                created_ms,
                updated_ms,
                expires_ms,
                candidate_id,
                authority,
                project_id,
                source_state_revision,
                current_task,
                relation,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'project-state', ?, ?, ?, ?, 'pending')
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            (
                dedupe_key,
                now_ms,
                now_ms,
                expires_ms,
                candidate,
                safe_project,
                revision,
                safe_task,
                safe_relation,
            ),
        )

        cur = await conn.execute(
            """
            SELECT *
            FROM memory_promotion_queue
            WHERE dedupe_key = ?
            """,
            (dedupe_key,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is None:
            raise RuntimeError(
                "promotion queue enqueue did not produce a row"
            )
        return _row_to_item(row)

    async def get(
        self,
        queue_item_id: int,
    ) -> MemoryPromotionQueueItem | None:
        """Return one exact durable promotion item by ID."""

        wanted = int(queue_item_id)
        if wanted <= 0:
            return None
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_promotion_queue
            WHERE id = ?
            LIMIT 1
            """,
            (wanted,),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_item(row) if row is not None else None

    async def pending(
        self,
        *,
        limit: int = 100,
    ) -> list[MemoryPromotionQueueItem]:
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            SELECT *
            FROM memory_promotion_queue
            WHERE status = 'pending'
              AND expires_ms > ?
            ORDER BY created_ms ASC, id ASC
            LIMIT ?
            """,
            (
                int(self._clock() * 1000),
                max(1, int(limit)),
            ),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_item(row) for row in rows]


async def attach_project_state_route_queue(
    *,
    bus: EventBus,
    db_path: str | Path,
    retention_days: int = DEFAULT_PROMOTION_RETENTION_DAYS,
) -> tuple[
    MemoryPromotionQueue,
    Callable[[], Awaitable[None]],
]:
    """Persist project-route events before later proposal construction."""

    queue = MemoryPromotionQueue(db_path)
    await queue.open()
    closed = False

    async def _on_route(
        event: ProjectStateMemoryProposalRequested,
    ) -> None:
        await queue.enqueue_project_state(
            candidate_id=event.candidate_id,
            project_id=event.project_id,
            source_state_revision=event.source_state_revision,
            current_task=event.current_task,
            relation=event.relation,
            retention_days=retention_days,
        )

    bus.subscribe(
        ProjectStateMemoryProposalRequested,
        _on_route,
    )

    async def _cleanup() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        bus.unsubscribe(
            ProjectStateMemoryProposalRequested,
            _on_route,
        )
        await queue.close()

    return queue, _cleanup


def _safe_text(
    value: str,
    field: str,
    max_chars: int,
) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > max_chars:
        raise ValueError(
            f"{field} exceeds {max_chars} characters"
        )
    if contains_secret(text):
        raise ValueError(
            f"{field} contains secret-shaped data"
        )
    return text


def _optional_safe_text(
    value: str | None,
    field: str,
    max_chars: int,
) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _safe_text(text, field, max_chars)


def _row_to_item(
    row: aiosqlite.Row,
) -> MemoryPromotionQueueItem:
    return MemoryPromotionQueueItem(
        id=int(row["id"]),
        created_ms=int(row["created_ms"]),
        updated_ms=int(row["updated_ms"]),
        expires_ms=int(row["expires_ms"]),
        candidate_id=int(row["candidate_id"]),
        authority=str(row["authority"]),  # type: ignore[arg-type]
        project_id=(
            str(row["project_id"])
            if row["project_id"] is not None
            else None
        ),
        source_state_revision=(
            str(row["source_state_revision"])
            if row["source_state_revision"] is not None
            else None
        ),
        current_task=(
            str(row["current_task"])
            if row["current_task"] is not None
            else None
        ),
        relation=str(row["relation"]),
        status=str(row["status"]),  # type: ignore[arg-type]
    )


__all__ = [
    "DEFAULT_PROMOTION_RETENTION_DAYS",
    "MemoryPromotionQueue",
    "MemoryPromotionQueueItem",
    "attach_project_state_route_queue",
]
