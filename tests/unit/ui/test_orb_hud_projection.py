"""N-16: the orb presents the canonical HUD snapshot; it owns no state machine.

These drive the REAL ``OrbBusBridge`` and the REAL ``HudStateAdapter`` with the
same events and check what the surface is asked to show.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from jarvis.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionExecuted,
    ActionProposed,
    AudioOutFirst,
    DictationStarted,
    ErrorOccurred,
    SystemStateChanged,
    TaskCompleted,
    TaskStarted,
    VoiceBootStatus,
    VoiceSessionStarted,
)
from jarvis.ui.hud.adapter import HudStateAdapter
from jarvis.ui.hud.models import PRIMARY_STATES, HudApproval, HudSnapshot
from jarvis.ui.jarvisbar.modes import MODES
from ui.orb import bus_bridge as bridge_mod
from ui.orb.bus_bridge import OrbBusBridge
from ui.orb.hud_projection import ORB_MODE_FOR_PRIMARY, orb_mode_for, rest_mode


class _Bus:
    def __init__(self) -> None:
        self.types: list[type] = []

    def subscribe(self, event_type, _handler) -> None:
        self.types.append(event_type)


class _Surface:
    """Validates modes synchronously, like every shipped surface does."""

    def __init__(self, *, reject: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._reject = reject

    def show(self, mode: str = "listen") -> None:
        if mode not in MODES or mode in self._reject:
            raise ValueError(mode)
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide", None))

    def play_animation(self, name: str) -> None:
        pass

    def stop_animation(self, name: str) -> None:
        pass

    def set_level(self, level: float) -> None:
        pass

    def show_listening_transcript(self, text: str = "", duration_ms: int = 0) -> None:
        pass

    def hide_comment(self) -> None:
        pass

    def shows(self) -> list[str]:
        return [mode for kind, mode in self.calls if kind == "show" and mode is not None]

    def last(self) -> str | None:
        shows = self.shows()
        return shows[-1] if shows else None


def _rig(*, hide_on_idle: bool = False, release: bool = True, reject=frozenset()):
    surface = _Surface(reject=reject)
    hud = HudStateAdapter(None, notify_interval_s=0)
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_Bus(),
        orb=surface,
        idle_animations_enabled=False,
        hide_on_idle=hide_on_idle,
        hud=hud,
    )
    bridge.attach()
    return surface, hud, bridge, release


async def _both(bridge: OrbBusBridge, hud: HudStateAdapter, event) -> None:
    """Deliver one event the way the bus does: to the HUD and to the bridge."""
    hud.ingest(event)
    if isinstance(event, SystemStateChanged):
        await bridge._on_state(event)  # noqa: SLF001
    elif isinstance(event, VoiceSessionStarted):
        await bridge._on_session_started(event)  # noqa: SLF001
    elif isinstance(event, AudioOutFirst):
        await bridge._on_audio_out_first(event)  # noqa: SLF001
    elif isinstance(event, DictationStarted):
        await bridge._on_dictation_started(event)  # noqa: SLF001
    await asyncio.sleep(0)  # flush the adapter's coalesced notification


async def _release(bridge: OrbBusBridge) -> None:
    await bridge._on_voice_boot_status(VoiceBootStatus(ready=True, detail="listening"))  # noqa: SLF001


# ------------------------------------------------------------------ pure mapping


def test_every_primary_state_has_an_orb_mode_every_surface_accepts() -> None:
    assert set(ORB_MODE_FOR_PRIMARY) == set(PRIMARY_STATES)
    assert set(ORB_MODE_FOR_PRIMARY.values()) <= set(MODES)


def test_approval_gets_a_distinct_attention_mode() -> None:
    approval_mode = ORB_MODE_FOR_PRIMARY["WAITING_FOR_APPROVAL"]
    others = {m for p, m in ORB_MODE_FOR_PRIMARY.items() if p != "WAITING_FOR_APPROVAL"}
    assert approval_mode == "attention"
    assert approval_mode not in others


def test_rest_mode_uses_only_background_facts() -> None:
    assert rest_mode(None) == "idle"
    assert rest_mode(HudSnapshot()) == "idle"
    assert rest_mode(HudSnapshot(primary_state="SPEAKING", attention=("working",))) == "work"
    approval = HudApproval(approval_id="a", kind="tool_call", action="x", decision_channel="none")
    snap = HudSnapshot(
        primary_state="SPEAKING", attention=("approval", "working"), approval_requests=(approval,)
    )
    assert rest_mode(snap) == "attention"
    assert orb_mode_for(snap) == "speak"


def test_bridge_has_no_background_state_machine_of_its_own() -> None:
    """The bridge never subscribes to work/approval events — only the HUD does."""
    bus = _Bus()
    OrbBusBridge(bus=bus, orb=_Surface(), idle_animations_enabled=False).attach()  # type: ignore[arg-type]
    operational = {
        ActionApprovalRequired,
        ActionApproved,
        ActionProposed,
        ActionExecuted,
        TaskStarted,
        TaskCompleted,
        ErrorOccurred,
    }
    assert operational.isdisjoint(bus.types)


# ------------------------------------------------------------- bridge + adapter


def test_persistent_bar_projects_background_state_at_rest() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _release(bridge)
        trace = uuid4()
        await _both(bridge, hud, TaskStarted(task_id="t1"))
        assert surface.last() == "work"
        await _both(
            bridge, hud, ActionApprovalRequired(trace_id=trace, tool_name="rm", mission_id="m")
        )
        assert surface.last() == "attention"
        assert hud.snapshot().primary_state == "WAITING_FOR_APPROVAL"
        await _both(bridge, hud, ActionApproved(trace_id=trace, tool_name="rm", approved_by="user"))
        assert surface.last() == "work"
        await _both(bridge, hud, TaskCompleted(task_id="t1"))
        assert surface.last() == "idle"
        assert hud.snapshot().primary_state == "IDLE"

    asyncio.run(scenario())


def test_working_never_overrides_a_foreground_turn() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _release(bridge)
        await _both(bridge, hud, VoiceSessionStarted(session_id="s"))
        await _both(bridge, hud, SystemStateChanged(new_state="LISTENING"))
        await _both(bridge, hud, SystemStateChanged(new_state="THINKING", previous="LISTENING"))
        await _both(bridge, hud, SystemStateChanged(new_state="SPEAKING", previous="THINKING"))
        before = list(surface.calls)
        await _both(bridge, hud, TaskStarted(task_id="bg"))
        await _both(bridge, hud, ActionApprovalRequired(tool_name="x", mission_id="m"))
        assert surface.calls == before, "background state must not repaint a live turn"
        assert hud.snapshot().primary_state == "SPEAKING"
        assert "approval" in hud.snapshot().attention
        # The turn ends → the rest look is the background projection.
        await _both(bridge, hud, SystemStateChanged(new_state="IDLE", previous="SPEAKING"))
        assert surface.last() == "attention"

    asyncio.run(scenario())


def test_foreground_presentation_matches_the_snapshot() -> None:
    """Listening/thinking/speaking keep their existing looks, and they are the
    projection of the canonical primary state (SPEAKING's silent synthesis
    lead-in is the one documented presentational exception)."""

    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _release(bridge)
        await _both(bridge, hud, VoiceSessionStarted(session_id="s"))
        for new, prev in (("LISTENING", "IDLE"), ("THINKING", "LISTENING")):
            await _both(bridge, hud, SystemStateChanged(new_state=new, previous=prev))
            assert surface.last() == orb_mode_for(hud.snapshot())
        await _both(bridge, hud, SystemStateChanged(new_state="SPEAKING", previous="THINKING"))
        assert hud.snapshot().primary_state == "SPEAKING"
        assert surface.last() == "think", "silent TTS lead-in keeps the think look"
        await _both(bridge, hud, AudioOutFirst())
        assert surface.last() == orb_mode_for(hud.snapshot()) == "speak"

    asyncio.run(scenario())


def test_hidden_surface_is_never_revealed_by_background_state() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig(hide_on_idle=True)
        await _release(bridge)
        await _both(bridge, hud, TaskStarted(task_id="t1"))
        await _both(bridge, hud, ActionApprovalRequired(tool_name="x", mission_id="m"))
        assert surface.shows() == []
        assert hud.snapshot().primary_state == "WAITING_FOR_APPROVAL"

    asyncio.run(scenario())


def test_gated_bar_shows_background_state_only_after_release() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _both(bridge, hud, ActionApprovalRequired(tool_name="x", mission_id="m"))
        assert surface.shows() == [], "boot gate closed — nothing may be painted"
        await _release(bridge)
        assert surface.last() == "attention"

    asyncio.run(scenario())


def test_dictation_owns_the_bar_over_background_changes() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _release(bridge)
        await _both(bridge, hud, DictationStarted())
        assert surface.last() == "dictate"
        await _both(bridge, hud, TaskStarted(task_id="t1"))
        assert surface.last() == "dictate"

    asyncio.run(scenario())


def test_global_error_is_a_transient_notice_then_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge_mod, "ERROR_NOTICE_DWELL_S", 0.01)

    async def scenario() -> None:
        surface, hud, bridge, _ = _rig()
        await _release(bridge)
        await _both(bridge, hud, TaskStarted(task_id="t1"))
        await _both(
            bridge,
            hud,
            ErrorOccurred(layer="brain", error_type="Down", recoverable=False),
        )
        assert hud.snapshot().primary_state == "ERROR"
        assert surface.last() == "notice"
        await asyncio.sleep(0.05)
        assert surface.last() == "work", "after the dwell the bar is clickable again"

    asyncio.run(scenario())


def test_surface_failure_does_not_corrupt_backend_state() -> None:
    async def scenario() -> None:
        surface, hud, bridge, _ = _rig(reject=frozenset({"work", "attention"}))
        await _release(bridge)
        await _both(bridge, hud, TaskStarted(task_id="t1"))
        await _both(bridge, hud, ActionApprovalRequired(tool_name="x", mission_id="m"))
        # An older surface that lacks the looks rests on idle instead…
        assert surface.last() == "idle"
        # …and the canonical state is untouched by the presentation failure.
        snap = hud.snapshot()
        assert snap.primary_state == "WAITING_FOR_APPROVAL"
        assert len(snap.approval_requests) == 1

    asyncio.run(scenario())


def test_bridge_without_hud_behaves_exactly_as_before() -> None:
    async def scenario() -> None:
        surface = _Surface()
        bridge = OrbBusBridge(  # type: ignore[arg-type]
            bus=_Bus(), orb=surface, idle_animations_enabled=False, hide_on_idle=False
        )
        bridge.attach()
        await _release(bridge)
        await bridge._on_state(SystemStateChanged(new_state="LISTENING"))  # noqa: SLF001
        await bridge._on_state(SystemStateChanged(new_state="IDLE", previous="LISTENING"))  # noqa: SLF001
        assert surface.last() == "idle"

    asyncio.run(scenario())
