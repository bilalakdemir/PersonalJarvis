"""The ONE definition of how HUD facts become a primary state.

Both the reducer (``HudReducer``) and every presentation surface (the orb
projection, the web workspace) depend on these rules instead of restating
them, so a surface cannot drift into a second, competing state machine.

Priority (first match wins) — NOT "last event wins":

1. ``ERROR``  — a *global* error is active (the voice runtime reported the
   supervisor ``ERROR`` state, or a core layer published a non-recoverable
   ``ErrorOccurred``). Scoped errors (one tool call, one UI socket, one
   project) never take the primary state; they surface as ``attention``.
2. The foreground interaction — ``LISTENING`` / ``THINKING`` / ``SPEAKING``.
   Whoever has the floor in the conversation keeps it: a background mission
   finishing, or an approval arriving, must not visually interrupt Jarvis
   while it is speaking or listening.
3. ``WAITING_FOR_APPROVAL`` — at least one pending, unexpired approval.
4. ``WORKING`` — background work is in flight (tool calls, tasks, missions,
   workers, Computer Use, Jarvis-agent jobs).
5. ``IDLE``.

The background facts that lose to the foreground stay visible through
``HudSnapshot.attention`` — they are never dropped.
"""

from __future__ import annotations

from typing import Final

from .models import PrimaryHudState

#: Supervisor / turn-taking states → semantic foreground state. Unknown states
#: map to ``None`` and are ignored by the reducer (fail closed: an unknown
#: label must not invent a state).
SUPERVISOR_TO_FOREGROUND: Final[dict[str, PrimaryHudState]] = {
    "IDLE": "IDLE",
    # PAUSED: voice is deliberately off — nothing is in the foreground.
    "PAUSED": "IDLE",
    # CONNECTING is the realtime handshake: the call is accepted and Jarvis is
    # getting ready to hear. Semantically the listening phase of the session;
    # the raw label survives in ``HudSnapshot.voice_state`` for the status line.
    "CONNECTING": "LISTENING",
    "LISTENING": "LISTENING",
    # Turn-taking sub-states the classic pipeline publishes on the same event.
    "USER_SPEAKING": "LISTENING",
    "WAITING_FOR_FINAL_TRANSCRIPT": "LISTENING",
    "WAITING_FOR_COMPLETION": "LISTENING",
    "THINKING": "THINKING",
    "SPEAKING": "SPEAKING",
    "ERROR": "ERROR",
}

FOREGROUND_ACTIVE: Final[frozenset[str]] = frozenset({"LISTENING", "THINKING", "SPEAKING"})

#: Error layers that belong to one UI transport/client, never to Jarvis as a
#: whole. A browser tab's socket dying is that tab's problem; it must not turn
#: the central reactor red for every other surface.
COMPONENT_ERROR_LAYER_PREFIXES: Final[tuple[str, ...]] = ("ui.", "channel.")


def foreground_from_supervisor(raw_state: str) -> PrimaryHudState | None:
    """Map a ``SystemStateChanged.new_state`` label; ``None`` when unknown."""
    return SUPERVISOR_TO_FOREGROUND.get(str(raw_state or "").strip().upper())


def resolve_primary_state(
    *,
    foreground: PrimaryHudState,
    global_error: bool,
    approvals_pending: bool,
    work_in_flight: bool,
) -> PrimaryHudState:
    """Apply the documented priority. Pure and total."""
    if global_error or foreground == "ERROR":
        return "ERROR"
    if foreground in FOREGROUND_ACTIVE:
        return foreground
    if approvals_pending:
        return "WAITING_FOR_APPROVAL"
    if work_in_flight:
        return "WORKING"
    return "IDLE"


def attention_flags(
    *,
    approvals_pending: bool,
    work_in_flight: bool,
    error_active: bool,
) -> tuple[str, ...]:
    """Background facts, in a stable order, independent of the primary state."""
    flags: list[str] = []
    if approvals_pending:
        flags.append("approval")
    if error_active:
        flags.append("error")
    if work_in_flight:
        flags.append("working")
    return tuple(flags)


def is_component_error_layer(layer: str) -> bool:
    text = str(layer or "")
    return any(text.startswith(prefix) for prefix in COMPONENT_ERROR_LAYER_PREFIXES)


__all__ = [
    "COMPONENT_ERROR_LAYER_PREFIXES",
    "FOREGROUND_ACTIVE",
    "SUPERVISOR_TO_FOREGROUND",
    "attention_flags",
    "foreground_from_supervisor",
    "is_component_error_layer",
    "resolve_primary_state",
]
