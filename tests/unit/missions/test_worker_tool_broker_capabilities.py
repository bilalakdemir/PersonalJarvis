from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core import runtime_refs
from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
from jarvis.missions.workers.worker_tool_broker import WorkerToolBroker


class _Gateway:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def catalog(self):
        return (
            SupervisorToolDescriptor(
                name="echo",
                description="echo",
                input_schema={"type": "object", "properties": {}},
                risk_tier="safe",
            ),
        )

    async def execute(self, name, arguments, request):
        self.requests.append(request)
        return ToolResult(True, {"name": name})


@pytest.mark.asyncio
async def test_worker_binding_issues_and_forwards_immutable_capability_grant(monkeypatch) -> None:
    runtime_refs._reset_for_tests()
    gateway = _Gateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)

    broker = WorkerToolBroker()
    monkeypatch.setattr(
        broker,
        "_ensure_server",
        lambda: SimpleNamespace(server_address=("127.0.0.1", 12345)),
    )

    binding = broker.issue(
        task_text="echo once",
        mcp_server_ids=(),
        app_commands=(),
        native_tool_names=("echo",),
        mission_id="mission-1",
        worker_id="worker-1",
        ttl_s=30,
    )
    assert binding is not None
    try:
        result = await binding.execute("echo", {})
        assert result["success"] is True
        assert len(gateway.requests) == 1
        request = gateway.requests[0]
        assert request.delegated is True
        assert request.capability_grant is not None
        assert request.capability_grant.tools == frozenset({"echo"})
        assert request.capability_grant.grant_id
    finally:
        binding.close()
        runtime_refs._reset_for_tests()
