from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "jarvis/safety/tool_executor.py"

replace_once(
    path,
    '''from collections.abc import Callable
from typing import TYPE_CHECKING, Any
''',
    '''from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
''',
)

replace_once(
    path,
    '''from .approval_surface import (
    CONVERSATIONAL,
    UNATTENDED,
    resolve_approval_surface,
)
from .risk_tier import ActionBlocked, RiskTierEvaluator
''',
    '''from .approval_surface import (
    CONVERSATIONAL,
    UNATTENDED,
    resolve_approval_surface,
)
from .capabilities import (
    CapabilityDenyCode,
    CapabilityGrant,
    CapabilityRequirementsError,
    evaluate_capability,
    requirements_for_tool,
)
from .governance import evaluate_governance
from .risk_tier import ActionBlocked, RiskTierEvaluator
''',
)

replace_once(
    path,
    '''def _optional_string(value: Any) -> str | None:
    """Normalize optional correlation metadata without inventing identifiers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class ToolExecutor:
''',
    '''def _optional_string(value: Any) -> str | None:
    """Normalize optional correlation metadata without inventing identifiers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class _PendingVoiceAction:
    tool: Tool
    args: dict[str, Any]
    project_id: str | None
    task_id: str | None
    project_root: str | None
    capability_grant: CapabilityGrant | None
    delegated: bool


class ToolExecutor:
''',
)

replace_once(
    path,
    '''        self._pending_voice: dict[UUID, tuple[Tool, dict[str, Any]]] = {}
''',
    '''        self._pending_voice: dict[UUID, _PendingVoiceAction] = {}
''',
)

replace_once(
    path,
    '''    async def _approval_unavailable(
''',
    '''    async def _policy_denied(
        self,
        *,
        tid: UUID,
        tool: Tool,
        namespace: str,
        code: str,
        reason: str,
    ) -> ToolResult:
        stable_reason = f"{namespace}:{code}: {reason}"
        await self._bus.publish(ActionDenied(
            trace_id=tid,
            tool_name=tool.name,
            reason=stable_reason,
        ))
        return ToolResult(success=False, output=None, error=stable_reason)

    async def _enforce_governance_and_capability(
        self,
        *,
        tid: UUID,
        tool: Tool,
        args: dict[str, Any],
        project_id: str | None,
        task_id: str | None,
        project_root: str | None,
        capability_grant: CapabilityGrant | None,
        delegated: bool,
    ) -> ToolResult | None:
        try:
            requirements = requirements_for_tool(
                tool,
                args,
                project_id=project_id,
                task_id=task_id,
            )
        except CapabilityRequirementsError as exc:
            return await self._policy_denied(
                tid=tid,
                tool=tool,
                namespace="capability",
                code=CapabilityDenyCode.MALFORMED_REQUIREMENTS.value,
                reason=str(exc),
            )

        governance = evaluate_governance(requirements, project_root=project_root)
        if not governance.allowed:
            return await self._policy_denied(
                tid=tid,
                tool=tool,
                namespace="governance",
                code=governance.code.value if governance.code is not None else "denied",
                reason=governance.reason,
            )

        if delegated:
            capability = evaluate_capability(capability_grant, requirements)
            if not capability.allowed:
                return await self._policy_denied(
                    tid=tid,
                    tool=tool,
                    namespace="capability",
                    code=capability.code.value if capability.code is not None else "invalid_grant",
                    reason=capability.reason,
                )
        return None

    async def _approval_unavailable(
''',
)
