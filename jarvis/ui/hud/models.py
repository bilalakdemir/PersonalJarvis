"""Immutable semantic HUD snapshot models (N-15).

A :class:`HudSnapshot` is a *projection*: every field is derived from events
other subsystems already publish (EventBus, the per-mission MissionBus). No
field here is authoritative for anything — the HUD never executes, approves,
mutates or promotes. Changing a value in a snapshot changes nothing in Jarvis.

Every text field that can originate from runtime data (tool arguments, error
messages, task titles) has passed :func:`jarvis.core.redact.safe_preview`
before it lands here, and is length-capped. Raw memory bodies, screen pixels,
prompts, model reasoning and file contents are never part of the model.

``to_dict`` produces the JSON-safe wire shape consumed by the web UI
(``frontend/src/types/hud.ts``). The two are kept in lock-step by
``tests/unit/ui/hud/test_hud_wire_parity.py`` (AP-4 five-layer rule).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final, Literal

#: Bumped when the wire shape changes incompatibly.
HUD_SCHEMA_VERSION: Final[int] = 1

PrimaryHudState = Literal[
    "IDLE",
    "LISTENING",
    "THINKING",
    "WORKING",
    "WAITING_FOR_APPROVAL",
    "SPEAKING",
    "ERROR",
]
PRIMARY_STATES: Final[tuple[str, ...]] = (
    "IDLE",
    "LISTENING",
    "THINKING",
    "WORKING",
    "WAITING_FOR_APPROVAL",
    "SPEAKING",
    "ERROR",
)

HudConnectionState = Literal["CONNECTED", "RECONNECTING", "DISCONNECTED"]
CONNECTION_STATES: Final[tuple[str, ...]] = ("CONNECTED", "RECONNECTING", "DISCONNECTED")

HudActivityStatus = Literal["running", "completed", "failed", "cancelled"]
ACTIVITY_STATUSES: Final[tuple[str, ...]] = ("running", "completed", "failed", "cancelled")

HudErrorScope = Literal["global", "operation", "agent", "project", "component"]
ERROR_SCOPES: Final[tuple[str, ...]] = ("global", "operation", "agent", "project", "component")

#: How the user can answer an approval card. The HUD never answers anything
#: itself — this names the EXISTING channel that owns the decision.
#:
#: - ``mission_tool_api`` — ``POST /api/missions/{mission_id}/tool-approvals/
#:   {trace_id}/approve|deny`` (``MissionToolApprovalCoordinator``). The only
#:   out-of-band approval route on ``develop``; the HUD may call it for exactly
#:   the ``(mission_id, trace_id)`` the card carries.
#: - ``chat_card`` — a typed-chat turn's own approval card answers it
#:   (``agent_chat.approval_bridge``); the HUD shows it read-only.
#: - ``project_state_api`` — exact N-14H project-state proposal identity.
#: - ``memory_promotion_api`` — exact persistent-memory candidate + digest.
#: - ``none`` — no safe out-of-band route; read-only.
ApprovalDecisionChannel = Literal[
    "mission_tool_api",
    "project_state_api",
    "memory_promotion_api",
    "chat_card",
    "none",
]
DECISION_CHANNELS: Final[tuple[str, ...]] = (
    "mission_tool_api",
    "project_state_api",
    "memory_promotion_api",
    "chat_card",
    "none",
)

ApprovalKind = Literal["tool_call", "project_state", "memory_promotion"]
APPROVAL_KINDS: Final[tuple[str, ...]] = ("tool_call", "project_state", "memory_promotion")


@dataclass(frozen=True, slots=True)
class HudProject:
    project_id: str
    project_name: str = ""
    current_task: str | None = None
    state_revision: str = ""
    #: False after ``ProjectStateInvalid`` until a newer load/commit.
    state_valid: bool = True
    issue_codes: tuple[str, ...] = ()
    updated_at_ns: int = 0


@dataclass(frozen=True, slots=True)
class HudActivity:
    """One unit of recorded operational work (tool call, task, mission, worker…)."""

    activity_id: str
    kind: str
    label: str
    status: HudActivityStatus = "running"
    trace_id: str = ""
    project_id: str | None = None
    mission_id: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    run_id: str | None = None
    #: Safe, capped one-line summary (redacted tool/action summary).
    detail: str = ""
    started_at_ns: int = 0
    updated_at_ns: int = 0


@dataclass(frozen=True, slots=True)
class HudApproval:
    """One pending approval, carrying its EXACT identity.

    ``approval_id`` is a composite of the identifiers the owning domain uses
    to decide the request — never a positional index, never "the pending one".
    """

    approval_id: str
    kind: ApprovalKind
    action: str
    decision_channel: ApprovalDecisionChannel
    reason: str = ""
    risk_tier: str = ""
    target_preview: str = ""
    trace_id: str = ""
    project_id: str | None = None
    mission_id: str | None = None
    worker_id: str | None = None
    transaction_id: str | None = None
    proposal_digest: str | None = None
    #: Durable project-state proposals (N-14H) also carry the promotion-queue
    #: row and the memory candidate they were built from.
    queue_item_id: int | None = None
    candidate_id: int | None = None
    files_affected: tuple[str, ...] = ()
    requested_at_ns: int = 0
    #: 0 = no expiry advertised by the domain.
    expires_at_ns: int = 0
    #: Why the card is read-only when ``decision_channel != mission_tool_api``.
    read_only_reason: str = ""


@dataclass(frozen=True, slots=True)
class HudMemoryActivity:
    """Metadata-only memory activity. Never carries a memory body."""

    activity_id: str
    kind: str
    status: str = "completed"
    #: Namespace / subject / slug — an identifier, not content.
    subject: str = ""
    project_id: str | None = None
    candidate_id: int | None = None
    at_ns: int = 0


@dataclass(frozen=True, slots=True)
class HudComputerActivity:
    active: bool = False
    mission_ids: tuple[str, ...] = ()
    #: Last Computer-Use loop phase (observe/plan/think/act/verify/…).
    phase: str = ""
    #: Last planned action KIND only (click/type/…). Never the target text.
    last_action_kind: str = ""
    screen_capture_active: bool = False
    #: ``monitor`` | ``window`` — never an app or window title.
    capture_target_kind: str = ""
    updated_at_ns: int = 0


@dataclass(frozen=True, slots=True)
class HudError:
    scope: HudErrorScope
    code: str
    message: str = ""
    layer: str = ""
    trace_id: str = ""
    #: The operation / mission / project id the error is scoped to, if any.
    related_id: str | None = None
    recoverable: bool = True
    at_ns: int = 0


@dataclass(frozen=True, slots=True)
class HudSnapshot:
    primary_state: PrimaryHudState = "IDLE"
    connection_state: HudConnectionState = "CONNECTED"
    #: Raw supervisor state behind the foreground (e.g. ``CONNECTING``) for a
    #: status line; ``primary_state`` is the semantic one to render.
    voice_state: str = "IDLE"
    #: Background facts that deserve attention even while the foreground owns
    #: ``primary_state`` (Jarvis SPEAKING while an approval waits):
    #: ``approval`` / ``working`` / ``error``.
    attention: tuple[str, ...] = ()
    active_project: HudProject | None = None
    active_operations: tuple[HudActivity, ...] = ()
    approval_requests: tuple[HudApproval, ...] = ()
    agent_activity: tuple[HudActivity, ...] = ()
    memory_activity: tuple[HudMemoryActivity, ...] = ()
    computer_activity: HudComputerActivity = field(default_factory=HudComputerActivity)
    last_error: HudError | None = None
    updated_at_ns: int = 0
    #: Identifies one adapter lifetime; a client that sees a new epoch must
    #: drop its cached snapshot instead of comparing revisions across boots.
    epoch: str = ""
    #: Monotonic per-epoch change counter — reconnect/resync ordering key.
    revision: int = 0
    schema_version: int = HUD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe wire shape (tuples → lists, nested dataclasses → dicts)."""
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


__all__ = [
    "ACTIVITY_STATUSES",
    "APPROVAL_KINDS",
    "CONNECTION_STATES",
    "DECISION_CHANNELS",
    "ERROR_SCOPES",
    "HUD_SCHEMA_VERSION",
    "PRIMARY_STATES",
    "ApprovalDecisionChannel",
    "ApprovalKind",
    "HudActivity",
    "HudActivityStatus",
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
