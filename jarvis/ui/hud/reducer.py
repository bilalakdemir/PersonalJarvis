"""Deterministic EventBus → HUD reducer (N-15).

The reducer turns events that other subsystems ALREADY publish into one
canonical :class:`~jarvis.ui.hud.models.HudSnapshot`. It is pure bookkeeping:
it never publishes, never calls a tool, never answers an approval, never
writes project state or memory. Feeding it an event changes only what the
HUD *shows*.

Rules it enforces (each one is covered by ``tests/unit/ui/hud``):

* **No "last event wins".** Foreground interaction (voice supervisor state)
  and background work (tool calls, tasks, missions, workers, Computer Use,
  approvals) are tracked in separate ledgers and combined only at snapshot
  time by :func:`~jarvis.ui.hud.semantics.resolve_primary_state`.
* **Exact correlation.** Every open item is keyed by the identifiers its
  domain uses: ``(trace_id, tool_name)`` for a tool call, ``task_id``,
  ``run_id``, ``mission_id``, ``worker_id``, ``(project_id, transaction_id,
  proposal_digest)``. A terminal event closes ONLY the item with the same key.
  Two concurrent calls with the same key are reference-counted, so one
  completion never closes the other.
* **Stale / out-of-order events** never roll state backward. Single-valued
  facts (foreground state, a project's CURRENT task, the Computer-Use phase)
  keep the timestamp of their last writer and ignore anything older. A close
  that arrives before its open is remembered as an orphan, and the late open
  whose timestamp is not newer than that close is matched to it instead of
  resurrecting a finished operation.
* **Privacy.** Every runtime-derived string passes ``safe_preview`` (secret
  masking + length cap). Memory bodies, prompts, screen content, CU target
  text and model reasoning are never read into the model at all.
* **Bounded.** Every collection is capped; nothing grows with uptime.

Timestamps are the events' own ``timestamp_ns`` (wall clock, ``time.time_ns``);
``snapshot(now_ns=...)`` applies time-based expiry (approval ``expires_at_ns``,
display TTLs) against an injected clock so the whole reducer is replayable.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Final

from jarvis.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionDenied,
    ActionExecuted,
    ActionPlanned,
    ActionProposed,
    CUControlEnded,
    CUControlStarted,
    CUStepProfiled,
    ErrorOccurred,
    Event,
    HarnessCompleted,
    HarnessDispatched,
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
    MemoryUpdated,
    MissionCompleted,
    ProfileUpdated,
    ScreenCaptureAnnounced,
    ScreenCaptureCompleted,
    SystemStarted,
    SystemStateChanged,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskInterrupted,
    TaskScheduled,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
    VoiceSessionStarted,
    WikiPageChanged,
    WorkflowCompleted,
    WorkflowStarted,
    WorkflowStepStarted,
)
from jarvis.core.project_state_events import (
    CurrentTaskChanged,
    ProjectContextResolved,
    ProjectStateApprovalRequired,
    ProjectStateCommitted,
    ProjectStateInvalid,
    ProjectStateLoaded,
    ProjectStateMemoryApprovalAccepted,
    ProjectStateMemoryApprovalRejected,
    ProjectStateMemoryProposalCreated,
    ProjectStateRolledBack,
    ProjectStateTransactionFailed,
    ProjectStateTransactionRejected,
    ProjectStateTransactionStarted,
)
from jarvis.core.redact import safe_preview

from .models import (
    HudActivity,
    HudApproval,
    HudComputerActivity,
    HudConnectionState,
    HudError,
    HudMemoryActivity,
    HudProject,
    HudSnapshot,
    PrimaryHudState,
)
from .semantics import (
    attention_flags,
    foreground_from_supervisor,
    is_component_error_layer,
    resolve_primary_state,
)

log = logging.getLogger(__name__)

# --- caps --------------------------------------------------------------------
MAX_OPEN_ITEMS: Final[int] = 64
MAX_ORPHANS: Final[int] = 256
MAX_RECENT_AGENTS: Final[int] = 8
MAX_MEMORY_ITEMS: Final[int] = 12
MAX_PROJECTS: Final[int] = 16
MAX_TASK_TITLES: Final[int] = 128

LABEL_CHARS: Final[int] = 80
DETAIL_CHARS: Final[int] = 160
MESSAGE_CHARS: Final[int] = 240

_SECOND_NS: Final[int] = 1_000_000_000
#: A scoped error stops claiming attention after this long (display only).
SCOPED_ERROR_TTL_NS: Final[int] = 300 * _SECOND_NS
#: Display-only safety valve: a tool call whose close event was lost must not
#: keep the reactor "working" forever. The bound matches the executor's own
#: approval cap (``ToolExecutor.MAX_APPROVAL_TIMEOUT_S`` = 15 min). Known gap:
#: a voice two-turn confirmation that is never answered publishes no event at
#: all, so its proposed call reads as running until this valve closes it.
STALE_AFTER_NS: Final[dict[str, int]] = {
    "tool": 15 * 60 * _SECOND_NS,
    "agent_tool": 15 * 60 * _SECOND_NS,
    "harness": 2 * 60 * 60 * _SECOND_NS,
}

_CHAT_APPROVAL_REF_PREFIX: Final[str] = "agent-chat:"
_READ_ONLY_NO_ROUTE: Final[str] = "no_out_of_band_route"
_READ_ONLY_CHAT: Final[str] = "answered_in_chat"
_READ_ONLY_PROJECT_STATE: Final[str] = "project_state_route_unavailable"

#: Worker kills that are a decision, not a failure.
_NON_ERROR_WORKER_KILLS: Final[frozenset[str]] = frozenset({"user", "parent_cancelled"})


# --- extension point -----------------------------------------------------------
MemoryMapper = Callable[[Event], "HudMemoryActivity | None"]
_MEMORY_MAPPERS: dict[str, MemoryMapper] = {}


def register_memory_mapper(event_name: str, mapper: MemoryMapper) -> None:
    """Teach the HUD a memory lifecycle event that does not exist on this build.

    Governed-memory events (candidate created, promotion proposed/approved,
    retention expiry, …) are owned by the memory domain and may land later
    (N-14H/N-14I). Rather than importing or re-implementing them here, the
    memory domain registers a mapper for its event class NAME. The mapper
    returns metadata only; the reducer re-sanitizes every string it returns.
    A mapper that raises is logged and ignored (fail closed: no activity).
    """
    name = str(event_name or "").strip()
    if not name:
        raise ValueError("event_name must be non-empty")
    _MEMORY_MAPPERS[name] = mapper


def unregister_memory_mapper(event_name: str) -> None:
    _MEMORY_MAPPERS.pop(str(event_name or "").strip(), None)


def registered_memory_mappers() -> tuple[str, ...]:
    return tuple(sorted(_MEMORY_MAPPERS))


# --- helpers ---------------------------------------------------------------------
def _safe(value: Any, limit: int = DETAIL_CHARS) -> str:
    if value is None:
        return ""
    return safe_preview(value, max_chars=limit).strip()


def _opt(value: Any, limit: int = LABEL_CHARS) -> str | None:
    text = _safe(value, limit)
    return text or None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):  # a non-numeric id is dropped, not guessed
        return None
    return number if number > 0 else None


def _ts(event: Any) -> int:
    try:
        return max(0, int(getattr(event, "timestamp_ns", 0) or 0))
    except (TypeError, ValueError):  # malformed stamp = unknown time, never trusted
        return 0


def _trace(event: Any) -> str:
    return str(getattr(event, "trace_id", "") or "")


@dataclass(slots=True)
class _Open:
    count: int
    record: HudActivity


class _Ledger:
    """Open/close bookkeeping with refcounts and out-of-order orphan matching."""

    def __init__(self) -> None:
        self._open: OrderedDict[str, _Open] = OrderedDict()
        self._orphans: OrderedDict[str, list[int]] = OrderedDict()

    def open(self, key: str, ts: int, record: HudActivity) -> bool:
        orphans = self._orphans.get(key)
        if orphans:
            # A close for this key was processed before this open, and the
            # close is not older than the open: they are the same operation,
            # delivered out of order. Match them instead of resurrecting it.
            for idx, close_ts in enumerate(orphans):
                if close_ts >= ts:
                    orphans.pop(idx)
                    if not orphans:
                        self._orphans.pop(key, None)
                    return False
        entry = self._open.get(key)
        if entry is None:
            self._open[key] = _Open(1, record)
            while len(self._open) > MAX_OPEN_ITEMS:
                self._open.popitem(last=False)
        else:
            entry.count += 1
            entry.record = replace(entry.record, updated_at_ns=max(entry.record.updated_at_ns, ts))
        return True

    def close(self, key: str, ts: int) -> HudActivity | None:
        """Close one reference. Returns the record when the item fully closed."""
        entry = self._open.get(key)
        if entry is None:
            bucket = self._orphans.setdefault(key, [])
            bucket.append(ts)
            self._orphans.move_to_end(key)
            while len(self._orphans) > MAX_ORPHANS:
                self._orphans.popitem(last=False)
            return None
        entry.count -= 1
        if entry.count > 0:
            return None
        self._open.pop(key, None)
        return entry.record

    def force_close(self, key: str) -> HudActivity | None:
        entry = self._open.pop(key, None)
        return entry.record if entry is not None else None

    def update(self, key: str, ts: int, **changes: Any) -> bool:
        entry = self._open.get(key)
        if entry is None or ts < entry.record.updated_at_ns:
            return False
        entry.record = replace(entry.record, updated_at_ns=ts, **changes)
        return True

    def get(self, key: str) -> HudActivity | None:
        entry = self._open.get(key)
        return entry.record if entry is not None else None

    def keys(self) -> list[str]:
        return list(self._open)

    def records(self) -> list[HudActivity]:
        return [entry.record for entry in self._open.values()]

    def __contains__(self, key: object) -> bool:
        return key in self._open

    def __len__(self) -> int:
        return len(self._open)


class HudReducer:
    """Stateful but deterministic: same events + same clock → same snapshot."""

    def __init__(self) -> None:
        self._foreground: PrimaryHudState = "IDLE"
        self._voice_state: str = "IDLE"
        self._foreground_ts = 0
        self._global_error: HudError | None = None
        self._scoped_error: HudError | None = None
        self._connection: HudConnectionState = "CONNECTED"

        self._ops = _Ledger()
        self._agents = _Ledger()
        self._recent_agents: deque[HudActivity] = deque(maxlen=MAX_RECENT_AGENTS)

        self._approvals: OrderedDict[str, HudApproval] = OrderedDict()
        self._approval_orphans: OrderedDict[str, int] = OrderedDict()
        # (trace_id, tool_name) → approval_id, so a terminal tool event that
        # carries no mission id still closes exactly its own card.
        self._tool_approval_index: dict[tuple[str, str], str] = {}

        self._projects: OrderedDict[str, HudProject] = OrderedDict()
        self._active_project_id: str | None = None

        self._memory: deque[HudMemoryActivity] = deque(maxlen=MAX_MEMORY_ITEMS)
        self._memory_seq = 0

        self._cu_missions: OrderedDict[str, int] = OrderedDict()
        self._cu_orphans: OrderedDict[str, int] = OrderedDict()
        self._computer = HudComputerActivity()

        self._task_titles: OrderedDict[str, str] = OrderedDict()
        self._mission_workers: dict[str, set[str]] = {}
        self._mission_projects: dict[str, tuple[str | None, str | None]] = {}

        self._updated_at_ns = 0

        self._handlers: dict[type, Callable[[Any], bool]] = {
            SystemStateChanged: self._on_system_state,
            VoiceSessionStarted: self._on_session_started,
            SystemStarted: self._on_system_started,
            ErrorOccurred: self._on_error,
            ActionProposed: self._on_action_proposed,
            ActionApprovalRequired: self._on_approval_required,
            ActionApproved: self._on_action_terminal,
            ActionDenied: self._on_action_terminal,
            ActionExecuted: self._on_action_terminal,
            ToolCallStarted: self._on_tool_call_started,
            ToolCallCompleted: self._on_tool_call_completed,
            HarnessDispatched: self._on_harness_dispatched,
            HarnessCompleted: self._on_harness_completed,
            TaskScheduled: self._on_task_scheduled,
            TaskStarted: self._on_task_started,
            TaskCompleted: self._on_task_terminal,
            TaskFailed: self._on_task_terminal,
            TaskInterrupted: self._on_task_terminal,
            TaskCancelled: self._on_task_terminal,
            WorkflowStarted: self._on_workflow_started,
            WorkflowStepStarted: self._on_workflow_step,
            WorkflowCompleted: self._on_workflow_completed,
            JarvisAgentTaskStarted: self._on_agent_started,
            JarvisAgentTaskCompleted: self._on_agent_completed,
            MissionCompleted: self._on_mission_completed,
            CUControlStarted: self._on_cu_started,
            CUControlEnded: self._on_cu_ended,
            CUStepProfiled: self._on_cu_step,
            ActionPlanned: self._on_cu_action_planned,
            ScreenCaptureAnnounced: self._on_capture_announced,
            ScreenCaptureCompleted: self._on_capture_completed,
            ProjectContextResolved: self._on_project_resolved,
            ProjectStateLoaded: self._on_project_loaded,
            ProjectStateInvalid: self._on_project_invalid,
            CurrentTaskChanged: self._on_current_task_changed,
            ProjectStateCommitted: self._on_project_committed,
            ProjectStateApprovalRequired: self._on_project_approval_required,
            ProjectStateMemoryProposalCreated: self._on_project_approval_required,
            ProjectStateMemoryApprovalAccepted: self._on_project_memory_decision,
            ProjectStateMemoryApprovalRejected: self._on_project_memory_decision,
            ProjectStateTransactionStarted: self._on_project_transaction_closed,
            ProjectStateTransactionRejected: self._on_project_transaction_closed,
            ProjectStateTransactionFailed: self._on_project_transaction_closed,
            ProjectStateRolledBack: self._on_project_transaction_closed,
            MemoryUpdated: self._on_memory_updated,
            ProfileUpdated: self._on_profile_updated,
            WikiPageChanged: self._on_wiki_changed,
        }

    # ------------------------------------------------------------------ input
    def handles(self, event: Event) -> bool:
        return type(event) in self._handlers or type(event).__name__ in _MEMORY_MAPPERS

    def apply(self, event: Event) -> bool:
        """Reduce one bus event. Returns True when HUD-visible state changed."""
        handler = self._handlers.get(type(event))
        changed = False
        if handler is not None:
            changed = bool(handler(event))
        else:
            mapper = _MEMORY_MAPPERS.get(type(event).__name__)
            if mapper is not None:
                changed = self._apply_memory_mapper(mapper, event)
        if changed:
            self._updated_at_ns = max(self._updated_at_ns, _ts(event))
        return changed

    def apply_mission_envelope(self, envelope: Any) -> bool:
        """Reduce one MissionBus envelope (duck-typed; no missions import)."""
        payload = getattr(envelope, "payload", None)
        etype = str(getattr(payload, "event_type", "") or "")
        mission_id = str(getattr(envelope, "mission_id", "") or "")
        if not etype or not mission_id:
            return False
        try:
            ts = max(0, int(getattr(envelope, "ts_ms", 0) or 0)) * 1_000_000
        except (TypeError, ValueError):  # malformed stamp = unknown time, never trusted
            ts = 0
        worker_id = _opt(getattr(envelope, "worker_id", None))
        changed = self._reduce_mission(etype, mission_id, worker_id, payload, ts)
        if changed:
            self._updated_at_ns = max(self._updated_at_ns, ts)
        return changed

    def set_connection_state(self, state: HudConnectionState) -> bool:
        if state == self._connection:
            return False
        self._connection = state
        return True

    # --------------------------------------------------------------- output
    def snapshot(self, *, now_ns: int, epoch: str = "", revision: int = 0) -> HudSnapshot:
        self._expire(now_ns)
        approvals = tuple(self._approvals.values())
        ops = tuple(self._ops.records())
        running_agents = tuple(self._agents.records())
        agents = running_agents + tuple(reversed(self._recent_agents))
        work = bool(ops or running_agents or self._computer.active)
        scoped = self._scoped_error
        if scoped is not None and now_ns - scoped.at_ns > SCOPED_ERROR_TTL_NS:
            scoped = None
        last_error = self._global_error or scoped
        global_error = self._global_error is not None
        primary = resolve_primary_state(
            foreground=self._foreground,
            global_error=global_error,
            approvals_pending=bool(approvals),
            work_in_flight=work,
        )
        project = (
            self._projects.get(self._active_project_id)
            if self._active_project_id is not None
            else None
        )
        return HudSnapshot(
            primary_state=primary,
            connection_state=self._connection,
            voice_state=self._voice_state,
            attention=attention_flags(
                approvals_pending=bool(approvals),
                work_in_flight=work,
                error_active=last_error is not None,
            ),
            active_project=project,
            active_operations=ops,
            approval_requests=approvals,
            agent_activity=agents,
            memory_activity=tuple(reversed(self._memory)),
            computer_activity=self._computer,
            last_error=last_error,
            updated_at_ns=self._updated_at_ns,
            epoch=epoch,
            revision=revision,
        )

    def next_deadline_ns(self) -> int | None:
        """Earliest future instant at which a time-based expiry changes the
        snapshot (approval expiry). ``None`` when nothing is scheduled."""
        deadlines = [a.expires_at_ns for a in self._approvals.values() if a.expires_at_ns > 0]
        return min(deadlines) if deadlines else None

    # ---------------------------------------------------------- foreground
    def _on_system_state(self, event: SystemStateChanged) -> bool:
        ts = _ts(event)
        if ts < self._foreground_ts:
            return False  # stale supervisor edge — never roll back
        mapped = foreground_from_supervisor(event.new_state)
        if mapped is None:
            log.debug("HUD ignored unknown supervisor state %r", event.new_state)
            return False
        self._foreground_ts = ts
        self._foreground = mapped
        self._voice_state = str(event.new_state).strip().upper()
        if mapped == "ERROR":
            if self._global_error is None or self._global_error.at_ns <= ts:
                self._global_error = HudError(
                    scope="global",
                    code="voice_runtime_error",
                    message="",
                    layer=_safe(event.source_layer, LABEL_CHARS),
                    trace_id=_trace(event),
                    recoverable=True,
                    at_ns=ts,
                )
        elif self._global_error is not None and self._global_error.at_ns <= ts:
            # A newer, healthy supervisor edge is the sign of life that ends a
            # global error. An OLDER edge (out of order) cannot clear it.
            self._global_error = None
        return True

    def _on_session_started(self, event: VoiceSessionStarted) -> bool:
        ts = _ts(event)
        if self._global_error is not None and self._global_error.at_ns <= ts:
            self._global_error = None
            return True
        return False

    def _on_system_started(self, event: SystemStarted) -> bool:
        return self._on_session_started(event)  # type: ignore[arg-type]

    # ---------------------------------------------------------------- errors
    def _on_error(self, event: ErrorOccurred) -> bool:
        ts = _ts(event)
        trace = _trace(event)
        layer = _safe(event.layer or event.source_layer, LABEL_CHARS)
        code = _safe(event.error_type, LABEL_CHARS) or "error"
        message = _safe(event.message, MESSAGE_CHARS)
        if not event.recoverable and not is_component_error_layer(layer):
            if self._global_error is None or self._global_error.at_ns <= ts:
                self._global_error = HudError(
                    scope="global",
                    code=code,
                    message=message,
                    layer=layer,
                    trace_id=trace,
                    recoverable=False,
                    at_ns=ts,
                )
                return True
            return False
        related, scope = self._correlate_trace(trace)
        if is_component_error_layer(layer) and related is None:
            scope = "component"
        return self._set_scoped_error(
            HudError(
                scope=scope,
                code=code,
                message=message,
                layer=layer,
                trace_id=trace,
                related_id=related,
                recoverable=bool(event.recoverable),
                at_ns=ts,
            )
        )

    def _correlate_trace(self, trace: str) -> tuple[str | None, str]:
        if trace:
            for record in self._ops.records():
                if record.trace_id == trace:
                    return record.activity_id, "operation"
            for record in self._agents.records():
                if record.trace_id == trace:
                    return record.activity_id, "agent"
        return None, "component"

    def _set_scoped_error(self, error: HudError) -> bool:
        current = self._scoped_error
        if current is not None and current.at_ns > error.at_ns:
            return False
        self._scoped_error = error
        return True

    # ---------------------------------------------------------- tool calls
    @staticmethod
    def _tool_key(trace: str, tool: str) -> str:
        return f"tool:{trace}:{tool}"

    def _on_action_proposed(self, event: ActionProposed) -> bool:
        ts = _ts(event)
        trace = _trace(event)
        tool = _safe(event.tool_name, LABEL_CHARS) or "tool"
        key = self._tool_key(trace, tool)
        return self._ops.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="tool",
                label=tool,
                trace_id=trace,
                detail=_safe(event.args),
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_action_terminal(self, event: ActionApproved | ActionDenied | ActionExecuted) -> bool:
        ts = _ts(event)
        trace = _trace(event)
        tool = _safe(event.tool_name, LABEL_CHARS) or "tool"
        # Only a DECISION may be remembered as an out-of-order orphan; an
        # ActionExecuted for a call that never needed approval must not
        # pre-empt a future card.
        changed = self._close_tool_approval(
            trace, tool, ts, remember_orphan=not isinstance(event, ActionExecuted)
        )
        if isinstance(event, ActionApproved):
            # Approved = the call is about to run; the operation stays open
            # until ActionExecuted closes it.
            return changed
        key = self._tool_key(trace, tool)
        closed = self._ops.close(key, ts)
        changed = changed or closed is not None
        if isinstance(event, ActionExecuted) and not event.success:
            changed = (
                self._set_scoped_error(
                    HudError(
                        scope="operation",
                        code="tool_failed",
                        message=_safe(event.error, MESSAGE_CHARS),
                        layer="safety.tool_executor",
                        trace_id=trace,
                        related_id=key,
                        recoverable=True,
                        at_ns=ts,
                    )
                )
                or changed
            )
        return changed

    def _on_tool_call_started(self, event: ToolCallStarted) -> bool:
        ts = _ts(event)
        trace = _trace(event)
        key = f"agent_tool:{trace}"
        tool = _safe(event.tool_name, LABEL_CHARS) or "tool"
        return self._ops.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="agent_tool",
                label=tool,
                trace_id=trace,
                detail=_safe(event.args_preview),
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_tool_call_completed(self, event: ToolCallCompleted) -> bool:
        ts = _ts(event)
        key = f"agent_tool:{_trace(event)}"
        closed = self._ops.close(key, ts)
        changed = closed is not None
        if not event.success and closed is not None:
            changed = (
                self._set_scoped_error(
                    HudError(
                        scope="operation",
                        code="tool_failed",
                        message=_safe(event.error, MESSAGE_CHARS),
                        layer="agent",
                        trace_id=_trace(event),
                        related_id=key,
                        at_ns=ts,
                    )
                )
                or changed
            )
        return changed

    def _on_harness_dispatched(self, event: HarnessDispatched) -> bool:
        ts = _ts(event)
        harness = _safe(event.harness, LABEL_CHARS) or "harness"
        key = f"harness:{_trace(event)}:{harness}"
        return self._ops.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="harness",
                label=harness,
                trace_id=_trace(event),
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_harness_completed(self, event: HarnessCompleted) -> bool:
        harness = _safe(event.harness, LABEL_CHARS) or "harness"
        return self._ops.close(f"harness:{_trace(event)}:{harness}", _ts(event)) is not None

    # -------------------------------------------------------------- tasks
    def _on_task_scheduled(self, event: TaskScheduled) -> bool:
        task_id = str(event.task_id or "")
        if task_id:
            self._task_titles[task_id] = _safe(event.title, LABEL_CHARS)
            self._task_titles.move_to_end(task_id)
            while len(self._task_titles) > MAX_TASK_TITLES:
                self._task_titles.popitem(last=False)
        return False

    def _on_task_started(self, event: TaskStarted) -> bool:
        task_id = str(event.task_id or "")
        if not task_id:
            return False
        ts = _ts(event)
        key = f"task:{task_id}"
        return self._ops.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="task",
                label=self._task_titles.get(task_id) or "Scheduled task",
                trace_id=_trace(event),
                task_id=task_id,
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_task_terminal(
        self, event: TaskCompleted | TaskFailed | TaskInterrupted | TaskCancelled
    ) -> bool:
        task_id = str(event.task_id or "")
        if not task_id:
            return False
        ts = _ts(event)
        key = f"task:{task_id}"
        closed = self._ops.close(key, ts)
        changed = closed is not None
        if isinstance(event, TaskFailed):
            changed = (
                self._set_scoped_error(
                    HudError(
                        scope="operation",
                        code="task_failed",
                        message=_safe(event.error, MESSAGE_CHARS),
                        layer="tasks",
                        trace_id=_trace(event),
                        related_id=key,
                        at_ns=ts,
                    )
                )
                or changed
            )
        return changed

    def _on_workflow_started(self, event: WorkflowStarted) -> bool:
        run_id = str(event.run_id or "")
        if not run_id:
            return False
        ts = _ts(event)
        key = f"workflow:{run_id}"
        return self._ops.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="workflow",
                label=_safe(event.title, LABEL_CHARS) or "Workflow",
                trace_id=_trace(event),
                run_id=run_id,
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_workflow_step(self, event: WorkflowStepStarted) -> bool:
        key = f"workflow:{event.run_id}"
        return self._ops.update(key, _ts(event), detail=_safe(event.label, LABEL_CHARS))

    def _on_workflow_completed(self, event: WorkflowCompleted) -> bool:
        ts = _ts(event)
        key = f"workflow:{event.run_id}"
        closed = self._ops.close(key, ts)
        changed = closed is not None
        if not event.success and closed is not None:
            changed = (
                self._set_scoped_error(
                    HudError(
                        scope="operation",
                        code="workflow_failed",
                        message=_safe(event.error, MESSAGE_CHARS),
                        layer="workflows",
                        trace_id=_trace(event),
                        related_id=key,
                        at_ns=ts,
                    )
                )
                or changed
            )
        return changed

    # ------------------------------------------------------------- agents
    def _finish_agent(self, key: str, ts: int, status: str) -> bool:
        record = self._agents.close(key, ts)
        if record is None:
            return False
        self._recent_agents.append(
            replace(record, status=status, updated_at_ns=max(record.updated_at_ns, ts))  # type: ignore[arg-type]
        )
        return True

    def _on_agent_started(self, event: JarvisAgentTaskStarted) -> bool:
        ts = _ts(event)
        key = f"agent:{_trace(event)}"
        provider = _safe(event.provider, LABEL_CHARS)
        return self._agents.open(
            key,
            ts,
            HudActivity(
                activity_id=key,
                kind="jarvis_agent",
                label=provider or "Jarvis agent",
                trace_id=_trace(event),
                detail=_safe(event.utterance, DETAIL_CHARS),
                started_at_ns=ts,
                updated_at_ns=ts,
            ),
        )

    def _on_agent_completed(self, event: JarvisAgentTaskCompleted) -> bool:
        ts = _ts(event)
        key = f"agent:{_trace(event)}"
        changed = self._finish_agent(key, ts, "completed" if event.success else "failed")
        if changed and not event.success:
            self._set_scoped_error(
                HudError(
                    scope="agent",
                    code="agent_failed",
                    message=_safe(event.error, MESSAGE_CHARS),
                    layer="agent",
                    trace_id=_trace(event),
                    related_id=key,
                    at_ns=ts,
                )
            )
        return changed

    def _on_mission_completed(self, event: MissionCompleted) -> bool:
        return self._close_mission(
            str(event.mission_id or ""),
            _ts(event),
            status=str(event.status or "approved"),
            reason=event.reason,
            trace=_trace(event),
        )

    def _close_mission(
        self, mission_id: str, ts: int, *, status: str, reason: str = "", trace: str = ""
    ) -> bool:
        if not mission_id:
            return False
        changed = False
        for worker_id in sorted(self._mission_workers.pop(mission_id, set())):
            key = f"worker:{mission_id}:{worker_id}"
            if key in self._agents:
                changed = self._finish_agent(key, ts, "cancelled") or changed
        terminal = {
            "approved": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "timed_out": "failed",
        }.get(status, "completed")
        key = f"mission:{mission_id}"
        changed = self._finish_agent(key, ts, terminal) or changed
        self._mission_projects.pop(mission_id, None)
        if terminal == "failed" and changed:
            self._set_scoped_error(
                HudError(
                    scope="agent",
                    code=f"mission_{status}",
                    message=_safe(reason, MESSAGE_CHARS),
                    layer="missions",
                    trace_id=trace,
                    related_id=key,
                    at_ns=ts,
                )
            )
        return changed

    def _reduce_mission(
        self, etype: str, mission_id: str, worker_id: str | None, payload: Any, ts: int
    ) -> bool:
        mission_key = f"mission:{mission_id}"
        if etype == "MissionDispatched":
            project_id = _opt(getattr(payload, "project_id", None))
            task_id = _opt(getattr(payload, "task_id", None))
            self._mission_projects[mission_id] = (project_id, task_id)
            # The prompt is deliberately NOT read: a mission card shows its
            # identity and correlation, not the user's raw instruction.
            return self._agents.open(
                mission_key,
                ts,
                HudActivity(
                    activity_id=mission_key,
                    kind="mission",
                    label="Mission",
                    mission_id=mission_id,
                    project_id=project_id,
                    task_id=task_id,
                    detail="dispatched",
                    started_at_ns=ts,
                    updated_at_ns=ts,
                ),
            )
        if etype == "MissionStateChanged":
            return self._agents.update(
                mission_key, ts, detail=_safe(getattr(payload, "to_state", ""), LABEL_CHARS)
            )
        if etype == "WorkerSpawned":
            wid = _opt(getattr(payload, "worker_id", None)) or worker_id
            if not wid:
                return False
            key = f"worker:{mission_id}:{wid}"
            project_id, task_id = self._mission_projects.get(mission_id, (None, None))
            self._mission_workers.setdefault(mission_id, set()).add(wid)
            cli = _safe(getattr(payload, "cli", ""), LABEL_CHARS)
            return self._agents.open(
                key,
                ts,
                HudActivity(
                    activity_id=key,
                    kind="worker",
                    label=f"{cli} worker" if cli else "Worker",
                    mission_id=mission_id,
                    worker_id=wid,
                    project_id=project_id,
                    task_id=task_id,
                    detail=_safe(getattr(payload, "model", ""), LABEL_CHARS),
                    started_at_ns=ts,
                    updated_at_ns=ts,
                ),
            )
        if etype == "WorkerProgress":
            wid = _opt(getattr(payload, "worker_id", None)) or worker_id
            if not wid:
                return False
            note = _safe(getattr(payload, "note", ""), LABEL_CHARS)
            stalled = bool(getattr(payload, "stalled", False))
            detail = "stalled" if stalled else note
            return self._agents.update(f"worker:{mission_id}:{wid}", ts, detail=detail)
        if etype in {"WorkerDraftReady", "WorkerKilled"}:
            wid = _opt(getattr(payload, "worker_id", None)) or worker_id
            if not wid:
                return False
            key = f"worker:{mission_id}:{wid}"
            self._mission_workers.get(mission_id, set()).discard(wid)
            if etype == "WorkerDraftReady":
                return self._finish_agent(key, ts, "completed")
            reason = str(getattr(payload, "reason", "") or "")
            failed = reason not in _NON_ERROR_WORKER_KILLS
            changed = self._finish_agent(key, ts, "failed" if failed else "cancelled")
            if changed and failed:
                self._set_scoped_error(
                    HudError(
                        scope="agent",
                        code=_safe(getattr(payload, "error_class", None) or reason, LABEL_CHARS)
                        or "worker_killed",
                        message=_safe(getattr(payload, "error_detail", ""), MESSAGE_CHARS),
                        layer="missions",
                        related_id=key,
                        at_ns=ts,
                    )
                )
            return changed
        terminal = {
            "MissionApproved": "approved",
            "MissionFailed": "failed",
            "MissionCancelled": "cancelled",
            "MissionTimedOut": "timed_out",
        }.get(etype)
        if terminal is not None:
            return self._close_mission(
                mission_id,
                ts,
                status=terminal,
                reason=str(getattr(payload, "reason", "") or ""),
            )
        return False

    # ---------------------------------------------------------- approvals
    def _open_approval(self, approval: HudApproval, ts: int) -> bool:
        orphan_ts = self._approval_orphans.get(approval.approval_id)
        if orphan_ts is not None and orphan_ts >= ts:
            # Its decision was already seen (out-of-order delivery).
            self._approval_orphans.pop(approval.approval_id, None)
            return False
        self._approvals[approval.approval_id] = approval
        while len(self._approvals) > MAX_OPEN_ITEMS:
            self._approvals.popitem(last=False)
        return True

    def _orphan_approval(self, approval_id: str, ts: int) -> None:
        self._approval_orphans[approval_id] = ts
        self._approval_orphans.move_to_end(approval_id)
        while len(self._approval_orphans) > MAX_ORPHANS:
            self._approval_orphans.popitem(last=False)

    def _on_approval_required(self, event: ActionApprovalRequired) -> bool:
        ts = _ts(event)
        trace = _trace(event)
        tool = _safe(event.tool_name, LABEL_CHARS) or "tool"
        mission_id = _opt(event.mission_id)
        ref = str(event.approval_ref or "")
        approval_id = f"tool_call:{mission_id or '-'}:{trace}:{tool}"
        if mission_id:
            channel, read_only = "mission_tool_api", ""
        elif ref.startswith(_CHAT_APPROVAL_REF_PREFIX):
            channel, read_only = "chat_card", _READ_ONLY_CHAT
        else:
            channel, read_only = "none", _READ_ONLY_NO_ROUTE
        approval = HudApproval(
            approval_id=approval_id,
            kind="tool_call",
            action=tool,
            decision_channel=channel,  # type: ignore[arg-type]
            reason=_safe(event.reason, LABEL_CHARS),
            risk_tier=_safe(event.risk_tier, LABEL_CHARS),
            target_preview=_safe(event.args_preview),
            trace_id=trace,
            mission_id=mission_id,
            worker_id=_opt(event.worker_id),
            requested_at_ns=ts,
            expires_at_ns=max(0, int(event.expires_at_ns or 0)),
            read_only_reason=read_only,
        )
        orphan_key = f"tool_call:*:{trace}:{tool}"
        orphan_ts = self._approval_orphans.get(orphan_key)
        if orphan_ts is not None and orphan_ts >= ts:
            # The decision for exactly this call was already seen.
            self._approval_orphans.pop(orphan_key, None)
            return False
        # Index by the identity the terminal events carry.
        self._tool_approval_index[(trace, tool)] = approval_id
        return self._open_approval(approval, ts)

    def _close_tool_approval(
        self, trace: str, tool: str, ts: int, *, remember_orphan: bool
    ) -> bool:
        approval_id = self._tool_approval_index.pop((trace, tool), None)
        if approval_id is None:
            if remember_orphan:
                # Decision seen before the request: remember it under the
                # (trace, tool) identity the late request will check.
                self._orphan_approval(f"tool_call:*:{trace}:{tool}", ts)
            return False
        return self._approvals.pop(approval_id, None) is not None

    @staticmethod
    def _project_approval_id(project_id: str, transaction_id: str, digest: str) -> str:
        return f"project_state:{project_id}:{transaction_id}:{digest}"

    def _on_project_approval_required(
        self, event: ProjectStateApprovalRequired | ProjectStateMemoryProposalCreated
    ) -> bool:
        """One card per exact proposal identity ``(project, transaction, digest)``.

        The governed planner publishes ``ProjectStateApprovalRequired`` from the
        Project State Engine and — for a durable memory-derived proposal (N-14H)
        — ``ProjectStateMemoryProposalCreated`` with the queue row and candidate
        as well. Both describe the SAME proposal, so they share one card; the
        richer event only adds identifiers, it never changes which proposal
        the card names.
        """
        transaction_id = str(event.transaction_id or "").strip()
        project_id = str(event.project_id or "").strip()
        digest = str(event.proposal_digest or "").strip()
        if not transaction_id or not project_id or not digest:
            # No exact identity → no card. A card that cannot name what it
            # approves must not exist (fail closed).
            log.debug("HUD dropped project-state approval without full identity")
            return False
        ts = _ts(event)
        approval_id = self._project_approval_id(project_id, transaction_id, digest)
        files = tuple(_safe(f, LABEL_CHARS) for f in (event.files_affected or ())[:8])
        queue_item_id = _positive_int(getattr(event, "queue_item_id", None))
        candidate_id = _positive_int(getattr(event, "candidate_id", None))
        existing = self._approvals.get(approval_id)
        if existing is not None:
            self._approvals[approval_id] = replace(
                existing,
                queue_item_id=existing.queue_item_id or queue_item_id,
                candidate_id=existing.candidate_id or candidate_id,
                files_affected=existing.files_affected or files,
                target_preview=existing.target_preview or ", ".join(files),
            )
            return self._approvals[approval_id] != existing
        for orphan_key in (approval_id, f"project_state:{project_id}:{transaction_id}"):
            orphan_ts = self._approval_orphans.get(orphan_key)
            if orphan_ts is not None and orphan_ts >= ts:
                # Its decision or transaction outcome was already seen.
                self._approval_orphans.pop(orphan_key, None)
                return False
        return self._open_approval(
            HudApproval(
                approval_id=approval_id,
                kind="project_state",
                action="Project state change",
                decision_channel="none",
                reason="governed_project_state",
                target_preview=", ".join(files),
                trace_id=_trace(event),
                project_id=project_id,
                transaction_id=transaction_id,
                proposal_digest=digest,
                queue_item_id=queue_item_id,
                candidate_id=candidate_id,
                files_affected=files,
                requested_at_ns=ts,
                read_only_reason=_READ_ONLY_PROJECT_STATE,
            ),
            ts,
        )

    def _on_project_memory_decision(
        self, event: ProjectStateMemoryApprovalAccepted | ProjectStateMemoryApprovalRejected
    ) -> bool:
        """Close exactly the decided proposal — never "the pending one".

        The decision names ``(project, transaction, digest)`` and the queue
        row. A card whose recorded queue row disagrees is NOT closed: that
        mismatch means the decision is about a different proposal (fail
        closed), and the card stays until its own outcome arrives.
        """
        project_id = str(event.project_id or "").strip()
        transaction_id = str(event.transaction_id or "").strip()
        digest = str(event.proposal_digest or "").strip()
        if not project_id or not transaction_id or not digest:
            return False
        ts = _ts(event)
        approval_id = self._project_approval_id(project_id, transaction_id, digest)
        card = self._approvals.get(approval_id)
        if card is None:
            self._orphan_approval(approval_id, ts)
            return False
        queue_item_id = _positive_int(event.queue_item_id)
        if card.queue_item_id and queue_item_id and card.queue_item_id != queue_item_id:
            log.warning("HUD ignored a project-state decision whose queue row does not match")
            return False
        self._approvals.pop(approval_id, None)
        return True

    def _on_project_transaction_closed(self, event: Any) -> bool:
        project_id = str(getattr(event, "project_id", "") or "")
        transaction_id = str(getattr(event, "transaction_id", "") or "")
        if not project_id or not transaction_id:
            return False
        ts = _ts(event)
        prefix = f"project_state:{project_id}:{transaction_id}:"
        matched = [aid for aid in self._approvals if aid.startswith(prefix)]
        for aid in matched:
            self._approvals.pop(aid, None)
        if not matched:
            self._orphan_approval(f"project_state:{project_id}:{transaction_id}", ts)
        changed = bool(matched)
        if isinstance(event, (ProjectStateTransactionFailed, ProjectStateRolledBack)):
            message = getattr(event, "error", "")
            changed = (
                self._set_scoped_error(
                    HudError(
                        scope="project",
                        code="project_state_"
                        + (
                            "failed"
                            if isinstance(event, ProjectStateTransactionFailed)
                            else "rolled_back"
                        ),
                        message=_safe(message, MESSAGE_CHARS),
                        layer="projects",
                        trace_id=_trace(event),
                        related_id=project_id,
                        at_ns=ts,
                    )
                )
                or changed
            )
        return changed

    # ------------------------------------------------------------ projects
    def _project(self, project_id: str) -> HudProject:
        project = self._projects.get(project_id)
        if project is None:
            project = HudProject(project_id=project_id)
            self._projects[project_id] = project
            while len(self._projects) > MAX_PROJECTS:
                oldest = next(iter(self._projects))
                if oldest == self._active_project_id:
                    self._projects.move_to_end(oldest)
                    oldest = next(iter(self._projects))
                self._projects.pop(oldest, None)
        return project

    def _write_project(self, project_id: str, ts: int, **changes: Any) -> bool:
        project = self._project(project_id)
        if ts < project.updated_at_ns:
            return False  # an older write must not overwrite a newer one
        self._projects[project_id] = replace(project, updated_at_ns=ts, **changes)
        return True

    def _on_project_resolved(self, event: ProjectContextResolved) -> bool:
        project_id = str(event.project_id or "").strip()
        if not project_id:
            return False
        ts = _ts(event)
        project = self._project(project_id)
        name = _safe(event.project_name, LABEL_CHARS)
        if name and ts >= project.updated_at_ns:
            self._projects[project_id] = replace(project, project_name=name, updated_at_ns=ts)
        if self._active_project_id != project_id:
            self._active_project_id = project_id
            return True
        return bool(name)

    def _on_project_loaded(self, event: ProjectStateLoaded) -> bool:
        project_id = str(event.project_id or "").strip()
        if not project_id:
            return False
        if self._active_project_id is None:
            self._active_project_id = project_id
        return self._write_project(
            project_id,
            _ts(event),
            current_task=_opt(event.current_task, DETAIL_CHARS),
            state_revision=_safe(event.state_revision, LABEL_CHARS),
            state_valid=True,
            issue_codes=(),
        )

    def _on_project_invalid(self, event: ProjectStateInvalid) -> bool:
        project_id = str(event.project_id or "").strip()
        if not project_id:
            return False
        ts = _ts(event)
        codes = tuple(_safe(c, LABEL_CHARS) for c in (event.issue_codes or ())[:8])
        changed = self._write_project(project_id, ts, state_valid=False, issue_codes=codes)
        self._set_scoped_error(
            HudError(
                scope="project",
                code="project_state_invalid",
                message=", ".join(codes),
                layer="projects",
                trace_id=_trace(event),
                related_id=project_id,
                at_ns=ts,
            )
        )
        return changed

    def _on_current_task_changed(self, event: CurrentTaskChanged) -> bool:
        project_id = str(event.project_id or "").strip()
        if not project_id:
            return False
        return self._write_project(
            project_id, _ts(event), current_task=_opt(event.current_task, DETAIL_CHARS)
        )

    def _on_project_committed(self, event: ProjectStateCommitted) -> bool:
        project_id = str(event.project_id or "").strip()
        if not project_id:
            return False
        changed = self._on_project_transaction_closed(event)
        return (
            self._write_project(
                project_id,
                _ts(event),
                current_task=_opt(event.current_task, DETAIL_CHARS),
                state_revision=_safe(event.resulting_state_revision, LABEL_CHARS),
                state_valid=True,
                issue_codes=(),
            )
            or changed
        )

    # ---------------------------------------------------------- computer use
    def _on_cu_started(self, event: CUControlStarted) -> bool:
        mission_id = str(event.mission_id or "") or _trace(event)
        ts = _ts(event)
        orphan = self._cu_orphans.get(mission_id)
        if orphan is not None and orphan >= ts:
            self._cu_orphans.pop(mission_id, None)
            return False
        self._cu_missions[mission_id] = self._cu_missions.get(mission_id, 0) + 1
        return self._refresh_computer(ts, phase="observe")

    def _on_cu_ended(self, event: CUControlEnded) -> bool:
        mission_id = str(event.mission_id or "") or _trace(event)
        ts = _ts(event)
        count = self._cu_missions.get(mission_id, 0)
        if count <= 0:
            self._cu_orphans[mission_id] = ts
            while len(self._cu_orphans) > MAX_ORPHANS:
                self._cu_orphans.popitem(last=False)
            return False
        if count == 1:
            self._cu_missions.pop(mission_id, None)
        else:
            self._cu_missions[mission_id] = count - 1
        if not self._cu_missions:
            return self._refresh_computer(ts, phase="", last_action_kind="")
        return self._refresh_computer(ts)

    def _refresh_computer(self, ts: int, **changes: Any) -> bool:
        self._computer = replace(
            self._computer,
            active=bool(self._cu_missions),
            mission_ids=tuple(_safe(m, LABEL_CHARS) for m in self._cu_missions),
            updated_at_ns=max(self._computer.updated_at_ns, ts),
            **changes,
        )
        return True

    def _on_cu_step(self, event: CUStepProfiled) -> bool:
        if not self._computer.active:
            return False
        ts = _ts(event)
        if ts < self._computer.updated_at_ns:
            return False
        phase = _safe(event.phase, LABEL_CHARS)
        if phase == self._computer.phase:
            return False
        self._computer = replace(self._computer, phase=phase, updated_at_ns=ts)
        return True

    def _on_cu_action_planned(self, event: ActionPlanned) -> bool:
        if not self._computer.active:
            return False
        ts = _ts(event)
        if ts < self._computer.updated_at_ns:
            return False
        # The KIND only. ``target_hint`` can quote on-screen text and is
        # deliberately never read.
        kind = _safe(event.action_kind, LABEL_CHARS)
        self._computer = replace(
            self._computer, last_action_kind=kind, phase="act", updated_at_ns=ts
        )
        return True

    def _on_capture_announced(self, event: ScreenCaptureAnnounced) -> bool:
        ts = _ts(event)
        if ts < self._computer.updated_at_ns:
            return False
        self._computer = replace(
            self._computer,
            screen_capture_active=True,
            capture_target_kind=_safe(event.target_kind, LABEL_CHARS),
            updated_at_ns=ts,
        )
        return True

    def _on_capture_completed(self, event: ScreenCaptureCompleted) -> bool:
        ts = _ts(event)
        if ts < self._computer.updated_at_ns or not self._computer.screen_capture_active:
            return False
        self._computer = replace(self._computer, screen_capture_active=False, updated_at_ns=ts)
        return True

    # --------------------------------------------------------------- memory
    def _remember(self, activity: HudMemoryActivity) -> bool:
        # Two memory events can share a trace; the sequence keeps every row's
        # id unique for the UI without inventing any identity of the domain.
        self._memory_seq += 1
        self._memory.append(
            replace(activity, activity_id=f"{activity.activity_id}#{self._memory_seq}")
        )
        return True

    def _on_memory_updated(self, event: MemoryUpdated) -> bool:
        ts = _ts(event)
        # Namespace + operation only. The key can name the fact; the value is
        # never on the event and never read.
        return self._remember(
            HudMemoryActivity(
                activity_id=f"memory:{_trace(event)}",
                kind="memory_updated",
                status=_safe(event.operation, LABEL_CHARS) or "put",
                subject=_safe(event.namespace, LABEL_CHARS),
                at_ns=ts,
            )
        )

    def _on_profile_updated(self, event: ProfileUpdated) -> bool:
        ts = _ts(event)
        subject = "/".join(
            part for part in (_safe(event.subject, 40), _safe(event.cluster, 40)) if part
        )
        # ``evidence`` (the quoted user text) is deliberately not read.
        return self._remember(
            HudMemoryActivity(
                activity_id=f"profile:{_trace(event)}",
                kind="profile_updated",
                status=_safe(event.operation, LABEL_CHARS) or "set",
                subject=subject,
                at_ns=ts,
            )
        )

    def _on_wiki_changed(self, event: WikiPageChanged) -> bool:
        ts = _ts(event)
        return self._remember(
            HudMemoryActivity(
                activity_id=f"wiki:{_trace(event)}",
                kind="wiki_page",
                status=_safe(event.kind, LABEL_CHARS) or "modified",
                subject=_safe(event.slug, LABEL_CHARS),
                at_ns=ts,
            )
        )

    def _apply_memory_mapper(self, mapper: MemoryMapper, event: Event) -> bool:
        try:
            activity = mapper(event)
        except Exception:  # noqa: BLE001 — a broken extension must not break the HUD
            log.warning("HUD memory mapper for %s failed", type(event).__name__, exc_info=True)
            return False
        if activity is None:
            return False
        try:
            candidate = int(activity.candidate_id) if activity.candidate_id is not None else None
        except (TypeError, ValueError):  # a non-numeric id is dropped, not guessed
            candidate = None
        return self._remember(
            HudMemoryActivity(
                activity_id=_safe(activity.activity_id, LABEL_CHARS) or f"memory:{_trace(event)}",
                kind=_safe(activity.kind, LABEL_CHARS) or type(event).__name__,
                status=_safe(activity.status, LABEL_CHARS),
                subject=_safe(activity.subject, LABEL_CHARS),
                project_id=_opt(activity.project_id),
                candidate_id=candidate,
                at_ns=activity.at_ns or _ts(event),
            )
        )

    # -------------------------------------------------------------- expiry
    def _expire(self, now_ns: int) -> None:
        for approval_id, approval in list(self._approvals.items()):
            if approval.expires_at_ns and approval.expires_at_ns <= now_ns:
                self._approvals.pop(approval_id, None)
                for idx_key, idx_val in list(self._tool_approval_index.items()):
                    if idx_val == approval_id:
                        self._tool_approval_index.pop(idx_key, None)
        for key in self._ops.keys():
            record = self._ops.get(key)
            if record is None:
                continue
            ttl = STALE_AFTER_NS.get(record.kind)
            if ttl is not None and now_ns - record.updated_at_ns > ttl:
                self._ops.force_close(key)


__all__ = [
    "HudReducer",
    "register_memory_mapper",
    "registered_memory_mappers",
    "unregister_memory_mapper",
]
