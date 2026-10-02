from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.dispatcher import BrainDispatcher
from jarvis.brain.manager import BrainManager, _PROJECT_EXECUTION_SCOPE
from jarvis.brain.project_context import (
    ProjectContextResolution,
    ProjectContextResolutionStatus,
    ProjectExecutionScope,
)
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _ReadTool:
    name = "wiki-list"
    schema: dict[str, Any] = {}


class _ToolBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "project-call",
                    "name": "wiki-list",
                    "input": {},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="done")
        yield BrainDelta(finish_reason="stop")


class _Executor:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    async def execute(
        self,
        _tool: Any,
        _args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        self.kwargs.append(dict(kwargs))
        return ToolResult(success=True, output="ok")


@pytest.mark.asyncio
async def test_tool_use_loop_forwards_project_execution_scope() -> None:
    brain = _ToolBrain()
    executor = _Executor()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ReadTool()},
        executor,  # type: ignore[arg-type]
        project_id="alpha",
        task_id="N-13 — Project-Scoped Delegation Bridge",
        project_root="/projects/alpha",
    )

    result = await loop.run([], user_utterance="list project notes")

    assert result.text == "done"
    assert len(executor.kwargs) == 1
    call = executor.kwargs[0]
    assert call["project_id"] == "alpha"
    assert call["task_id"] == "N-13 — Project-Scoped Delegation Bridge"
    assert call["project_root"] == "/projects/alpha"


def test_dispatcher_preserves_project_scope_when_brain_is_swapped() -> None:
    first = _ToolBrain()
    second = _ToolBrain()
    dispatcher = BrainDispatcher(
        first,
        tools={"wiki-list": _ReadTool()},
        executor=_Executor(),  # type: ignore[arg-type]
        project_id="alpha",
        task_id="N-13",
        project_root="/projects/alpha",
    )

    swapped = dispatcher.with_brain(second)

    assert swapped.brain is second
    assert swapped._project_id == "alpha"
    assert swapped._task_id == "N-13"
    assert swapped._project_root == "/projects/alpha"


@pytest.mark.asyncio
async def test_generate_resets_project_execution_scope_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BrainManager.__new__(BrainManager)

    async def fake_generate(*_args: Any, **_kwargs: Any) -> str:
        _PROJECT_EXECUTION_SCOPE.set(
            ProjectExecutionScope(
                project_id="alpha",
                task_id="N-13",
                project_root="/projects/alpha",
            )
        )
        return "ok"

    monkeypatch.setattr(manager, "_generate", fake_generate)
    before = _PROJECT_EXECUTION_SCOPE.get()

    assert await manager.generate("project turn") == "ok"
    assert _PROJECT_EXECUTION_SCOPE.get() is before


class _ResolutionOnlyTurnContext:
    def __init__(self, resolution: ProjectContextResolution) -> None:
        self._resolution = resolution

    def resolve_turn(self, *_args: Any, **_kwargs: Any) -> ProjectContextResolution:
        return self._resolution


def test_ambiguous_project_resolution_blocks_delegation() -> None:
    manager = BrainManager.__new__(BrainManager)
    manager._project_turn_context = _ResolutionOnlyTurnContext(
        ProjectContextResolution(status=ProjectContextResolutionStatus.AMBIGUOUS)
    )

    block, scope, blocked = manager._resolve_project_turn_context(
        user_text="compare alpha and beta"
    )

    assert scope is None
    assert blocked is True
    assert "[PROJECT CONTEXT — AMBIGUOUS]" in block


def test_no_project_resolution_does_not_block_non_project_delegation() -> None:
    manager = BrainManager.__new__(BrainManager)
    manager._project_turn_context = _ResolutionOnlyTurnContext(
        ProjectContextResolution(status=ProjectContextResolutionStatus.NO_PROJECT)
    )

    block, scope, blocked = manager._resolve_project_turn_context(
        user_text="research this unrelated topic"
    )

    assert block == ""
    assert scope is None
    assert blocked is False
