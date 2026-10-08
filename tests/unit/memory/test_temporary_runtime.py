from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceTurnCompleted
from jarvis.core.memory_events import TemporaryMemoryStored
from jarvis.memory.temporary_runtime import (
    TemporaryConversationCapture, capture_chat_completion, set_running_capture,
)


@pytest.mark.asyncio
async def test_capture_voice_and_event(tmp_path):
    bus = EventBus()
    capture = TemporaryConversationCapture(tmp_path / "jarvis.db", bus=bus)
    observed = []
    async def on_stored(event):
        observed.append(event)
    bus.subscribe(TemporaryMemoryStored, on_stored)
    capture.start_voice()
    try:
        await bus.publish(VoiceTurnCompleted(
            session_id="s", turn_id="t", user_text="My project is starting",
            jarvis_text="Understood",
        ))
        for _ in range(100):
            if observed:
                break
            await __import__("asyncio").sleep(.01)
        assert observed
        item = await capture.store.get(observed[0].item_id)
        assert item is not None
        assert item.source == "voice"
        assert json.loads(item.content)["user"] == "My project is starting"
        assert item.promotion_state == "unreviewed"
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_direct_chat_only(tmp_path):
    capture = TemporaryConversationCapture(tmp_path / "jarvis.db")
    set_running_capture(capture)
    session = SimpleNamespace(session_id="chat1")
    def completion(direct):
        return SimpleNamespace(
            turn=SimpleNamespace(turn_id="t1", direct_user=direct, user_text="A preference"),
            events_json=json.dumps([
                {"kind": "user_message", "payload": {"text": "I prefer quiet"}},
                {"kind": "assistant_text", "payload": {"text": "Noted"}},
            ]),
        )
    try:
        await capture_chat_completion(session, completion(False))
        assert await capture.store.due_for_review(within_hours=24*30) == []
        await capture_chat_completion(session, completion(True))
        items = await capture.store.due_for_review(within_hours=24*30)
        assert len(items) == 1
        assert json.loads(items[0].content)["user"] == "I prefer quiet"
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_secret_not_written(tmp_path):
    capture = TemporaryConversationCapture(tmp_path / "jarvis.db")
    try:
        outcome = await capture.capture(
            user="sk-proj-" + "x"*35,
            source="chat", session_id="a", turn_id="b",
        )
        assert outcome is None
    finally:
        await capture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capture_status", "expected"),
    [
        ("empty", "temporary"), ("filtered", "temporary"),
        ("candidates", "candidate"), ("failed", None), ("started", None),
    ],
)
async def test_expiry_review_respects_capture_audit_and_scopes_stage2(
    tmp_path, monkeypatch, capture_status, expected,
):
    from jarvis.memory.temporary_runtime import review_expiring
    from jarvis.memory.wiki import integration

    capture = TemporaryConversationCapture(tmp_path / "jarvis.db")
    item_id = await capture.capture(
        user="My AERION project has a milestone",
        source="chat", session_id="session1", turn_id="turn1",
    )
    assert item_id is not None
    item = await capture.store.get(item_id)
    assert item is not None
    calls = []

    class Extractor:
        async def extract_and_journal(self, *args, **kwargs):
            calls.append(("extract", kwargs["review_key"]))
            return int(capture_status == "candidates")

    class Journal:
        def capture_status(self, key):
            calls.append(("audit", key))
            return capture_status

    class Scheduler:
        async def trigger(self, source, **kwargs):
            calls.append(("trigger", kwargs["review_keys"]))

    monkeypatch.setattr(
        integration, "get_running_capture_runtime",
        lambda: SimpleNamespace(
            extractor=Extractor(), journal=Journal(), scheduler=Scheduler(),
        ),
    )
    try:
        assert await review_expiring(item) == expected
        assert calls[:2] == [
            ("extract", f"temporary:v1:{item_id}"),
            ("audit", f"temporary:v1:{item_id}"),
        ]
        assert ("trigger", (f"temporary:v1:{item_id}",)) in calls if expected == "candidate" else not any(
            call[0] == "trigger" for call in calls
        )
    finally:
        await capture.close()
