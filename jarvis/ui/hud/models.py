"""Immutable semantic HUD snapshot models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PrimaryHudState = Literal[
    "IDLE",
    "LISTENING",
    "THINKING",
    "WORKING",
    "WAITING_FOR_APPROVAL",
    "SPEAKING",
    "ERROR",
]
HudConnectionState = Literal["CONNECTED", "RECONNECTING", "DISCONNECTED"]
HudActivityStatus = Literal["running", "waiting", "completed", "failed"]
HudErrorScope = Literal["operation", "agent", "project", "system"]


@dataclass(frozen=True, slots=True)
class HudProject:
    project_id: str
    project_name: str = ""
    current_task: str | None = None
    state_revision: str = ""


@dataclass(frozen=True, slots=True)
class HudActivity:
    activity_id: str
    kind: str
    label: str
    status: HudActivityStatus
    trace_id: str = ""
    project_id: str | None = None
    mission_id: str | None = None
    task_id: str | None = None
    grant_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HudApproval:
    approval_id: str
    kind: str
    action: str
    reason: str
    trace_id: str = ""
    project_id: str | None = None
    mission_id: str | None = None
    transaction_id: str | None = None
    proposal_digest: str | None = None
    queue_item_id: int | None = None
    candidate_id: int | None = None
    expires_at_ns: int = 0
    scope_preview: str = ""


@dataclass(frozen=True, slots=True)
class HudMemoryActivity:
    activity_id: str
    kind: str
    status: str
    candidate_id: int | None = None
    project_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HudComputerActivity:
    active: bool = False
    phase: str = "OBSERVE"
    mission_id: str | None = None
    action: str = ""
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class HudError:
    scope: HudErrorScope
    code: str
    message: str
    trace_id: str = ""
    recoverable: bool = True


@dataclass(frozen=True, slots=True)
class HudSnapshot:
    primary_state: PrimaryHudState = "IDLE"
    connection_state: HudConnectionState = "CONNECTED"
    active_project: HudProject | None = None
    active_operations: tuple[HudActivity, ...] = ()
    approval_requests: tuple[HudApproval, ...] = ()
    agent_activity: tuple[HudActivity, ...] = ()
    memory_activity: tuple[HudMemoryActivity, ...] = ()
    computer_activity: HudComputerActivity = field(
        default_factory=HudComputerActivity
    )
    last_error: HudError | None = None
    updated_at_ns: int = 0


__all__ = [
    "HudActivity",
    "HudApproval",
    "HudComputerActivity",
    "HudConnectionState",
    "HudError",
    "HudErrorScope",
    "HudMemoryActivity",
    "HudProject",
    "HudSnapshot",
    "PrimaryHudState",
]
