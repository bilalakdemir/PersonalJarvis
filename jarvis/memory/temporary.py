"""Retention-bounded general temporary memory on Jarvis' existing SQLite DB."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence, cast

import aiosqlite

from .migration_runner import run_migrations
from .wiki.secret_guard import contains_secret

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

PromotionState = Literal[
    "unreviewed",
    "temporary",
    "candidate",
    "approval_pending",
    "promoted",
    "rejected",
]
PROMOTION_STATES: tuple[str, ...] = (
    "unreviewed",
    "temporary",
    "candidate",
    "approval_pending",
    "promoted",
    "rejected",
)

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000
_MAX_CONTENT_CHARS = 50_000
_MAX_SOURCE_CHARS = 160
_MAX_KIND_CHARS = 80
_MAX_PROJECT_ID_CHARS = 240
_MAX_EVIDENCE_REFS = 50
_MAX_EVIDENCE_REF_CHARS = 512


@dataclass(frozen=True, slots=True)
class TemporaryMemoryItem:
    id: int
    created_ms: int
    updated_ms: int
    expires_ms: int
    source: str
    kind: str
    content: str
    project_id: str | None
    promotion_state: PromotionState
    evidence_refs: tuple[str, ...]


class TemporaryMemoryStore:
    """Disposable non-conversation context; never a Wiki/project-state writer."""

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

    async def __aenter__(self) -> TemporaryMemoryStore:
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.open()
        assert self._conn is not None
        return self._conn

    async def put(
        self,
        *,
        content: str,
        source: str,
        kind: str,
        retention_days: int,
        project_id: str | None = None,
        evidence_refs: Sequence[str] = (),
        promotion_state: PromotionState = "unreviewed",
    ) -> int:
        safe_content = _bounded(content, "content", _MAX_CONTENT_CHARS)
        if contains_secret(safe_content):
            raise ValueError("temporary memory content contains secret-shaped data")

        safe_source = _bounded(source, "source", _MAX_SOURCE_CHARS)
        safe_kind = _bounded(kind, "kind", _MAX_KIND_CHARS)
        safe_project_id = _optional_bounded(
            project_id, "project_id", _MAX_PROJECT_ID_CHARS
        )
        safe_evidence = _normalise_evidence_refs(evidence_refs)
        safe_state = _promotion_state(promotion_state)

        days = int(retention_days)
        if days <= 0:
            raise ValueError("retention_days must be greater than zero")

        now_ms = int(self._clock() * 1000)
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            INSERT INTO temporary_memory (
                created_ms, updated_ms, expires_ms, source, kind, content,
                project_id, promotion_state, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_ms,
                now_ms,
                now_ms + days * _MS_PER_DAY,
                safe_source,
                safe_kind,
                safe_content,
                safe_project_id,
                safe_state,
                json.dumps(safe_evidence, ensure_ascii=False),
            ),
        )
        rowid = int(cur.lastrowid or 0)
        await cur.close()
        return rowid

    async def get(self, item_id: int) -> TemporaryMemoryItem | None:
        conn = await self._ensure_open()
        cur = await conn.execute(
            "SELECT * FROM temporary_memory WHERE id = ?",
            (int(item_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_item(row) if row is not None else None

    async def due_for_review(
        self,
        *,
        within_hours: int,
        limit: int = 100,
    ) -> list[TemporaryMemoryItem]:
        hours = int(within_hours)
        if hours < 0:
            raise ValueError("within_hours must not be negative")
        now_ms = int(self._clock() * 1000)
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            SELECT * FROM temporary_memory
            WHERE promotion_state = 'unreviewed'
              AND expires_ms > ?
              AND expires_ms <= ?
            ORDER BY expires_ms ASC, id ASC
            LIMIT ?
            """,
            (now_ms, now_ms + hours * _MS_PER_HOUR, max(1, int(limit))),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_item(row) for row in rows]

    async def set_promotion_state(
        self,
        item_id: int,
        state: PromotionState,
    ) -> bool:
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            UPDATE temporary_memory
            SET promotion_state = ?, updated_ms = ?
            WHERE id = ?
            """,
            (_promotion_state(state), int(self._clock() * 1000), int(item_id)),
        )
        changed = cur.rowcount == 1
        await cur.close()
        return changed

    async def delete_expired(self) -> int:
        conn = await self._ensure_open()
        cur = await conn.execute(
            "DELETE FROM temporary_memory WHERE expires_ms <= ?",
            (int(self._clock() * 1000),),
        )
        removed = max(0, int(cur.rowcount))
        await cur.close()
        return removed

    async def delete(self, item_id: int) -> bool:
        conn = await self._ensure_open()
        cur = await conn.execute(
            "DELETE FROM temporary_memory WHERE id = ?",
            (int(item_id),),
        )
        changed = cur.rowcount == 1
        await cur.close()
        return changed


def _row_to_item(row: aiosqlite.Row) -> TemporaryMemoryItem:
    try:
        raw_refs = json.loads(str(row["evidence_json"] or "[]"))
    except (json.JSONDecodeError, TypeError, ValueError):
        log.warning(
            "TemporaryMemoryStore: invalid evidence_json for item %s; "
            "using empty evidence refs",
            row["id"],
        )
        raw_refs = []
    return TemporaryMemoryItem(
        id=int(row["id"]),
        created_ms=int(row["created_ms"]),
        updated_ms=int(row["updated_ms"]),
        expires_ms=int(row["expires_ms"]),
        source=str(row["source"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        project_id=(str(row["project_id"]) if row["project_id"] is not None else None),
        promotion_state=_promotion_state(str(row["promotion_state"])),
        evidence_refs=tuple(x for x in raw_refs if isinstance(x, str)),
    )


def _promotion_state(value: str) -> PromotionState:
    text = str(value or "").strip().lower()
    if text not in PROMOTION_STATES:
        raise ValueError(f"unknown promotion_state: {value!r}")
    return cast(PromotionState, text)


def _bounded(value: str, field: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    return text


def _optional_bounded(
    value: str | None,
    field: str,
    max_chars: int,
) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    if contains_secret(text):
        raise ValueError(f"{field} contains secret-shaped data")
    return text


def _normalise_evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    refs: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if len(text) > _MAX_EVIDENCE_REF_CHARS:
            raise ValueError(
                f"evidence reference exceeds {_MAX_EVIDENCE_REF_CHARS} characters"
            )
        if contains_secret(text):
            raise ValueError("evidence reference contains secret-shaped data")
        if text not in refs:
            refs.append(text)
        if len(refs) >= _MAX_EVIDENCE_REFS:
            break
    return tuple(refs)


__all__ = [
    "PROMOTION_STATES",
    "PromotionState",
    "TemporaryMemoryItem",
    "TemporaryMemoryStore",
]
