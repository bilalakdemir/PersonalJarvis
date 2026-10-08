"""Bounded temporary conversation capture and governed expiry review."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from jarvis.core.memory_events import TemporaryMemoryStored
from jarvis.memory.temporary import TemporaryMemoryItem, TemporaryMemoryStore

log = logging.getLogger(__name__)
RETENTION_DAYS = 30
MAX_USER_CHARS = 12000
MAX_ASSISTANT_CHARS = 3000


class TemporaryConversationCapture:
    def __init__(self, db_path: str | Path, *, bus: Any = None) -> None:
        self.store = TemporaryMemoryStore(db_path)
        self.bus = bus
        self._voice_handler = None
        self._voice_tasks: set[asyncio.Task[None]] = set()

    def start_voice(self) -> None:
        if self.bus is None or self._voice_handler is not None:
            return
        from jarvis.core.events import VoiceTurnCompleted

        async def save(event: Any) -> None:
            try:
                await self.capture(
                    user=str(getattr(event, "user_text", "") or ""),
                    assistant=str(getattr(event, "jarvis_text", "") or ""),
                    source="voice",
                    session_id=str(getattr(event, "session_id", "") or ""),
                    turn_id=str(getattr(event, "turn_id", "") or ""),
                )
            except Exception:
                log.warning("temporary voice capture failed", exc_info=True)

        async def handle(event: Any) -> None:
            # Never put SQLite I/O on the voice event's critical path.
            task = asyncio.create_task(save(event), name="memory-temporary-voice")
            self._voice_tasks.add(task)
            task.add_done_callback(self._voice_tasks.discard)

        self._voice_handler = handle
        self.bus.subscribe(VoiceTurnCompleted, handle)

    async def close(self) -> None:
        if self.bus is not None and self._voice_handler is not None:
            from jarvis.core.events import VoiceTurnCompleted
            self.bus.unsubscribe(VoiceTurnCompleted, self._voice_handler)
            self._voice_handler = None
        if self._voice_tasks:
            tasks = tuple(self._voice_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._voice_tasks.clear()
        if running_capture() is self:
            set_running_capture(None)
        await self.store.close()

    async def capture(
        self, *, user: str, assistant: str = "", source: str,
        session_id: str, turn_id: str,
    ) -> int | None:
        user = (user or "").strip()
        if not user or not session_id or not turn_id:
            return None
        content = json.dumps({
            "v": 1, "user": user[:MAX_USER_CHARS],
            "assistant_context": (assistant or "")[:MAX_ASSISTANT_CHARS],
            "session_id": session_id, "turn_id": turn_id,
        }, ensure_ascii=False)
        try:
            item_id = await self.store.put(
                content=content, source=source, kind="conversation_turn",
                retention_days=RETENTION_DAYS,
                evidence_refs=(f"session:{session_id}", f"turn:{turn_id}"),
            )
            if self.bus is not None:
                item = await self.store.get(item_id)
                if item is not None:
                    await self.bus.publish(TemporaryMemoryStored(
                        source_layer="memory", item_id=item_id,
                        kind=item.kind, project_id=item.project_id,
                        expires_ms=item.expires_ms,
                    ))
            return item_id
        except ValueError:
            log.info("temporary capture rejected unsafe content")
            return None


_capture: TemporaryConversationCapture | None = None


def set_running_capture(value: TemporaryConversationCapture | None) -> None:
    global _capture
    _capture = value


def running_capture() -> TemporaryConversationCapture | None:
    return _capture


async def capture_chat_completion(session: Any, completion: Any) -> None:
    capture = running_capture()
    if capture is None:
        return
    turn = getattr(completion, "turn", None)
    if turn is None or not getattr(turn, "direct_user", True):
        return
    from jarvis.society.memory_intent import user_evidence
    try:
        events = json.loads(completion.events_json)
        typed = [user_evidence(e) for e in events if e.get("kind") == "user_message"]
        user = (typed[-1] if typed else getattr(turn, "user_text", "")).strip()
        answer = "\\n".join(
            str((e.get("payload") or {}).get("text") or "")
            for e in events if e.get("kind") == "assistant_text"
        )
        await capture.capture(
            user=user, assistant=answer, source="chat",
            session_id=str(getattr(session, "session_id", "")),
            turn_id=str(getattr(turn, "turn_id", "")),
        )
    except Exception:
        log.warning("temporary chat capture failed", exc_info=True)


async def review_expiring(item: TemporaryMemoryItem) -> str | None:
    """Return terminal state only when extraction audit confirms completion."""
    if item.kind != "conversation_turn":
        return "temporary"
    try:
        envelope = json.loads(item.content)
        if envelope.get("v") != 1:
            return "temporary"
        user = str(envelope["user"])
        assistant = str(envelope.get("assistant_context") or "")
        session_id = str(envelope["session_id"])
        turn_id = str(envelope["turn_id"])
    except (ValueError, TypeError, KeyError):
        log.warning("temporary expiry review cannot decode item %s", item.id, exc_info=True)
        return "temporary"

    from jarvis.memory.wiki.integration import get_running_capture_runtime
    runtime = get_running_capture_runtime()
    if runtime is None:
        return None
    review_key = f"temporary:v1:{item.id}"
    turn_hash = hashlib.sha256(
        (session_id + "\\0" + turn_id + "\\0" + user).encode("utf-8")
    ).hexdigest()
    await runtime.extractor.extract_and_journal(
        user, assistant, source_label=f"temporary-expiry:{item.id}",
        turn_hash=turn_hash, review_key=review_key,
        session_id=session_id, turn_id=turn_id,
        source_kind="temporary-expiry",
    )
    status = await __import__("asyncio").to_thread(runtime.journal.capture_status, review_key)
    if status == "candidates":
        if runtime.scheduler is not None:
            from jarvis.memory.wiki.scheduler import TriggerSource
            try:
                await runtime.scheduler.trigger(
                    TriggerSource.JOURNAL, review_keys=(review_key,)
                )
            except Exception:
                log.warning("temporary scoped journal trigger failed", exc_info=True)
        return "candidate"
    if status in {"empty", "filtered"}:
        return "temporary"
    return None
