"""Canonical HUD semantic backend (N-15).

The HUD is a projection. Authoritative state stays with the EventBus, the
Project State Engine, the mission/worker system, the safety/approval system,
the memory domain, Computer Use and the voice runtime. Nothing in this package
executes tools, answers approvals, writes project state, promotes memory or
manages agents.

- :mod:`.models`    — immutable ``HudSnapshot`` and its parts (+ wire shape).
- :mod:`.semantics` — the ONE primary-state priority rule every surface uses.
- :mod:`.reducer`   — deterministic event → snapshot reduction.
- :mod:`.adapter`   — bus wiring, coalesced listeners, one adapter per bus.
"""

from __future__ import annotations

from .adapter import HudStateAdapter, hud_adapter_for, reset_hud_adapters
from .models import (
    HudActivity,
    HudApproval,
    HudComputerActivity,
    HudError,
    HudMemoryActivity,
    HudProject,
    HudSnapshot,
)
from .reducer import HudReducer, register_memory_mapper, unregister_memory_mapper
from .semantics import resolve_primary_state

__all__ = [
    "HudActivity",
    "HudApproval",
    "HudComputerActivity",
    "HudError",
    "HudMemoryActivity",
    "HudProject",
    "HudReducer",
    "HudSnapshot",
    "HudStateAdapter",
    "hud_adapter_for",
    "register_memory_mapper",
    "reset_hud_adapters",
    "resolve_primary_state",
    "unregister_memory_mapper",
]
