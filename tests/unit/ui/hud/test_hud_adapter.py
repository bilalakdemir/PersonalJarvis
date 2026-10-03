"""HudStateAdapter wiring: real EventBus, snapshot resync, listener isolation."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionApprovalRequired,
    ActionProposed,
    SystemStateChanged,
    TaskStarted,
)
from jarvis.ui.hud.adapter import HudStateAdapter, hud_adapter_for, reset_hud_adapters


class _Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000_000_000_000

    def __call__(self) -> int:
        return self.now


def test_adapter_projects_real_bus_events_and_never_publishes() -> None:
    async def scenario() -> None:
        bus = EventBus()
        published: list[str] = []

        async def spy(event) -> None:  # every event on the bus, HUD's included
            published.append(type(event).__name__)

        bus.subscribe_all(spy)
        adapter = HudStateAdapter(bus, notify_interval_s=0)
        adapter.start()
        await bus.publish(SystemStateChanged(new_state="LISTENING"))
        await bus.publish(TaskStarted(task_id="t1"))
        snap = adapter.snapshot()
        assert snap.primary_state == "LISTENING"
        assert [op.task_id for op in snap.active_operations] == ["t1"]
        # The HUD is a projection: it added nothing to the bus.
        assert published == ["SystemStateChanged", "TaskStarted"]
        adapter.close()

    asyncio.run(scenario())


def test_reconnect_snapshot_is_full_state_without_replay() -> None:
    clock = _Clock()
    adapter = HudStateAdapter(None, clock_ns=clock)
    trace = uuid4()
    adapter.ingest(ActionProposed(trace_id=trace, tool_name="deploy", timestamp_ns=clock.now))
    adapter.ingest(
        ActionApprovalRequired(
            trace_id=trace, tool_name="deploy", mission_id="m1", timestamp_ns=clock.now
        )
    )
    first = adapter.snapshot()
    # A client that reconnects later just reads the snapshot again.
    again = adapter.snapshot()
    assert again.primary_state == "WAITING_FOR_APPROVAL"
    assert again.revision == first.revision, "no visible change → same revision"
    assert again.epoch == first.epoch and again.epoch
    assert again.approval_requests[0].approval_id == f"tool_call:m1:{trace}:deploy"


def test_revision_increases_on_visible_change_including_expiry() -> None:
    clock = _Clock()
    adapter = HudStateAdapter(None, clock_ns=clock)
    base = adapter.snapshot().revision
    adapter.ingest(
        ActionApprovalRequired(
            tool_name="x",
            mission_id="m",
            expires_at_ns=clock.now + 5_000_000_000,
            timestamp_ns=clock.now,
        )
    )
    after_request = adapter.snapshot()
    assert after_request.revision > base
    clock.now += 6_000_000_000
    expired = adapter.snapshot()
    assert expired.approval_requests == ()
    assert expired.revision > after_request.revision


def test_listener_failure_is_isolated_and_coalesced() -> None:
    async def scenario() -> None:
        bus = EventBus()
        adapter = HudStateAdapter(bus, notify_interval_s=0.02)
        adapter.start()
        seen: list[str] = []

        def broken(_snap) -> None:
            raise RuntimeError("surface crashed")

        adapter.add_listener(broken)
        adapter.add_listener(lambda snap: seen.append(snap.primary_state))
        await bus.publish(SystemStateChanged(new_state="LISTENING"))
        await bus.publish(SystemStateChanged(new_state="THINKING", previous="LISTENING"))
        await bus.publish(SystemStateChanged(new_state="SPEAKING", previous="THINKING"))
        await asyncio.sleep(0.08)
        # Three events, one coalesced notification carrying the latest state.
        assert seen == ["SPEAKING"]
        # Backend state is intact despite the broken surface.
        assert adapter.snapshot().primary_state == "SPEAKING"
        adapter.close()

    asyncio.run(scenario())


def test_malformed_event_does_not_raise_into_the_bus() -> None:
    adapter = HudStateAdapter(None)
    bogus = TaskStarted(task_id="t", timestamp_ns="not-a-number")  # type: ignore[arg-type]
    adapter.ingest(bogus)  # must not raise
    assert adapter.snapshot().primary_state in {"IDLE", "WORKING"}


def test_one_adapter_per_bus() -> None:
    reset_hud_adapters()
    try:
        bus_a, bus_b = EventBus(), EventBus()
        assert hud_adapter_for(bus_a) is hud_adapter_for(bus_a)
        assert hud_adapter_for(bus_a) is not hud_adapter_for(bus_b)
        assert len(bus_a._wildcard_subscribers) == 1  # noqa: SLF001 — started exactly once
    finally:
        reset_hud_adapters()


def test_mission_bus_tap_is_read_only_and_idempotent() -> None:
    class _MissionBus:
        def __init__(self) -> None:
            self.handlers = []

        def subscribe_all(self, handler):
            self.handlers.append(handler)
            return lambda: self.handlers.remove(handler)

    async def scenario() -> None:
        from types import SimpleNamespace

        mbus = _MissionBus()
        adapter = HudStateAdapter(None)
        adapter.attach_mission_bus(mbus)
        adapter.attach_mission_bus(mbus)
        assert len(mbus.handlers) == 1
        env = SimpleNamespace(
            mission_id="m1",
            worker_id=None,
            ts_ms=1_800_000_000_000,
            payload=SimpleNamespace(event_type="MissionDispatched", prompt="x"),
        )
        await mbus.handlers[0](env)
        assert adapter.snapshot().primary_state == "WORKING"
        adapter.close()
        assert mbus.handlers == []

    asyncio.run(scenario())


def test_connection_state_is_independent_of_primary() -> None:
    adapter = HudStateAdapter(None)
    adapter.ingest(SystemStateChanged(new_state="SPEAKING"))
    snap = adapter.set_connection_state("RECONNECTING")
    assert (snap.primary_state, snap.connection_state) == ("SPEAKING", "RECONNECTING")


def test_a_snapshot_read_never_swallows_a_pending_push() -> None:
    """The REST route (or a surface attaching) may read between a change and
    the coalesced notification; the listeners must still be told."""

    async def scenario() -> None:
        adapter = HudStateAdapter(None, notify_interval_s=0.02)
        seen: list[str] = []
        adapter.add_listener(lambda snap: seen.append(snap.primary_state))
        adapter.ingest(TaskStarted(task_id="t1"))
        assert adapter.snapshot().primary_state == "WORKING"  # the "REST read"
        await asyncio.sleep(0.06)
        assert seen == ["WORKING"]

    asyncio.run(scenario())


def test_snapshot_is_safe_to_read_from_another_thread() -> None:
    import threading

    adapter = HudStateAdapter(None)
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                adapter.snapshot().to_dict()
        except BaseException as exc:  # noqa: BLE001 — surfaced by the assert below
            errors.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for i in range(2_000):
            adapter.ingest(TaskStarted(task_id=f"t{i}"))
    finally:
        stop.set()
        thread.join(timeout=5)
    assert errors == []
    assert adapter.snapshot().primary_state == "WORKING"


def test_shared_adapter_does_not_keep_its_bus_alive() -> None:
    import gc
    import weakref

    from jarvis.ui.hud import adapter as adapter_mod

    reset_hud_adapters()
    bus = EventBus()
    hud_adapter_for(bus)
    bus_ref = weakref.ref(bus)
    del bus
    gc.collect()
    assert bus_ref() is None
    assert len(adapter_mod._ADAPTERS) == 0  # noqa: SLF001
