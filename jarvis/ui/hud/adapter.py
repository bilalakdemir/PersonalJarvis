"""``HudStateAdapter`` — the one owner of HUD semantic state per EventBus.

Wiring, not policy: the adapter subscribes a single wildcard observer to the
EXISTING :class:`~jarvis.core.bus.EventBus` (no second event system), feeds a
:class:`~jarvis.ui.hud.reducer.HudReducer`, and hands snapshots to
presentation listeners (the orb projection, the web transport). It publishes
nothing onto the bus and owns no operational state of its own.

Reconnect/resync: :meth:`snapshot` always returns the full current state, so a
reloaded window, a reconnected socket or a resumed laptop recovers reality
with one read — no event replay.

Failure isolation: a listener that raises is logged and skipped; the reducer
itself is wrapped so a malformed event degrades to "no change" instead of an
exception inside ``EventBus._safe_dispatch`` (AP-18).

One adapter per bus: :func:`hud_adapter_for` returns the shared instance so
the desktop overlay and the web server project the SAME semantic state rather
than two drifting copies.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any, Final
from uuid import uuid4

from jarvis.core.events import Event

from .models import HudConnectionState, HudSnapshot
from .reducer import HudReducer

log = logging.getLogger(__name__)

SnapshotListener = Callable[[HudSnapshot], None]

#: Listener notifications are coalesced to at most one per this interval: a
#: Computer-Use heartbeat or worker progress can arrive many times a second,
#: and the HUD renders semantic state, not frames.
DEFAULT_NOTIFY_INTERVAL_S: Final[float] = 0.1


class HudStateAdapter:
    """Projects EventBus (+ optional MissionBus) events into ``HudSnapshot``."""

    def __init__(
        self,
        bus: Any | None = None,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        notify_interval_s: float = DEFAULT_NOTIFY_INTERVAL_S,
    ) -> None:
        # Weak: the bus already holds the adapter (through its subscription),
        # and ``hud_adapter_for`` keys a WeakKeyDictionary by the bus — a
        # strong back-reference would keep every bus (and adapter) alive.
        self._bus_ref: weakref.ReferenceType[Any] | None = (
            weakref.ref(bus) if bus is not None else None
        )
        self._clock_ns = clock_ns
        self._notify_interval_s = max(0.0, float(notify_interval_s))
        self._reducer = HudReducer()
        # Reducer state is written on the event loop (bus observer) and read
        # from wherever a snapshot is requested — the REST route runs in
        # FastAPI's threadpool so the loop never serves it. One plain lock
        # around every reducer access keeps those two sides consistent; it is
        # never held while a listener runs.
        self._lock = threading.Lock()
        self._epoch = uuid4().hex
        self._revision = 0
        self._started = False
        self._listeners: list[SnapshotListener] = []
        self._mission_unsubscribers: dict[int, Callable[[], None]] = {}
        self._notify_handle: asyncio.TimerHandle | asyncio.Handle | None = None
        self._deadline_handle: asyncio.TimerHandle | None = None
        self._last_signature: tuple[Any, ...] | None = None
        # What listeners were last told — kept apart from ``_last_signature``
        # so a snapshot READ (the REST route, a surface attaching) can never
        # swallow the notification a change still owes the listeners.
        self._notified_signature: tuple[Any, ...] | None = None

    # ------------------------------------------------------------ lifecycle
    def _bus(self) -> Any | None:
        return self._bus_ref() if self._bus_ref is not None else None

    @property
    def epoch(self) -> str:
        return self._epoch

    @property
    def revision(self) -> int:
        return self._revision

    def start(self) -> None:
        """Subscribe to the bus. Idempotent; cheap (no I/O, AP-26)."""
        bus = self._bus()
        if self._started or bus is None:
            return
        bus.subscribe_all(self.handle)
        self._started = True

    def close(self) -> None:
        bus = self._bus()
        if self._started and bus is not None:
            unsubscribe_all = getattr(bus, "unsubscribe_all", None)
            if callable(unsubscribe_all):
                unsubscribe_all(self.handle)
        self._started = False
        for unsubscribe in list(self._mission_unsubscribers.values()):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 — teardown must finish
                log.debug("HUD mission-bus unsubscribe failed", exc_info=True)
        self._mission_unsubscribers.clear()
        for handle in (self._notify_handle, self._deadline_handle):
            if handle is not None:
                handle.cancel()
        self._notify_handle = None
        self._deadline_handle = None

    def attach_mission_bus(self, mission_bus: Any) -> None:
        """Also project the per-mission MissionBus (missions/workers).

        The MissionBus is existing infrastructure whose envelopes never reach
        the global bus except as ``MissionCompleted``; without this tap the
        HUD could not show a running worker. Read-only, idempotent per bus.
        """
        if mission_bus is None or id(mission_bus) in self._mission_unsubscribers:
            return
        subscribe_all = getattr(mission_bus, "subscribe_all", None)
        if not callable(subscribe_all):
            return
        unsubscribe = subscribe_all(self.handle_mission_envelope)
        self._mission_unsubscribers[id(mission_bus)] = (
            unsubscribe if callable(unsubscribe) else (lambda: None)
        )

    # -------------------------------------------------------------- input
    async def handle(self, event: Event) -> None:
        """EventBus wildcard observer. Never raises (AP-18)."""
        self.ingest(event)

    async def handle_mission_envelope(self, envelope: Any) -> None:
        try:
            with self._lock:
                changed = self._reducer.apply_mission_envelope(envelope)
        except Exception:  # noqa: BLE001 — a projection bug must not break missions
            log.warning("HUD failed to reduce a mission envelope", exc_info=True)
            return
        if changed:
            self._changed()

    def ingest(self, event: Event) -> bool:
        """Synchronously reduce one event (used by tests and the bus observer)."""
        try:
            with self._lock:
                changed = self._reducer.apply(event)
        except Exception:  # noqa: BLE001 — a projection bug must not break the bus
            log.warning("HUD failed to reduce %s", type(event).__name__, exc_info=True)
            return False
        if changed:
            self._changed()
        return changed

    def set_connection_state(self, state: HudConnectionState) -> HudSnapshot:
        with self._lock:
            changed = self._reducer.set_connection_state(state)
        if changed:
            self._changed()
        return self.snapshot()

    # ------------------------------------------------------------- output
    def snapshot(self) -> HudSnapshot:
        """The full current snapshot (startup / reconnect / reload / resume).

        Thread-safe: callable from the event loop and from a worker thread.
        """
        with self._lock:
            snap = self._reducer.snapshot(
                now_ns=self._clock_ns(), epoch=self._epoch, revision=self._revision
            )
            signature = _signature(snap)
            if signature != self._last_signature:
                # Time-based expiry (an approval timing out) changes the
                # snapshot without an event; give it its own revision so
                # clients that order by revision still accept it.
                if self._last_signature is not None:
                    self._revision += 1
                    snap = _with_revision(snap, self._revision)
                self._last_signature = signature
            return snap

    def add_listener(self, listener: SnapshotListener) -> Callable[[], None]:
        """Register a presentation callback; returns an unsubscribe callable."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    # ----------------------------------------------------------- internals
    def _changed(self) -> None:
        if not self._listeners:
            self._schedule_deadline()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (sync tests, Tk-only harness): notify inline.
            self._notify()
            return
        if self._notify_handle is not None:
            return  # coalesced into the pending notification
        if self._notify_interval_s <= 0:
            self._notify_handle = loop.call_soon(self._notify)
        else:
            self._notify_handle = loop.call_later(self._notify_interval_s, self._notify)

    def _notify(self) -> None:
        self._notify_handle = None
        snap = self.snapshot()
        signature = _signature(snap)
        if signature == self._notified_signature:
            self._schedule_deadline()
            return  # nothing a viewer could see changed since the last push
        self._notified_signature = signature
        for listener in list(self._listeners):
            try:
                listener(snap)
            except Exception:  # noqa: BLE001 — a broken surface must not stop the others
                log.warning("HUD listener %r failed", listener, exc_info=True)
        self._schedule_deadline()

    def _schedule_deadline(self) -> None:
        """Wake once at the next approval expiry so it disappears on time."""
        if self._deadline_handle is not None:
            self._deadline_handle.cancel()
            self._deadline_handle = None
        with self._lock:
            deadline = self._reducer.next_deadline_ns()
        if deadline is None or not self._listeners:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (sync caller): expiry applies on the next read
            return
        delay_s = max(0.0, (deadline - self._clock_ns()) / 1_000_000_000) + 0.05
        self._deadline_handle = loop.call_later(delay_s, self._notify)


def _signature(snap: HudSnapshot) -> tuple[Any, ...]:
    """Everything a viewer can see, minus the bookkeeping fields."""
    return (
        snap.primary_state,
        snap.connection_state,
        snap.voice_state,
        snap.attention,
        snap.active_project,
        snap.active_operations,
        snap.approval_requests,
        snap.agent_activity,
        snap.memory_activity,
        snap.computer_activity,
        snap.last_error,
    )


def _with_revision(snap: HudSnapshot, revision: int) -> HudSnapshot:
    from dataclasses import replace

    return replace(snap, revision=revision)


# --- one adapter per bus ---------------------------------------------------------
_ADAPTERS: weakref.WeakKeyDictionary[Any, HudStateAdapter] = weakref.WeakKeyDictionary()


def hud_adapter_for(bus: Any) -> HudStateAdapter:
    """The shared, started adapter for ``bus`` (created on first use).

    Desktop overlay and web server both call this with the process bus, so
    there is exactly one semantic HUD authority per bus.
    """
    adapter = _ADAPTERS.get(bus)
    if adapter is None:
        adapter = HudStateAdapter(bus)
        _ADAPTERS[bus] = adapter
    adapter.start()
    return adapter


def reset_hud_adapters() -> None:
    """For tests only."""
    for adapter in list(_ADAPTERS.values()):
        adapter.close()
    _ADAPTERS.clear()


__all__ = [
    "DEFAULT_NOTIFY_INTERVAL_S",
    "HudStateAdapter",
    "SnapshotListener",
    "hud_adapter_for",
    "reset_hud_adapters",
]
