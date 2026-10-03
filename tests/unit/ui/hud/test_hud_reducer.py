"""Semantic contract of the canonical HUD reducer (N-15).

Every test feeds real ``jarvis.core.events`` dataclasses with explicit
timestamps and checks the resulting ``HudSnapshot`` — no mocks, no bus.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

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
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
    MemoryUpdated,
    MissionCompleted,
    ProfileUpdated,
    ScreenCaptureAnnounced,
    ScreenCaptureCompleted,
    SystemStateChanged,
    TaskFailed,
    TaskScheduled,
    TaskStarted,
    VoiceSessionStarted,
)
from jarvis.core.memory_events import (
    MemoryCandidateCreated,
    MemoryConflictDetected,
    MemoryDeleted,
    MemoryPreExpiryReviewRequired,
    MemoryPromotionApproved,
    MemoryPromotionProposed,
    MemoryPromotionRejected,
    PersistentMemoryChanged,
    TemporaryMemoryExpired,
    TemporaryMemoryStored,
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
    ProjectStateTransactionStarted,
)
from jarvis.ui.hud.models import HudMemoryActivity
from jarvis.ui.hud.reducer import (
    SCOPED_ERROR_TTL_NS,
    HudReducer,
    register_memory_mapper,
    unregister_memory_mapper,
)

T0 = 1_800_000_000_000_000_000  # a fixed wall-clock origin (ns)
SEC = 1_000_000_000


def at(seconds: float) -> int:
    return T0 + int(seconds * SEC)


def snap(reducer: HudReducer, now_s: float = 10.0):
    return reducer.snapshot(now_ns=at(now_s))


def state(new: str, ts: float, prev: str = "IDLE") -> SystemStateChanged:
    return SystemStateChanged(new_state=new, previous=prev, timestamp_ns=at(ts))


# --------------------------------------------------------------------------- primary


@pytest.mark.parametrize(
    ("supervisor", "primary"),
    [
        ("IDLE", "IDLE"),
        ("LISTENING", "LISTENING"),
        ("CONNECTING", "LISTENING"),
        ("WAITING_FOR_COMPLETION", "LISTENING"),
        ("THINKING", "THINKING"),
        ("SPEAKING", "SPEAKING"),
        ("ERROR", "ERROR"),
        ("PAUSED", "IDLE"),
    ],
)
def test_foreground_supervisor_states_map_to_primary(supervisor: str, primary: str) -> None:
    r = HudReducer()
    r.apply(state(supervisor, 1))
    s = snap(r)
    assert s.primary_state == primary
    assert s.voice_state == supervisor


def test_idle_by_default() -> None:
    s = snap(HudReducer())
    assert s.primary_state == "IDLE"
    assert s.attention == ()
    assert s.active_operations == ()
    assert s.connection_state == "CONNECTED"


def test_unknown_supervisor_state_is_ignored() -> None:
    r = HudReducer()
    r.apply(state("LISTENING", 1))
    assert r.apply(state("BANANA", 2)) is False
    assert snap(r).primary_state == "LISTENING"


def test_working_when_background_operation_and_no_foreground() -> None:
    r = HudReducer()
    r.apply(ActionProposed(tool_name="read_file", args={"path": "a.txt"}, timestamp_ns=at(1)))
    s = snap(r)
    assert s.primary_state == "WORKING"
    assert s.attention == ("working",)
    assert [o.label for o in s.active_operations] == ["read_file"]


def test_waiting_for_approval_beats_working() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionProposed(trace_id=trace, tool_name="run_shell", timestamp_ns=at(1)))
    r.apply(
        ActionApprovalRequired(
            trace_id=trace, tool_name="run_shell", mission_id="m-1", timestamp_ns=at(1.1)
        )
    )
    s = snap(r)
    assert s.primary_state == "WAITING_FOR_APPROVAL"
    assert s.attention == ("approval", "working")


def test_foreground_speaking_coexists_with_background_work_and_approval() -> None:
    """Jarvis keeps the floor; the background stays visible as attention."""
    r = HudReducer()
    r.apply(state("SPEAKING", 1, "THINKING"))
    r.apply(CUControlStarted(mission_id="cu-1", timestamp_ns=at(2)))
    r.apply(ActionApprovalRequired(tool_name="delete_file", mission_id="m-9", timestamp_ns=at(3)))
    r.apply(MemoryUpdated(namespace="facts", key="k", timestamp_ns=at(4)))
    s = snap(r)
    assert s.primary_state == "SPEAKING"
    assert "approval" in s.attention and "working" in s.attention
    assert s.computer_activity.active is True
    assert len(s.approval_requests) == 1
    assert len(s.memory_activity) == 1


def test_working_does_not_override_listening_or_thinking() -> None:
    r = HudReducer()
    r.apply(TaskStarted(task_id="t1", timestamp_ns=at(1)))
    r.apply(state("LISTENING", 2))
    assert snap(r).primary_state == "LISTENING"
    r.apply(state("THINKING", 3, "LISTENING"))
    assert snap(r).primary_state == "THINKING"
    r.apply(state("IDLE", 4, "THINKING"))
    assert snap(r).primary_state == "WORKING"


def test_multiple_simultaneous_operations_and_exact_closure() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionProposed(trace_id=trace, tool_name="read_file", timestamp_ns=at(1)))
    r.apply(ActionProposed(trace_id=trace, tool_name="web_search", timestamp_ns=at(1.1)))
    r.apply(TaskStarted(task_id="t-1", timestamp_ns=at(1.2)))
    assert len(snap(r).active_operations) == 3

    # Same trace, different tool: closes ONLY read_file.
    r.apply(ActionExecuted(trace_id=trace, tool_name="read_file", success=True, timestamp_ns=at(2)))
    labels = sorted(o.label for o in snap(r).active_operations)
    assert labels == ["Scheduled task", "web_search"]

    # A completion for an unrelated trace closes nothing.
    r.apply(ActionExecuted(tool_name="web_search", success=True, timestamp_ns=at(2.5)))
    assert len(snap(r).active_operations) == 2


def test_two_concurrent_calls_with_same_identity_are_refcounted() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionProposed(trace_id=trace, tool_name="grep", timestamp_ns=at(1)))
    r.apply(ActionProposed(trace_id=trace, tool_name="grep", timestamp_ns=at(1.1)))
    r.apply(ActionExecuted(trace_id=trace, tool_name="grep", success=True, timestamp_ns=at(2)))
    assert len(snap(r).active_operations) == 1, "one completion must not close both calls"
    r.apply(ActionExecuted(trace_id=trace, tool_name="grep", success=True, timestamp_ns=at(3)))
    assert snap(r).active_operations == ()
    assert snap(r).primary_state == "IDLE"


def test_task_title_comes_from_schedule_and_task_closes_by_id() -> None:
    r = HudReducer()
    r.apply(TaskScheduled(task_id="t-7", title="Nightly backup", timestamp_ns=at(0)))
    r.apply(TaskStarted(task_id="t-7", timestamp_ns=at(1)))
    assert snap(r).active_operations[0].label == "Nightly backup"
    r.apply(TaskFailed(task_id="t-7", error="disk full", timestamp_ns=at(2)))
    s = snap(r, 3)
    assert s.active_operations == ()
    assert s.last_error is not None
    assert s.last_error.scope == "operation"
    assert s.last_error.related_id == "task:t-7"
    assert s.primary_state == "IDLE", "a scoped error never takes the primary state"
    assert s.attention == ("error",)


# ------------------------------------------------------------------------------ errors


def test_scoped_error_vs_global_error() -> None:
    r = HudReducer()
    r.apply(state("LISTENING", 1))
    # A UI socket failing is component-scoped even when non-recoverable.
    r.apply(
        ErrorOccurred(
            layer="ui.web.ws",
            error_type="RuntimeError",
            message="x",
            recoverable=False,
            timestamp_ns=at(2),
        )
    )
    s = snap(r)
    assert s.primary_state == "LISTENING"
    assert s.last_error is not None and s.last_error.scope == "component"

    # A core, non-recoverable failure is global and outranks the foreground.
    r.apply(
        ErrorOccurred(
            layer="brain",
            error_type="ProviderDown",
            message="all providers failed",
            recoverable=False,
            timestamp_ns=at(3),
        )
    )
    s = snap(r)
    assert s.primary_state == "ERROR"
    assert s.last_error is not None and s.last_error.scope == "global"


def test_global_error_cleared_by_newer_healthy_state_but_not_older() -> None:
    r = HudReducer()
    r.apply(
        ErrorOccurred(layer="realtime.x", error_type="Boom", recoverable=False, timestamp_ns=at(5))
    )
    assert snap(r).primary_state == "ERROR"
    # An OLDER state edge delivered late cannot clear it.
    r.apply(state("LISTENING", 4))
    assert snap(r).primary_state == "ERROR"
    # A newer sign of life does.
    r.apply(VoiceSessionStarted(session_id="s", timestamp_ns=at(6)))
    assert snap(r).primary_state == "LISTENING"


def test_supervisor_error_state_is_global_until_next_state() -> None:
    r = HudReducer()
    r.apply(ActionProposed(tool_name="x", timestamp_ns=at(0.5)))
    r.apply(state("ERROR", 1, "THINKING"))
    s = snap(r)
    assert s.primary_state == "ERROR"
    assert s.last_error is not None and s.last_error.code == "voice_runtime_error"
    r.apply(state("IDLE", 2, "ERROR"))
    s = snap(r)
    assert s.primary_state == "WORKING"
    assert s.last_error is None


def test_recoverable_error_correlates_to_open_operation_by_trace() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionProposed(trace_id=trace, tool_name="fetch", timestamp_ns=at(1)))
    r.apply(ErrorOccurred(trace_id=trace, layer="tools", error_type="Timeout", timestamp_ns=at(2)))
    err = snap(r).last_error
    assert err is not None
    assert err.scope == "operation"
    assert err.related_id == f"tool:{trace}:fetch"


def test_scoped_error_expires_from_attention() -> None:
    r = HudReducer()
    r.apply(ErrorOccurred(layer="tools", error_type="E", timestamp_ns=at(1)))
    assert snap(r, 2).attention == ("error",)
    later = 1 + SCOPED_ERROR_TTL_NS / SEC + 1
    assert snap(r, later).last_error is None
    assert snap(r, later).attention == ()


# ---------------------------------------------------------------------- stale / order


def test_stale_supervisor_edge_does_not_roll_back() -> None:
    r = HudReducer()
    r.apply(state("SPEAKING", 3, "THINKING"))
    r.apply(state("LISTENING", 2))  # older edge arriving late
    assert snap(r).primary_state == "SPEAKING"


def test_close_before_open_does_not_resurrect_operation() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionExecuted(trace_id=trace, tool_name="ls", success=True, timestamp_ns=at(2)))
    r.apply(ActionProposed(trace_id=trace, tool_name="ls", timestamp_ns=at(1)))
    assert snap(r).active_operations == ()
    assert snap(r).primary_state == "IDLE"


def test_genuinely_new_open_after_old_orphan_close_opens() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionExecuted(trace_id=trace, tool_name="ls", success=True, timestamp_ns=at(1)))
    r.apply(ActionProposed(trace_id=trace, tool_name="ls", timestamp_ns=at(5)))
    assert len(snap(r).active_operations) == 1


def test_stale_current_task_update_ignored() -> None:
    r = HudReducer()
    r.apply(ProjectContextResolved(project_id="p1", project_name="Atlas", timestamp_ns=at(1)))
    r.apply(CurrentTaskChanged(project_id="p1", current_task="T-2 ship", timestamp_ns=at(3)))
    r.apply(CurrentTaskChanged(project_id="p1", current_task="T-1 old", timestamp_ns=at(2)))
    project = snap(r).active_project
    assert project is not None
    assert project.current_task == "T-2 ship"
    assert project.project_name == "Atlas"


# ----------------------------------------------------------------------------- project


def test_project_current_update_and_commit() -> None:
    r = HudReducer()
    r.apply(ProjectContextResolved(project_id="p1", project_name="Atlas", timestamp_ns=at(1)))
    r.apply(
        ProjectStateLoaded(
            project_id="p1", state_revision="r1", current_task="T-1", timestamp_ns=at(2)
        )
    )
    r.apply(
        ProjectStateCommitted(
            project_id="p1",
            transaction_id="tx",
            resulting_state_revision="r2",
            current_task="T-2",
            timestamp_ns=at(3),
        )
    )
    project = snap(r).active_project
    assert project is not None
    assert (project.current_task, project.state_revision, project.state_valid) == (
        "T-2",
        "r2",
        True,
    )

    r.apply(
        ProjectStateInvalid(project_id="p1", issue_codes=("missing_current",), timestamp_ns=at(4))
    )
    s = snap(r)
    assert s.active_project is not None and s.active_project.state_valid is False
    assert s.last_error is not None and s.last_error.scope == "project"
    assert s.primary_state == "IDLE"


def test_current_task_for_other_project_does_not_hijack_active_project() -> None:
    r = HudReducer()
    r.apply(ProjectContextResolved(project_id="p1", project_name="Atlas", timestamp_ns=at(1)))
    r.apply(CurrentTaskChanged(project_id="p2", current_task="other", timestamp_ns=at(2)))
    project = snap(r).active_project
    assert project is not None and project.project_id == "p1"
    assert project.current_task is None


# --------------------------------------------------------------------------- approvals


def test_exact_tool_approval_correlation() -> None:
    r = HudReducer()
    trace_a, trace_b = uuid4(), uuid4()
    r.apply(
        ActionApprovalRequired(
            trace_id=trace_a, tool_name="rm", mission_id="m1", timestamp_ns=at(1)
        )
    )
    r.apply(
        ActionApprovalRequired(
            trace_id=trace_b, tool_name="rm", mission_id="m2", timestamp_ns=at(1.1)
        )
    )
    r.apply(
        ActionApprovalRequired(
            trace_id=trace_a, tool_name="mv", mission_id="m1", timestamp_ns=at(1.2)
        )
    )
    assert len(snap(r).approval_requests) == 3

    # Deciding (trace_a, rm) clears exactly that card.
    r.apply(
        ActionApproved(trace_id=trace_a, tool_name="rm", approved_by="user", timestamp_ns=at(2))
    )
    remaining = {(a.trace_id, a.action) for a in snap(r).approval_requests}
    assert remaining == {(str(trace_b), "rm"), (str(trace_a), "mv")}

    # A decision for an unknown identity clears nothing.
    r.apply(ActionDenied(trace_id=uuid4(), tool_name="rm", timestamp_ns=at(3)))
    assert len(snap(r).approval_requests) == 2


def test_tool_approval_identity_and_decision_channel() -> None:
    r = HudReducer()
    trace = UUID("11111111-2222-4333-8444-555555555555")
    r.apply(
        ActionApprovalRequired(
            trace_id=trace,
            tool_name="run_shell",
            mission_id="m/1",
            worker_id="w1",
            risk_tier="ask",
            reason="risk_tier",
            args_preview="rm -rf build",
            expires_at_ns=at(60),
            timestamp_ns=at(1),
        )
    )
    r.apply(
        ActionApprovalRequired(tool_name="write", approval_ref="agent-chat:s1", timestamp_ns=at(1))
    )
    r.apply(ActionApprovalRequired(tool_name="cron", timestamp_ns=at(1)))
    by_action = {a.action: a for a in snap(r).approval_requests}
    mission = by_action["run_shell"]
    assert mission.approval_id == f"tool_call:m/1:{trace}:run_shell"
    assert (mission.mission_id, mission.trace_id, mission.worker_id) == ("m/1", str(trace), "w1")
    assert mission.decision_channel == "mission_tool_api"
    assert mission.read_only_reason == ""
    assert by_action["write"].decision_channel == "chat_card"
    assert by_action["cron"].decision_channel == "none"
    assert by_action["cron"].read_only_reason


def test_expired_approval_disappears() -> None:
    r = HudReducer()
    r.apply(
        ActionApprovalRequired(
            tool_name="x", mission_id="m", expires_at_ns=at(10), timestamp_ns=at(1)
        )
    )
    assert snap(r, 5).primary_state == "WAITING_FOR_APPROVAL"
    assert snap(r, 11).approval_requests == ()
    assert snap(r, 11).primary_state == "IDLE"


def test_decision_before_request_does_not_create_card() -> None:
    r = HudReducer()
    trace = uuid4()
    r.apply(ActionDenied(trace_id=trace, tool_name="rm", reason="user_denied", timestamp_ns=at(2)))
    r.apply(
        ActionApprovalRequired(trace_id=trace, tool_name="rm", mission_id="m", timestamp_ns=at(1))
    )
    assert snap(r).approval_requests == ()


def test_project_state_approval_preserves_transaction_and_digest() -> None:
    r = HudReducer()
    r.apply(
        ProjectStateApprovalRequired(
            project_id="p1",
            transaction_id="tx-1",
            proposal_digest="d-1",
            files_affected=("STATE.md",),
            timestamp_ns=at(1),
        )
    )
    r.apply(
        ProjectStateApprovalRequired(
            project_id="p1",
            transaction_id="tx-2",
            proposal_digest="d-2",
            files_affected=("TASKS.md",),
            timestamp_ns=at(1.5),
        )
    )
    cards = {a.transaction_id: a for a in snap(r).approval_requests}
    assert cards["tx-1"].approval_id == "project_state:p1:tx-1:d-1"
    assert cards["tx-1"].proposal_digest == "d-1"
    assert cards["tx-1"].decision_channel == "none", "no safe route on develop → read-only"
    assert cards["tx-1"].files_affected == ("STATE.md",)

    r.apply(
        ProjectStateTransactionStarted(project_id="p1", transaction_id="tx-1", timestamp_ns=at(2))
    )
    assert [a.transaction_id for a in snap(r).approval_requests] == ["tx-2"]


def test_project_state_approval_without_identity_fails_closed() -> None:
    r = HudReducer()
    r.apply(ProjectStateApprovalRequired(project_id="p1", transaction_id="tx", proposal_digest=""))
    assert snap(r).approval_requests == ()


# ----------------------------------------------------------------------- computer use


def test_computer_use_activity_refcounted_and_metadata_only() -> None:
    r = HudReducer()
    r.apply(CUControlStarted(mission_id="a", timestamp_ns=at(1)))
    r.apply(CUControlStarted(mission_id="b", timestamp_ns=at(1.1)))
    r.apply(CUStepProfiled(phase="think", timestamp_ns=at(1.2)))
    r.apply(
        ActionPlanned(
            action_kind="click", target_hint="{name:Password: hunter2}", timestamp_ns=at(1.3)
        )
    )
    s = snap(r)
    assert s.primary_state == "WORKING"
    assert s.computer_activity.active
    assert s.computer_activity.last_action_kind == "click"
    assert "hunter2" not in repr(s.to_dict()), "CU target text must never reach the HUD"

    r.apply(CUControlEnded(mission_id="a", timestamp_ns=at(2)))
    assert snap(r).computer_activity.active, "mission b still controls the computer"
    r.apply(CUControlEnded(mission_id="b", timestamp_ns=at(3)))
    s = snap(r)
    assert not s.computer_activity.active
    assert s.primary_state == "IDLE"


def test_screen_capture_indicator() -> None:
    r = HudReducer()
    r.apply(ScreenCaptureAnnounced(target_kind="window", target_label="Window", timestamp_ns=at(1)))
    assert snap(r).computer_activity.screen_capture_active
    assert snap(r).computer_activity.capture_target_kind == "window"
    r.apply(ScreenCaptureCompleted(timestamp_ns=at(2)))
    assert not snap(r).computer_activity.screen_capture_active


# ------------------------------------------------------------------ agents / missions


def test_jarvis_agent_lifecycle_shows_completion_and_error() -> None:
    r = HudReducer()
    ok, bad = uuid4(), uuid4()
    r.apply(
        JarvisAgentTaskStarted(
            trace_id=ok, provider="codex", utterance="build it", timestamp_ns=at(1)
        )
    )
    r.apply(JarvisAgentTaskStarted(trace_id=bad, provider="claude", timestamp_ns=at(1.1)))
    assert snap(r).primary_state == "WORKING"
    r.apply(JarvisAgentTaskCompleted(trace_id=ok, success=True, timestamp_ns=at(2)))
    r.apply(
        JarvisAgentTaskCompleted(trace_id=bad, success=False, error="quota", timestamp_ns=at(3))
    )
    s = snap(r)
    statuses = {a.activity_id: a.status for a in s.agent_activity}
    assert statuses == {f"agent:{ok}": "completed", f"agent:{bad}": "failed"}
    assert s.primary_state == "IDLE"
    assert s.last_error is not None and s.last_error.scope == "agent"


def _envelope(etype: str, mission_id: str, ts_s: float, worker_id: str | None = None, **payload):
    return SimpleNamespace(
        mission_id=mission_id,
        worker_id=worker_id,
        ts_ms=at(ts_s) // 1_000_000,
        payload=SimpleNamespace(event_type=etype, **payload),
    )


def test_mission_and_worker_activity_from_mission_bus() -> None:
    r = HudReducer()
    r.apply_mission_envelope(
        _envelope(
            "MissionDispatched", "m1", 1, prompt="secret plan", project_id="p1", task_id="T-4"
        )
    )
    r.apply_mission_envelope(
        _envelope("WorkerSpawned", "m1", 2, worker_id="w1", cli="codex", model="gpt-x")
    )
    s = snap(r)
    assert s.primary_state == "WORKING"
    kinds = {a.kind: a for a in s.agent_activity}
    assert kinds["mission"].project_id == "p1" and kinds["mission"].task_id == "T-4"
    assert kinds["worker"].label == "codex worker"
    assert kinds["worker"].project_id == "p1"
    assert "secret plan" not in repr(s.to_dict()), "mission prompts never reach the HUD"

    r.apply(MissionCompleted(mission_id="m1", status="approved", timestamp_ns=at(5)))
    s = snap(r)
    assert all(a.status != "running" for a in s.agent_activity)
    assert s.primary_state == "IDLE"


def test_worker_killed_by_failure_is_agent_error_but_user_kill_is_not() -> None:
    r = HudReducer()
    r.apply_mission_envelope(_envelope("MissionDispatched", "m1", 1, prompt="p"))
    r.apply_mission_envelope(
        _envelope("WorkerSpawned", "m1", 2, worker_id="w1", cli="claude", model="x")
    )
    r.apply_mission_envelope(
        _envelope("WorkerSpawned", "m1", 2.1, worker_id="w2", cli="codex", model="y")
    )
    r.apply_mission_envelope(_envelope("WorkerKilled", "m1", 3, worker_id="w1", reason="user"))
    assert snap(r).last_error is None
    r.apply_mission_envelope(
        _envelope(
            "WorkerKilled", "m1", 4, worker_id="w2", reason="timeout", error_class="worker_timeout"
        )
    )
    err = snap(r).last_error
    assert err is not None and err.scope == "agent" and err.code == "worker_timeout"


# ------------------------------------------------------------------------------ memory


def test_memory_activity_is_metadata_only() -> None:
    r = HudReducer()
    r.apply(
        MemoryUpdated(
            namespace="facts", key="favourite_colour", operation="put", timestamp_ns=at(1)
        )
    )
    r.apply(
        ProfileUpdated(
            subject="user",
            cluster="identity",
            field="name",
            evidence="my password is hunter22",
            timestamp_ns=at(2),
        )
    )
    s = snap(r)
    assert [m.kind for m in s.memory_activity] == ["profile_updated", "memory_updated"]
    assert "hunter22" not in repr(s.to_dict())
    assert s.primary_state == "IDLE", "memory maintenance stays quiet"


def test_future_memory_event_extension_point() -> None:
    class MemoryPromotionProposed:  # stand-in for an event that does not exist on develop
        trace_id = uuid4()
        timestamp_ns = at(1)

    def mapper(event):
        return HudMemoryActivity(
            activity_id="promo:1",
            kind="promotion_proposed",
            status="waiting",
            subject="token " + "sk-" + "Z9" * 15,
            candidate_id=7,
        )

    register_memory_mapper("MemoryPromotionProposed", mapper)
    try:
        r = HudReducer()
        assert r.apply(MemoryPromotionProposed()) is True  # type: ignore[arg-type]
        item = snap(r).memory_activity[0]
        assert item.kind == "promotion_proposed" and item.candidate_id == 7
        assert "Z9Z9Z9" not in item.subject, "mapper output is re-redacted"
    finally:
        unregister_memory_mapper("MemoryPromotionProposed")


def test_broken_memory_mapper_fails_closed() -> None:
    class Weird:
        trace_id = uuid4()
        timestamp_ns = at(1)

    def boom(_event):
        raise RuntimeError("bad mapper")

    register_memory_mapper("Weird", boom)
    try:
        r = HudReducer()
        assert r.apply(Weird()) is False  # type: ignore[arg-type]
        assert snap(r).memory_activity == ()
    finally:
        unregister_memory_mapper("Weird")


def test_governed_memory_lifecycle_projects_metadata_and_exact_approval() -> None:
    r = HudReducer()
    expires_ms = at(120) // 1_000_000
    r.apply(
        TemporaryMemoryStored(
            item_id=4,
            kind="research",
            project_id="p1",
            expires_ms=expires_ms,
            timestamp_ns=at(1),
        )
    )
    r.apply(
        MemoryPreExpiryReviewRequired(
            item_id=4,
            kind="research",
            project_id="p1",
            expires_ms=expires_ms,
            timestamp_ns=at(2),
        )
    )
    r.apply(
        MemoryCandidateCreated(
            candidate_id=7,
            kind="decision",
            basis="explicit",
            timestamp_ns=at(3),
        )
    )
    r.apply(
        MemoryPromotionProposed(
            candidate_id=7,
            proposal_digest="digest-7",
            governance_class="user-wide-decision",
            expires_ms=expires_ms,
            timestamp_ns=at(4),
        )
    )
    r.apply(
        MemoryPromotionProposed(
            candidate_id=8,
            proposal_digest="digest-8",
            governance_class="operating-rule",
            expires_ms=expires_ms,
            timestamp_ns=at(4.1),
        )
    )

    before = snap(r, 5)
    assert before.primary_state == "WAITING_FOR_APPROVAL"
    assert {card.approval_id for card in before.approval_requests} == {
        "memory_promotion:7:digest-7",
        "memory_promotion:8:digest-8",
    }
    card = next(c for c in before.approval_requests if c.candidate_id == 7)
    assert card.kind == "memory_promotion"
    assert card.decision_channel == "none"
    assert card.proposal_digest == "digest-7"

    r.apply(
        MemoryPromotionApproved(
            candidate_id=7,
            proposal_digest="digest-7",
            timestamp_ns=at(6),
        )
    )
    after = snap(r, 7)
    assert [c.candidate_id for c in after.approval_requests] == [8]
    assert any(m.kind == "promotion_approved" and m.candidate_id == 7 for m in after.memory_activity)


def test_governed_memory_reject_before_proposal_prevents_late_card() -> None:
    r = HudReducer()
    r.apply(
        MemoryPromotionRejected(
            candidate_id=7,
            proposal_digest="digest-7",
            reason="user-rejected",
            timestamp_ns=at(2),
        )
    )
    r.apply(
        MemoryPromotionProposed(
            candidate_id=7,
            proposal_digest="digest-7",
            governance_class="operating-rule",
            expires_ms=at(100) // 1_000_000,
            timestamp_ns=at(1),
        )
    )
    assert snap(r, 3).approval_requests == ()


def test_governed_memory_terminal_events_never_expose_bodies() -> None:
    r = HudReducer()
    r.apply(
        PersistentMemoryChanged(
            candidate_id=7,
            change_kind="update",
            target_ref="decisions/project",
            timestamp_ns=at(1),
        )
    )
    r.apply(
        MemoryConflictDetected(
            candidate_id=7,
            conflict_ref="decisions/project",
            timestamp_ns=at(2),
        )
    )
    r.apply(
        TemporaryMemoryExpired(
            item_id=4,
            kind="research",
            project_id="p1",
            timestamp_ns=at(3),
        )
    )
    r.apply(
        MemoryDeleted(
            memory_kind="candidate",
            memory_id="7",
            reason="retention-expired",
            timestamp_ns=at(4),
        )
    )
    wire = repr(snap(r, 5).to_dict())
    assert "decisions/project" in wire
    assert "candidate:7" in wire
    assert "content" not in wire.lower()


# ------------------------------------------------------------------------------ privacy


def test_redaction_of_tool_arguments_and_errors() -> None:
    r = HudReducer()
    # Built at runtime so no credential-shaped literal sits in the repo.
    fake_key = "sk-" + "proj-" + "Q7" * 15
    r.apply(ActionProposed(tool_name="http", args={"headers": {"Authorization": fake_key}}))
    r.apply(
        ActionApprovalRequired(tool_name="http", mission_id="m", args_preview=f"key={fake_key}")
    )
    r.apply(ErrorOccurred(layer="tools", error_type="E", message=f"api_key={fake_key} rejected"))
    wire = repr(snap(r, 0).to_dict())
    assert fake_key not in wire
    assert "<redacted:" in wire


def test_long_values_are_capped() -> None:
    r = HudReducer()
    r.apply(ActionProposed(tool_name="write", args={"content": "x" * 10_000}, timestamp_ns=at(1)))
    detail = snap(r).active_operations[0].detail
    assert len(detail) < 400


def test_snapshot_wire_shape_is_json_safe() -> None:
    import json

    r = HudReducer()
    r.apply(ProjectContextResolved(project_id="p1", project_name="Atlas", timestamp_ns=at(1)))
    r.apply(ActionApprovalRequired(tool_name="x", mission_id="m", timestamp_ns=at(1)))
    payload = r.snapshot(now_ns=at(2), epoch="e", revision=3).to_dict()
    json.dumps(payload)
    assert payload["revision"] == 3 and payload["epoch"] == "e"
    assert isinstance(payload["approval_requests"], list)


# ------------------------------------------------- N-14H durable project-state proposals


def _created(tx: str, digest: str, queue: int, ts: float, project: str = "p1"):
    return ProjectStateMemoryProposalCreated(
        project_id=project,
        queue_item_id=queue,
        candidate_id=queue * 10,
        transaction_id=tx,
        proposal_digest=digest,
        files_affected=("STATE.md",),
        timestamp_ns=at(ts),
    )


def test_durable_proposal_and_engine_request_share_one_exact_card() -> None:
    r = HudReducer()
    r.apply(
        ProjectStateApprovalRequired(
            project_id="p1",
            transaction_id="tx-1",
            proposal_digest="d-1",
            files_affected=("STATE.md",),
            timestamp_ns=at(1),
        )
    )
    r.apply(_created("tx-1", "d-1", 7, 1.1))
    cards = snap(r).approval_requests
    assert len(cards) == 1
    card = cards[0]
    assert card.approval_id == "project_state:p1:tx-1:d-1"
    assert (card.queue_item_id, card.candidate_id) == (7, 70)
    assert card.decision_channel == "none"


def test_durable_decision_closes_exactly_its_proposal() -> None:
    r = HudReducer()
    r.apply(_created("tx-1", "d-1", 7, 1))
    r.apply(_created("tx-2", "d-2", 8, 1.1))
    # Same transaction, different digest: a different proposal — nothing closes.
    r.apply(
        ProjectStateMemoryApprovalRejected(
            project_id="p1",
            queue_item_id=7,
            transaction_id="tx-1",
            proposal_digest="d-OTHER",
            timestamp_ns=at(2),
        )
    )
    assert len(snap(r).approval_requests) == 2
    # Mismatched queue row for the same identity: fail closed, card stays.
    r.apply(
        ProjectStateMemoryApprovalAccepted(
            project_id="p1",
            queue_item_id=99,
            transaction_id="tx-1",
            proposal_digest="d-1",
            timestamp_ns=at(2.5),
        )
    )
    assert len(snap(r).approval_requests) == 2
    r.apply(
        ProjectStateMemoryApprovalAccepted(
            project_id="p1",
            queue_item_id=7,
            transaction_id="tx-1",
            proposal_digest="d-1",
            timestamp_ns=at(3),
        )
    )
    assert [a.transaction_id for a in snap(r).approval_requests] == ["tx-2"]


def test_durable_decision_before_creation_creates_no_card() -> None:
    r = HudReducer()
    r.apply(
        ProjectStateMemoryApprovalRejected(
            project_id="p1",
            queue_item_id=7,
            transaction_id="tx-1",
            proposal_digest="d-1",
            timestamp_ns=at(2),
        )
    )
    r.apply(_created("tx-1", "d-1", 7, 1))
    assert snap(r).approval_requests == ()
