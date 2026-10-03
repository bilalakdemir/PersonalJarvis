from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionExecuted,
    ActionProposed,
    BrainTurnStarted,
    CUControlEnded,
    CUControlStarted,
    ErrorOccurred,
    ListeningStarted,
    SpeechSpoken,
)
from jarvis.core.memory_events import (
    MemoryPromotionApproved,
    MemoryPromotionProposed,
)
from jarvis.core.project_state_events import (
    CurrentTaskChanged,
    ProjectContextResolved,
    ProjectStateLoaded,
    ProjectStateMemoryApprovalAccepted,
    ProjectStateMemoryProposalCreated,
)
from jarvis.ui.hud import HudStateAdapter


@pytest.mark.asyncio
async def test_foreground_transitions_and_background_work_coexist() -> None:
    adapter = HudStateAdapter(clock_ns=lambda: 100)
    trace = uuid4()

    await adapter.handle(ListeningStarted(trace_id=trace, timestamp_ns=1))
    assert adapter.snapshot().primary_state == "LISTENING"

    await adapter.handle(BrainTurnStarted(trace_id=trace, timestamp_ns=2))
    assert adapter.snapshot().primary_state == "THINKING"

    await adapter.handle(
        ActionProposed(
            trace_id=trace,
            timestamp_ns=3,
            tool_name="search",
        )
    )
    assert len(adapter.snapshot().active_operations) == 1

    await adapter.handle(SpeechSpoken(trace_id=trace, timestamp_ns=4))
    snap = adapter.snapshot()
    assert snap.primary_state == "SPEAKING"
    assert len(snap.active_operations) == 1

    await adapter.handle(
        ActionExecuted(
            trace_id=trace,
            timestamp_ns=5,
            tool_name="search",
            success=True,
        )
    )
    assert adapter.snapshot().active_operations == ()


@pytest.mark.asyncio
async def test_tool_approval_is_exactly_correlated() -> None:
    adapter = HudStateAdapter()
    waiting = uuid4()
    unrelated = uuid4()

    await adapter.handle(
        ActionApprovalRequired(
            trace_id=waiting,
            timestamp_ns=10,
            tool_name="write",
            reason="risk_tier",
            args_preview="{redacted}",
        )
    )
    assert adapter.snapshot().primary_state == "WAITING_FOR_APPROVAL"
    assert len(adapter.snapshot().approval_requests) == 1

    await adapter.handle(
        ActionApproved(
            trace_id=unrelated,
            timestamp_ns=11,
            tool_name="write",
            approved_by="user",
        )
    )
    assert len(adapter.snapshot().approval_requests) == 1

    await adapter.handle(
        ActionApproved(
            trace_id=waiting,
            timestamp_ns=12,
            tool_name="write",
            approved_by="user",
        )
    )
    assert adapter.snapshot().approval_requests == ()


@pytest.mark.asyncio
async def test_project_memory_approval_keeps_exact_transaction_identity() -> None:
    adapter = HudStateAdapter()
    trace = uuid4()

    await adapter.handle(
        ProjectStateMemoryProposalCreated(
            trace_id=trace,
            timestamp_ns=20,
            project_id="jarvis",
            queue_item_id=7,
            candidate_id=11,
            transaction_id="tx-1",
            proposal_digest="digest-1",
            source_state_revision="revision",
            files_affected=("STATE.md", "TASKS.md"),
        )
    )
    request = adapter.snapshot().approval_requests[0]
    assert request.kind == "project-state"
    assert request.queue_item_id == 7
    assert request.candidate_id == 11
    assert request.transaction_id == "tx-1"
    assert request.proposal_digest == "digest-1"

    await adapter.handle(
        ProjectStateMemoryApprovalAccepted(
            trace_id=trace,
            timestamp_ns=21,
            project_id="jarvis",
            queue_item_id=7,
            transaction_id="tx-1",
            proposal_digest="digest-1",
        )
    )
    assert adapter.snapshot().approval_requests == ()


@pytest.mark.asyncio
async def test_memory_approval_and_project_snapshot_are_independent() -> None:
    adapter = HudStateAdapter()
    trace = uuid4()

    await adapter.handle(
        ProjectContextResolved(
            trace_id=trace,
            timestamp_ns=30,
            project_id="jarvis",
            project_name="Personal Jarvis",
        )
    )
    await adapter.handle(
        ProjectStateLoaded(
            trace_id=trace,
            timestamp_ns=31,
            project_id="jarvis",
            state_revision="rev-1",
            current_task="N-15",
        )
    )
    await adapter.handle(
        MemoryPromotionProposed(
            trace_id=trace,
            timestamp_ns=32,
            candidate_id=5,
            proposal_digest="memory-digest",
            governance_class="operating-rule",
        )
    )
    snap = adapter.snapshot()
    assert snap.active_project is not None
    assert snap.active_project.current_task == "N-15"
    assert snap.primary_state == "WAITING_FOR_APPROVAL"

    await adapter.handle(
        MemoryPromotionApproved(
            trace_id=trace,
            timestamp_ns=33,
            candidate_id=5,
            proposal_digest="memory-digest",
        )
    )
    await adapter.handle(
        CurrentTaskChanged(
            trace_id=trace,
            timestamp_ns=34,
            project_id="jarvis",
            transaction_id="tx",
            previous_task="N-15",
            current_task="N-16",
        )
    )
    snap = adapter.snapshot()
    assert snap.active_project is not None
    assert snap.active_project.current_task == "N-16"
    assert snap.approval_requests == ()


@pytest.mark.asyncio
async def test_computer_use_is_background_activity() -> None:
    adapter = HudStateAdapter()
    trace = uuid4()

    await adapter.handle(
        CUControlStarted(
            trace_id=trace,
            timestamp_ns=40,
            mission_id="mission-1",
        )
    )
    snap = adapter.snapshot()
    assert snap.computer_activity.active is True
    assert snap.primary_state == "WORKING"

    await adapter.handle(
        CUControlEnded(
            trace_id=trace,
            timestamp_ns=41,
            mission_id="mission-1",
            reason="finished",
        )
    )
    assert adapter.snapshot().computer_activity.active is False


@pytest.mark.asyncio
async def test_nonrecoverable_error_has_priority_over_approval() -> None:
    adapter = HudStateAdapter()
    trace = uuid4()

    await adapter.handle(
        ActionApprovalRequired(
            trace_id=trace,
            timestamp_ns=50,
            tool_name="write",
        )
    )
    await adapter.handle(
        ErrorOccurred(
            trace_id=trace,
            timestamp_ns=51,
            layer="core",
            error_type="fatal",
            message="core unavailable",
            recoverable=False,
        )
    )
    snap = adapter.snapshot()
    assert snap.primary_state == "ERROR"
    assert snap.last_error is not None
    assert snap.last_error.scope == "system"


@pytest.mark.asyncio
async def test_stale_event_does_not_roll_state_backward() -> None:
    adapter = HudStateAdapter()
    trace = uuid4()

    await adapter.handle(BrainTurnStarted(trace_id=trace, timestamp_ns=100))
    await adapter.handle(ListeningStarted(trace_id=trace, timestamp_ns=99))
    assert adapter.snapshot().primary_state == "THINKING"


def test_connection_state_is_separate_from_operational_state() -> None:
    adapter = HudStateAdapter(clock_ns=lambda: 100)
    snap = adapter.set_connection_state("RECONNECTING")
    assert snap.connection_state == "RECONNECTING"
    assert snap.primary_state == "IDLE"
