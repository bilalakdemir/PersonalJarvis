"""Orb/Bar projection of the canonical HUD snapshot (N-16).

The orb does not own an operational state machine. What Jarvis is *doing* —
listening, thinking, working on a mission, waiting for an approval, failed —
is decided once, by :class:`jarvis.ui.hud.HudStateAdapter`, and this module
only translates a :class:`~jarvis.ui.hud.models.HudSnapshot` into the
surface's existing coarse-mode vocabulary (``jarvis.ui.jarvisbar.modes``).

What stays local in ``OrbBusBridge`` is purely presentational choreography the
snapshot cannot and should not carry: the wave/think/nod/salute animations,
the transcript bubble, the mic-level routing, the dictation lane, the
session-resurrection latch, the boot visibility gate and the silent
TTS-synthesis lead-in (SPEAKING keeps the ``think`` look until the first
audible sample, ``AudioOutFirst``). Those are timing and visibility rules for
one surface, not facts about Jarvis.

Mapping (``ORB_MODE_FOR_PRIMARY``):

==========================  ==============  ===================================
primary state               coarse mode     notes
==========================  ==============  ===================================
IDLE                        ``idle``
LISTENING                   ``listen``
THINKING                    ``think``
SPEAKING                    ``speak``       ``think`` until audio is audible
WORKING                     ``work``        background only, never over a turn
WAITING_FOR_APPROVAL        ``attention``   distinct look; click = idle click
ERROR                       ``notice``      transient dwell, then the rest mode
==========================  ==============  ===================================

At rest (no voice session, no dictation, no notice in flight) a *persistent*
bar paints :func:`rest_mode` — the background part of the snapshot. A surface
that hides when idle (the mascot, the non-persistent bar) is never revealed by
background state: popping a hidden mascot for a mission would resurrect it
outside a session, exactly the bug the bridge's latch exists to prevent.
"""

from __future__ import annotations

from typing import Final

from jarvis.ui.hud.models import HudSnapshot
from jarvis.ui.jarvisbar.modes import MODES

ORB_MODE_FOR_PRIMARY: Final[dict[str, str]] = {
    "IDLE": "idle",
    "LISTENING": "listen",
    "THINKING": "think",
    "SPEAKING": "speak",
    "WORKING": "work",
    "WAITING_FOR_APPROVAL": "attention",
    "ERROR": "notice",
}

#: How long the transient ERROR notice stays before the rest mode returns.
ERROR_NOTICE_DWELL_S: Final[float] = 4.0


def orb_mode_for(snapshot: HudSnapshot) -> str:
    """The coarse mode that presents ``snapshot.primary_state``."""
    return ORB_MODE_FOR_PRIMARY.get(snapshot.primary_state, "idle")


def rest_mode(snapshot: HudSnapshot | None) -> str:
    """What a persistent surface shows while the voice lane is at rest.

    Only the BACKGROUND facts count here — the foreground is, by definition,
    not on this surface right now. Approval outranks work (it is the one thing
    that needs the user), errors are shown transiently (see bridge), never as
    a resting look that would make the bar inert.
    """
    if snapshot is None:
        return "idle"
    if snapshot.approval_requests:
        return "attention"
    if "working" in snapshot.attention:
        return "work"
    return "idle"


def is_new_global_error(previous: HudSnapshot | None, current: HudSnapshot) -> bool:
    """True on the edge INTO a global error (not on every snapshot during it)."""
    if current.primary_state != "ERROR":
        return False
    return previous is None or previous.primary_state != "ERROR"


def _assert_vocabulary() -> None:
    missing = sorted(set(ORB_MODE_FOR_PRIMARY.values()) - set(MODES))
    if missing:  # pragma: no cover — guarded by test_orb_hud_projection
        raise RuntimeError(f"orb projection uses modes no surface accepts: {missing}")


_assert_vocabulary()

__all__ = [
    "ERROR_NOTICE_DWELL_S",
    "ORB_MODE_FOR_PRIMARY",
    "is_new_global_error",
    "orb_mode_for",
    "rest_mode",
]
