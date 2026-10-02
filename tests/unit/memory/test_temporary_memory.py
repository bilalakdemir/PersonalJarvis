from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.temporary import TemporaryMemoryStore

_DAY_S = 86_400


@pytest.mark.asyncio
async def test_temporary_memory_roundtrip_preserves_scope_and_evidence(
    tmp_path: Path,
) -> None:
    now = [1_700_000_000.0]
    db_path = tmp_path / "jarvis.db"

    async with TemporaryMemoryStore(db_path, clock=lambda: now[0]) as store:
        item_id = await store.put(
            content="Compare three approaches before choosing one.",
            source="research",
            kind="working_note",
            retention_days=30,
            project_id="personal-jarvis",
            evidence_refs=("conversation:42", "source:design-note"),
        )
        item = await store.get(item_id)

    assert item is not None
    assert item.content == "Compare three approaches before choosing one."
    assert item.source == "research"
    assert item.kind == "working_note"
    assert item.project_id == "personal-jarvis"
    assert item.promotion_state == "unreviewed"
    assert item.evidence_refs == ("conversation:42", "source:design-note")
    assert item.expires_ms - item.created_ms == 30 * _DAY_S * 1000


@pytest.mark.asyncio
async def test_due_for_review_only_returns_unreviewed_items_in_window(
    tmp_path: Path,
) -> None:
    now = [1_700_000_000.0]
    async with TemporaryMemoryStore(
        tmp_path / "jarvis.db",
        clock=lambda: now[0],
    ) as store:
        due_id = await store.put(
            content="Potentially durable preference.",
            source="conversation",
            kind="observation",
            retention_days=1,
        )
        reviewed_id = await store.put(
            content="Already classified.",
            source="conversation",
            kind="observation",
            retention_days=1,
        )
        await store.set_promotion_state(reviewed_id, "temporary")

        due = await store.due_for_review(within_hours=24)

    assert [item.id for item in due] == [due_id]


@pytest.mark.asyncio
async def test_delete_expired_removes_only_expired_rows(tmp_path: Path) -> None:
    now = [1_700_000_000.0]
    async with TemporaryMemoryStore(
        tmp_path / "jarvis.db",
        clock=lambda: now[0],
    ) as store:
        expired_id = await store.put(
            content="Short-lived note.",
            source="research",
            kind="note",
            retention_days=1,
        )
        live_id = await store.put(
            content="Longer-lived note.",
            source="research",
            kind="note",
            retention_days=30,
        )

        now[0] += 2 * _DAY_S
        assert await store.delete_expired() == 1
        assert await store.get(expired_id) is None
        assert await store.get(live_id) is not None


@pytest.mark.asyncio
async def test_secret_shaped_content_is_rejected_before_write(tmp_path: Path) -> None:
    store = TemporaryMemoryStore(tmp_path / "jarvis.db")

    with pytest.raises(ValueError, match="secret-shaped"):
        await store.put(
            content="credential sk-proj-" + ("a" * 30),
            source="conversation",
            kind="note",
            retention_days=30,
        )

    await store.close()


@pytest.mark.asyncio
async def test_secret_shaped_metadata_is_rejected_before_write(tmp_path: Path) -> None:
    store = TemporaryMemoryStore(tmp_path / "jarvis.db")

    with pytest.raises(ValueError, match="source contains secret-shaped"):
        await store.put(
            content="Safe temporary note.",
            source="sk-proj-" + ("b" * 30),
            kind="note",
            retention_days=30,
        )

    await store.close()


@pytest.mark.asyncio
async def test_invalid_evidence_json_shape_degrades_to_empty_refs(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"

    async with TemporaryMemoryStore(db_path) as store:
        item_id = await store.put(
            content="Safe temporary note.",
            source="research",
            kind="note",
            retention_days=30,
            evidence_refs=("source:one",),
        )
        conn = await store._ensure_open()  # noqa: SLF001 - corruption probe
        await conn.execute(
            "UPDATE temporary_memory SET evidence_json = ? WHERE id = ?",
            ('{"unexpected":"object"}', item_id),
        )

        item = await store.get(item_id)

    assert item is not None
    assert item.evidence_refs == ()


@pytest.mark.asyncio
async def test_reopen_is_idempotent_and_keeps_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"

    async with TemporaryMemoryStore(db_path) as store:
        item_id = await store.put(
            content="Persist across process restart until retention expiry.",
            source="research",
            kind="note",
            retention_days=30,
        )

    async with TemporaryMemoryStore(db_path) as reopened:
        assert await reopened.get(item_id) is not None

    conn = sqlite3.connect(db_path)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'temporary_memory'"
        ).fetchone()
    finally:
        conn.close()

    assert version >= 10
    assert table == ("temporary_memory",)
