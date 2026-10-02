"""Deterministic EventBus -> HudSnapshot reducer."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import Event
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

_MAX_ITEMS = 32
_FOREGROUND_STATES = {
    "IDLE",
    "LISTENING",
    "THINKING",
    "WORKING",
    "SPEAKING",
    "ERROR",
}


class HudStateAdapter:
    """Stateful semantic reducer; never performs operational actions."""

    def __init__(self, bus: EventBus | None = None, *, clock_ns=time.time_ns) -> None:
        self._bus = bus
        self._clock_ns = clock_ns
        self._started = False
        self._foreground: PrimaryHudState = "IDLE"
        self._connection: HudConnectionState = "CONNECTED"
        self._project: HudProject | None = None
        self._operations: dict[str, HudActivity] = {}
        self._approvals: dict[str, HudApproval] = {}
        self._agents: dict[str, HudActivity] = {}
        self._memory: dict[str, HudMemoryActivity] = {}
        self._computer = HudComputerActivity()
        self._last_error: HudError | None = None
        self._last_timestamp_ns = 0

    def start(self) -> None:
        if self._started or self._bus is None:
            return
        self._bus.subscribe_all(self.handle)
        self._started = True

    def close(self) -> None:
        if not self._started or self._bus is None:
            return
        self._bus.unsubscribe_all(self.handle)
        self._started = False

    def set_connection_state(self, state: HudConnectionState) -> HudSnapshot:
        self._connection = state
        self._touch()
        return self.snapshot()

    async def handle(self, event: Event) -> None:
        """Reduce one event. Stale events never roll semantic state backward."""

        timestamp_ns = max(0, int(getattr(event, "timestamp_ns", 0) or 0))
        if timestamp_ns and timestamp_ns < self._last_timestamp_ns:
            return
        self._last_timestamp_ns = max(self._last_timestamp_ns, timestamp_ns)
        name = type(event).__name__
        trace = str(getattr(event, "trace_id", "") or "")

        if name == "SystemStateChanged":
            state = str(getattr(event, "new_state", "") or "").upper()
            if state in _FOREGROUND_STATES:
                self._foreground = state  # type: ignore[assignment]
        elif name == "ListeningStarted":
            self._foreground = "LISTENING"
        elif name == "BrainTurnStarted":
            self._foreground = "THINKING"
        elif name == "BrainTurnCompleted" and self._foreground == "THINKING":
            self._foreground = "IDLE"
        elif name == "ResponseGenerated" and self._foreground == "THINKING":
            self._foreground = "SPEAKING"
        elif name == "SpeechSpoken":
            self._foreground = "SPEAKING"

        if name in {"ActionProposed", "ToolCallStarted"}:
            tool = str(getattr(event, "tool_name", "") or "tool")
            key = f"tool:{trace}"
            self._operations[key] = HudActivity(
                activity_id=key,
                kind="tool",
                label=tool,
                status="running",
                trace_id=trace,
                detail=_safe(getattr(event, "args_preview", "")),
            )
        elif name in {"ActionExecuted", "ToolCallCompleted"}:
            self._close_operation(f"tool:{trace}")
            if getattr(event, "success", True) is False:
                self._last_error = HudError(
                    scope="operation",
                    code=name,
                    message=_safe(getattr(event, "error", "")),
                    trace_id=trace,
                    recoverable=True,
                )

        if name == "TaskStarted":
            task_id = str(getattr(event, "task_id", "") or trace)
            key = f"task:{task_id}"
            self._operations[key] = HudActivity(
                activity_id=key,
                kind="task",
                label="Scheduled task",
                status="running",
                trace_id=trace,
                task_id=task_id,
            )
        elif name in {"TaskCompleted", "TaskFailed", "TaskInterrupted"}:
            task_id = str(getattr(event, "task_id", "") or trace)
            self._close_operation(f"task:{task_id}")

        if name == "JarvisAgentTaskStarted":
            key = f"agent:{trace}"
            self._agents[key] = HudActivity(
                activity_id=key,
                kind="agent",
                label="Jarvis agent",
                status="running",
                trace_id=trace,
                detail=_safe(getattr(event, "provider", "")),
            )
        elif name == "JarvisAgentTaskCompleted":
            self._agents.pop(f"agent:{trace}", None)

        if name == "ActionApprovalRequired":
            key = f"tool:{trace}"
            self._approvals[key] = HudApproval(
                approval_id=key,
                kind="tool",
                action=str(getattr(event, "tool_name", "") or "tool"),
                reason=_safe(getattr(event, "reason", "approval required")),
                trace_id=trace,
                mission_id=getattr(event, "mission_id", None),
                expires_at_ns=int(
                    getattr(event, "expires_at_ns", 0) or 0
                ),
                scope_preview=_safe(getattr(event, "args_preview", "")),
            )
        elif name in {"ActionApproved", "ActionDenied", "ActionExecuted"}:
            self._approvals.pop(f"tool:{trace}", None)

        if name in {
            "ProjectStateApprovalRequired",
            "ProjectStateMemoryProposalCreated",
        }:
            transaction_id = str(
                getattr(event, "transaction_id", "") or ""
            )
            digest = str(getattr(event, "proposal_digest", "") or "")
            key = f"project-state:{transaction_id}:{digest}"
            self._approvals[key] = HudApproval(
                approval_id=key,
                kind="project-state",
                action="Project state change",
                reason="governed project-state approval",
                trace_id=trace,
                project_id=_optional(getattr(event, "project_id", None)),
                transaction_id=transaction_id,
                proposal_digest=digest,
                queue_item_id=_optional_int(
                    getattr(event, "queue_item_id", None)
                ),
                candidate_id=_optional_int(
                    getattr(event, "candidate_id", None)
                ),
            )
        elif name in {
            "ProjectStateMemoryApprovalAccepted",
            "ProjectStateMemoryApprovalRejected",
        }:
            self._remove_project_approval(
                str(getattr(event, "transaction_id", "") or ""),
                str(getattr(event, "proposal_digest", "") or ""),
            )

        if name == "MemoryPromotionProposed":
            candidate_id = int(getattr(event, "candidate_id", 0) or 0)
            digest = str(getattr(event, "proposal_digest", "") or "")
            key = f"memory:{candidate_id}:{digest}"
            self._approvals[key] = HudApproval(
                approval_id=key,
                kind="memory",
                action="Persistent memory promotion",
                reason=str(
                    getattr(event, "governance_class", "")
                    or "governed memory"
                ),
                trace_id=trace,
                candidate_id=candidate_id or None,
                proposal_digest=digest,
            )
            self._remember_memory(
                key,
                "promotion-proposed",
                "waiting",
                candidate_id=candidate_id or None,
            )
        elif name in {"MemoryPromotionApproved", "MemoryPromotionRejected"}:
            candidate_id = int(getattr(event, "candidate_id", 0) or 0)
            digest = str(getattr(event, "proposal_digest", "") or "")
            self._approvals.pop(f"memory:{candidate_id}:{digest}", None)
            self._remember_memory(
                f"memory:{candidate_id}:{digest}",
                name,
                "completed" if name.endswith("Approved") else "rejected",
                candidate_id=candidate_id or None,
            )
        elif name in {
            "MemoryCandidateCreated",
            "PersistentMemoryChanged",
            "MemoryConflictDetected",
            "MemoryDeleted",
            "TemporaryMemoryExpired",
            "TemporaryMemoryStored",
        }:
            candidate_id = _optional_int(getattr(event, "candidate_id", None))
            item_id = _optional_int(getattr(event, "item_id", None))
            memory_id = candidate_id or item_id or 0
            key = f"memory-event:{name}:{memory_id}:{trace}"
            self._remember_memory(
                key,
                name,
                "completed",
                candidate_id=candidate_id,
                project_id=_optional(getattr(event, "project_id", None)),
            )

        if name == "ProjectContextResolved":
            self._project = HudProject(
                project_id=str(getattr(event, "project_id", "") or ""),
                project_name=str(getattr(event, "project_name", "") or ""),
            )
        elif name == "ProjectStateLoaded":
            project_id = str(getattr(event, "project_id", "") or "")
            existing = self._project
            self._project = HudProject(
                project_id=project_id,
                project_name=(
                    existing.project_name
                    if existing is not None
                    and existing.project_id == project_id
                    else ""
                ),
                current_task=_optional(
                    getattr(event, "current_task", None)
                ),
                state_revision=str(
                    getattr(event, "state_revision", "") or ""
                ),
            )
        elif name == "CurrentTaskChanged":
            project_id = str(getattr(event, "project_id", "") or "")
            existing = self._project
            self._project = HudProject(
                project_id=project_id,
                project_name=(
                    existing.project_name
                    if existing is not None
                    and existing.project_id == project_id
                    else ""
                ),
                current_task=_optional(
                    getattr(event, "current_task", None)
                ),
                state_revision=(
                    existing.state_revision
                    if existing is not None
                    and existing.project_id == project_id
                    else ""
                ),
            )
        elif name == "ProjectStateCommitted" and self._project is not None:
            if self._project.project_id == getattr(event, "project_id", ""):
                self._project = replace(
                    self._project,
                    current_task=_optional(
                        getattr(event, "current_task", None)
                    ),
                    state_revision=str(
                        getattr(event, "resulting_state_revision", "") or ""
                    ),
                )

        if name == "CUControlStarted":
            self._computer = HudComputerActivity(
                active=True,
                phase="INTERACT",
                mission_id=_optional(getattr(event, "mission_id", None)),
            )
        elif name == "CUControlEnded":
            self._computer = HudComputerActivity(
                active=False,
                phase="OBSERVE",
                mission_id=_optional(getattr(event, "mission_id", None)),
                action=_safe(getattr(event, "reason", "")),
            )

        if name == "ErrorOccurred":
            recoverable = bool(getattr(event, "recoverable", True))
            self._last_error = HudError(
                scope="operation" if recoverable else "system",
                code=str(getattr(event, "error_type", "") or "error"),
                message=_safe(getattr(event, "message", "")),
                trace_id=trace,
                recoverable=recoverable,
            )
            if not recoverable:
                self._foreground = "ERROR"

        self._trim()
        self._touch(timestamp_ns)

    def snapshot(self) -> HudSnapshot:
        """Return the full current snapshot for reconnect/resync."""

        primary = self._effective_primary_state()
        return HudSnapshot(
            primary_state=primary,
            connection_state=self._connection,
            active_project=self._project,
            active_operations=tuple(self._operations.values()),
            approval_requests=tuple(self._approvals.values()),
            agent_activity=tuple(self._agents.values()),
            memory_activity=tuple(self._memory.values()),
            computer_activity=self._computer,
            last_error=self._last_error,
            updated_at_ns=self._last_timestamp_ns,
        )

    def _effective_primary_state(self) -> PrimaryHudState:
        if (
            self._foreground == "ERROR"
            and self._last_error is not None
            and self._last_error.scope == "system"
        ):
            return "ERROR"
        if self._approvals:
            return "WAITING_FOR_APPROVAL"
        if self._foreground != "IDLE":
            return self._foreground
        if self._operations or self._agents or self._computer.active:
            return "WORKING"
        return "IDLE"

    def _close_operation(self, key: str) -> None:
        self._operations.pop(key, None)

    def _remove_project_approval(
        self,
        transaction_id: str,
        digest: str,
    ) -> None:
        self._approvals.pop(
            f"project-state:{transaction_id}:{digest}",
            None,
        )

    def _remember_memory(
        self,
        key: str,
        kind: str,
        status: str,
        *,
        candidate_id: int | None = None,
        project_id: str | None = None,
    ) -> None:
        self._memory[key] = HudMemoryActivity(
            activity_id=key,
            kind=kind,
            status=status,
            candidate_id=candidate_id,
            project_id=project_id,
        )

    def _trim(self) -> None:
        for mapping in (
            self._operations,
            self._agents,
            self._memory,
        ):
            while len(mapping) > _MAX_ITEMS:
                mapping.pop(next(iter(mapping)))

    def _touch(self, timestamp_ns: int = 0) -> None:
        now = int(timestamp_ns or self._clock_ns())
        self._last_timestamp_ns = max(self._last_timestamp_ns, now)


def _safe(value: Any) -> str:
    return safe_preview(str(value or ""), max_chars=240).strip()


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = ["HudStateAdapter"]
