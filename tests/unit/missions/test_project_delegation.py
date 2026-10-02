from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain.project_context import (
    ProjectContextResolution,
    ProjectContextResolutionStatus,
    project_execution_scope,
)
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.protocols import (
    ExecutionContext,
    SupervisorToolDescriptor,
    ToolResult,
)
from jarvis.missions.manager import MissionManager
from jarvis.missions.workers.worker_tool_broker import WorkerToolBroker
from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool
from jarvis.projects import ProjectContextSnapshot


class _Gateway:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        return (
            SupervisorToolDescriptor(
                name="echo",
                description="echo",
                input_schema={"type": "object", "properties": {}},
                risk_tier="safe",
            ),
        )

    async def execute(self, name: str, arguments: dict[str, Any], request: Any) -> ToolResult:
        self.requests.append(request)
        return ToolResult(success=True, output={"name": name, "arguments": arguments})


class _RecordingManager:
    def __init__(self) -> None:
        self.dispatches: list[dict[str, Any]] = []

    async def dispatch(self, **kwargs: Any) -> str:
        self.dispatches.append(dict(kwargs))
        return "mission-1"


class _Kontrollierer:
    async def run_mission(self, _mission_id: str) -> None:
        return None


def _snapshot(root: Path) -> ProjectContextSnapshot:
    return ProjectContextSnapshot(
        project_id="alpha",
        project_name="Alpha",
        root_path=root,
        main_goal="Build Alpha",
        phase="Core Implementation",
        current_task="N-13 — Project-Scoped Delegation Bridge",
        last_completed="N-12",
        next_step="Finish N-13",
        blockers="None",
        active_decisions=(),
        relevant_backlog_items=(),
        state_revision="abc123",
    )


def test_execution_scope_is_derived_only_from_valid_canonical_resolution(
    tmp_path: Path,
) -> None:
    resolved = ProjectContextResolution(
        status=ProjectContextResolutionStatus.RESOLVED,
        snapshot=_snapshot(tmp_path),
        project_id="alpha",
    )

    scope = project_execution_scope(resolved)

    assert scope is not None
    assert scope.project_id == "alpha"
    assert scope.task_id == "N-13 — Project-Scoped Delegation Bridge"
    assert scope.project_root == str(tmp_path)
    assert project_execution_scope(
        ProjectContextResolution(status=ProjectContextResolutionStatus.AMBIGUOUS)
    ) is None
    assert project_execution_scope(
        ProjectContextResolution(status=ProjectContextResolutionStatus.UNAVAILABLE)
    ) is None


@pytest.mark.asyncio
async def test_mission_project_scope_survives_manager_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    project_root = str((tmp_path / "alpha").resolve())

    first = MissionManager(db_path)
    await first.start()
    mission_id = await first.dispatch(
        prompt="Do project work",
        language="en",
        project_id="alpha",
        task_id="N-13 — Project-Scoped Delegation Bridge",
        project_root=project_root,
    )
    scope = await first.project_scope(mission_id)
    assert scope is not None
    assert scope.project_id == "alpha"
    assert scope.task_id == "N-13 — Project-Scoped Delegation Bridge"
    assert scope.project_root == project_root
    await first.stop()

    second = MissionManager(db_path)
    await second.start()
    restored = await second.project_scope(mission_id)
    assert restored == scope
    await second.stop()


@pytest.mark.asyncio
async def test_worker_broker_binds_project_scope_into_grant_and_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_refs._reset_for_tests()
    gateway = _Gateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)
    broker = WorkerToolBroker()
    monkeypatch.setattr(
        broker,
        "_ensure_server",
        lambda: SimpleNamespace(server_address=("127.0.0.1", 12345)),
    )
    project_root = str(tmp_path.resolve())

    binding = broker.issue(
        task_text="echo once",
        mcp_server_ids=(),
        app_commands=(),
        native_tool_names=("echo",),
        mission_id="mission-1",
        worker_id="worker-1",
        project_id="alpha",
        task_id="N-13 — Project-Scoped Delegation Bridge",
        project_root=project_root,
        ttl_s=30,
    )
    assert binding is not None
    try:
        result = await binding.execute("echo", {})
        assert result["success"] is True
        request = gateway.requests[0]
        assert request.project_id == "alpha"
        assert request.task_id == "N-13 — Project-Scoped Delegation Bridge"
        assert request.project_root == project_root
        grant = request.capability_grant
        assert grant is not None
        assert grant.project_ids == frozenset({"alpha"})
        assert grant.task_ids == frozenset({"N-13 — Project-Scoped Delegation Bridge"})
        assert grant.read_roots == (project_root,)
        assert grant.write_roots == (project_root,)
    finally:
        binding.close()
        runtime_refs._reset_for_tests()


def test_worker_broker_rejects_incomplete_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_refs._reset_for_tests()
    gateway = _Gateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)
    broker = WorkerToolBroker()
    monkeypatch.setattr(
        broker,
        "_ensure_server",
        lambda: SimpleNamespace(server_address=("127.0.0.1", 12345)),
    )

    assert broker.issue(
        task_text="echo once",
        mcp_server_ids=(),
        app_commands=(),
        native_tool_names=("echo",),
        project_id="alpha",
        project_root=None,
        ttl_s=30,
    ) is None
    assert broker.issue(
        task_text="echo once",
        mcp_server_ids=(),
        app_commands=(),
        native_tool_names=("echo",),
        task_id="N-13",
        project_root=str(tmp_path.resolve()),
        ttl_s=30,
    ) is None
    runtime_refs._reset_for_tests()


@pytest.mark.asyncio
async def test_spawn_worker_carries_execution_context_into_mission_dispatch(
    tmp_path: Path,
) -> None:
    manager = _RecordingManager()
    tool = SpawnWorkerTool(
        bus=EventBus(),
        manager=manager,
        kontrollierer=_Kontrollierer(),
    )
    project_root = str(tmp_path.resolve())
    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="delegate this project work",
        config={"output_language": "en"},
        memory_read=None,
        project_id="alpha",
        task_id="N-13 — Project-Scoped Delegation Bridge",
        project_root=project_root,
    )

    result = await tool.execute(
        {
            "utterance": "delegate this project work",
            "action": "finish the bridge",
            "target": "",
            "language": "en",
        },
        ctx,
    )
    assert result.success is True
    await asyncio.sleep(0)

    assert len(manager.dispatches) == 1
    dispatched = manager.dispatches[0]
    assert dispatched["project_id"] == "alpha"
    assert dispatched["task_id"] == "N-13 — Project-Scoped Delegation Bridge"
    assert dispatched["project_root"] == project_root
