from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import ExecutionContext, SupervisorToolRequest, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.capabilities import CapabilityGrant
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL, ToolExecutor


class _SafeTool:
    name = "safe_tool"
    risk_tier = "safe"
    schema: dict[str, Any] = {}
    description = "safe"

    def __init__(self) -> None:
        self.calls = 0
        self.last_ctx: ExecutionContext | None = None

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls += 1
        self.last_ctx = ctx
        return ToolResult(True, "ok")


class _AskTool(_SafeTool):
    name = "ask_tool"
    risk_tier = "ask"


class _CanonicalWriteTool(_AskTool):
    name = "generic_write"

    def capability_requirements_for_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"write_paths": [str(args["path"])]}

    def intent_confirms_args(self, args: dict[str, Any], utterance: str) -> bool:
        return True


def _executor() -> ToolExecutor:
    bus = EventBus()
    return ToolExecutor(
        bus=bus,
        evaluator=RiskTierEvaluator(SafetyConfig()),
        approval=ApprovalWorkflow(bus),
    )


def _grant(tool_name: str, *, seconds: float = 60.0) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-1",
        tools=frozenset({tool_name}),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=seconds),
    )


@pytest.mark.asyncio
async def test_direct_call_remains_backward_compatible_without_grant() -> None:
    tool = _SafeTool()
    result = await _executor().execute(tool, {})
    assert result.success is True
    assert tool.calls == 1
    assert tool.last_ctx is not None
    assert tool.last_ctx.delegated is False
    assert tool.last_ctx.capability_grant is None


@pytest.mark.asyncio
async def test_delegated_call_without_grant_fails_closed() -> None:
    tool = _SafeTool()
    result = await _executor().execute(tool, {}, delegated=True)
    assert result.success is False
    assert (result.error or "").startswith("capability:missing_grant")
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_valid_delegated_call_reaches_existing_risk_pipeline() -> None:
    tool = _SafeTool()
    grant = _grant(tool.name)
    result = await _executor().execute(
        tool,
        {},
        capability_grant=grant,
        delegated=True,
    )
    assert result.success is True
    assert tool.calls == 1
    assert tool.last_ctx is not None
    assert tool.last_ctx.capability_grant is grant
    assert tool.last_ctx.delegated is True


@pytest.mark.asyncio
async def test_governance_precedes_explicit_intent() -> None:
    tool = _CanonicalWriteTool()
    result = await _executor().execute(
        tool,
        {"path": "/projects/jarvis/STATE.md"},
        user_utterance="Yes, change the state file.",
        project_root="/projects/jarvis",
        config_snapshot={"voice_confirm": True},
    )
    assert result.success is False
    assert (result.error or "").startswith("governance:canonical_state_mutation")
    assert result.error != VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_voice_confirmation_rechecks_original_grant_expiry() -> None:
    tool = _AskTool()
    grant = _grant(tool.name, seconds=0.02)
    executor = _executor()
    trace_id = uuid4()

    first = await executor.execute(
        tool,
        {},
        trace_id=trace_id,
        config_snapshot={"voice_confirm": True},
        capability_grant=grant,
        delegated=True,
    )
    assert first.error == VOICE_CONFIRM_SENTINEL
    await asyncio.sleep(0.04)

    confirmed = await executor.execute_confirmed(trace_id)
    assert confirmed.success is False
    assert (confirmed.error or "").startswith("capability:expired")
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_voice_confirmation_preserves_security_context() -> None:
    tool = _AskTool()
    grant = _grant(tool.name)
    executor = _executor()
    trace_id = uuid4()

    first = await executor.execute(
        tool,
        {},
        trace_id=trace_id,
        config_snapshot={"voice_confirm": True},
        project_id="project-1",
        task_id="task-1",
        project_root="/projects/jarvis",
        capability_grant=CapabilityGrant(
            grant_id=grant.grant_id,
            tools=grant.tools,
            expires_at=grant.expires_at,
            project_ids=frozenset({"project-1"}),
            task_ids=frozenset({"task-1"}),
        ),
        delegated=True,
    )
    assert first.error == VOICE_CONFIRM_SENTINEL

    confirmed = await executor.execute_confirmed(trace_id)
    assert confirmed.success is True
    assert tool.last_ctx is not None
    assert tool.last_ctx.project_id == "project-1"
    assert tool.last_ctx.task_id == "task-1"
    assert tool.last_ctx.project_root == "/projects/jarvis"
    assert tool.last_ctx.delegated is True
    assert tool.last_ctx.capability_grant is not None
    assert tool.last_ctx.capability_grant.grant_id == "grant-1"


@pytest.mark.asyncio
async def test_gateway_forwards_security_context_to_executor() -> None:
    tool = _SafeTool()
    grant = _grant(tool.name)
    seen: dict[str, Any] = {}

    class CaptureExecutor:
        async def execute(self, passed_tool, args, **kwargs):
            seen.update(kwargs)
            return ToolResult(True, "ok")

    class Manager:
        _tools = {tool.name: tool}
        _tool_executor = CaptureExecutor()

    gateway = BrainSupervisorToolGateway(Manager())
    request = SupervisorToolRequest(
        trace_id=uuid4(),
        origin="mission_worker",
        user_utterance="do it",
        project_id="project-1",
        task_id="task-1",
        project_root="/projects/jarvis",
        capability_grant=grant,
        delegated=True,
    )
    result = await gateway.execute(tool.name, {}, request)
    assert result.success is True
    assert seen["project_id"] == "project-1"
    assert seen["task_id"] == "task-1"
    assert seen["project_root"] == "/projects/jarvis"
    assert seen["capability_grant"] is grant
    assert seen["delegated"] is True
