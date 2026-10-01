from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.safety.capabilities import (
    CapabilityDenyCode,
    CapabilityGrant,
    CapabilityRequirements,
    CapabilityRequirementsError,
    evaluate_capability,
    path_is_within_root,
    requirements_for_tool,
    validate_child_grant,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _grant(**overrides):
    values = dict(
        grant_id="g1",
        tools=frozenset({"tool-a", "tool-b"}),
        expires_at=NOW + timedelta(minutes=10),
        project_ids=frozenset({"project-1"}),
        task_ids=frozenset({"task-1"}),
        read_roots=("/workspace/project",),
        write_roots=("/workspace/project/out",),
        allow_network=True,
        external_services=frozenset({"github"}),
        credentials=frozenset({"github-token"}),
    )
    values.update(overrides)
    return CapabilityGrant(**values)


def test_valid_grant_allows_exact_scope() -> None:
    decision = evaluate_capability(
        _grant(),
        CapabilityRequirements(
            tool_name="tool-a",
            project_id="project-1",
            task_id="task-1",
            read_paths=("/workspace/project/input.txt",),
            write_paths=("/workspace/project/out/result.txt",),
            network=True,
            external_services=frozenset({"github"}),
            credentials=frozenset({"github-token"}),
        ),
        now=NOW,
    )
    assert decision.allowed is True


def test_missing_grant_fails_closed() -> None:
    decision = evaluate_capability(None, CapabilityRequirements(tool_name="tool-a"), now=NOW)
    assert decision.code == CapabilityDenyCode.MISSING_GRANT


def test_naive_expiry_is_invalid() -> None:
    decision = evaluate_capability(
        _grant(expires_at=datetime(2026, 10, 1, 13, 0)),
        CapabilityRequirements(tool_name="tool-a"),
        now=NOW,
    )
    assert decision.code == CapabilityDenyCode.INVALID_GRANT


def test_expired_grant_is_denied() -> None:
    decision = evaluate_capability(
        _grant(expires_at=NOW - timedelta(seconds=1)),
        CapabilityRequirements(tool_name="tool-a"),
        now=NOW,
    )
    assert decision.code == CapabilityDenyCode.EXPIRED


@pytest.mark.parametrize(
    ("requirements", "code"),
    [
        (CapabilityRequirements(tool_name="nope"), CapabilityDenyCode.TOOL_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", project_id="other"), CapabilityDenyCode.PROJECT_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", task_id="other"), CapabilityDenyCode.TASK_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", read_paths=("/workspace/elsewhere/x",)), CapabilityDenyCode.READ_PATH_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", write_paths=("/workspace/project/elsewhere/x",)), CapabilityDenyCode.WRITE_PATH_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", network=True), CapabilityDenyCode.NETWORK_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", external_services=frozenset({"slack"})), CapabilityDenyCode.SERVICE_NOT_GRANTED),
        (CapabilityRequirements(tool_name="tool-a", credentials=frozenset({"other-secret"})), CapabilityDenyCode.CREDENTIAL_NOT_GRANTED),
    ],
)
def test_scope_violations_are_stable(requirements, code) -> None:
    grant = _grant(allow_network=False) if code == CapabilityDenyCode.NETWORK_NOT_GRANTED else _grant()
    decision = evaluate_capability(grant, requirements, now=NOW)
    assert decision.allowed is False
    assert decision.code == code


def test_path_containment_is_component_aware() -> None:
    assert path_is_within_root("/workspace/foo/bar", "/workspace/foo")
    assert not path_is_within_root("/workspace/foobar", "/workspace/foo")
    assert not path_is_within_root("/workspace/foo/../secret", "/workspace/foo")


def test_child_grant_may_narrow_but_never_widen() -> None:
    parent = _grant()
    child = _grant(
        grant_id="child",
        tools=frozenset({"tool-a"}),
        project_ids=frozenset({"project-1"}),
        task_ids=frozenset(),
        read_roots=("/workspace/project/sub",),
        write_roots=("/workspace/project/out/sub",),
        external_services=frozenset(),
        credentials=frozenset(),
        expires_at=NOW + timedelta(minutes=5),
    )
    assert validate_child_grant(parent, child).allowed is True

    widened = CapabilityGrant(
        grant_id="wide",
        tools=frozenset({"tool-a", "tool-b", "tool-c"}),
        expires_at=NOW + timedelta(minutes=5),
    )
    assert validate_child_grant(parent, widened).code == CapabilityDenyCode.CHILD_WIDENS_PARENT


def test_child_expiry_cannot_exceed_parent() -> None:
    parent = _grant()
    child = _grant(grant_id="child", expires_at=parent.expires_at + timedelta(seconds=1))
    assert validate_child_grant(parent, child).code == CapabilityDenyCode.CHILD_WIDENS_PARENT


def test_requirements_hook_is_strict_and_cannot_override_identity() -> None:
    class Tool:
        name = "tool-a"

        def capability_requirements_for_args(self, args):
            return {
                "write_paths": ["/workspace/project/out/result.txt"],
                "network": True,
                "external_services": ["github"],
            }

    requirements = requirements_for_tool(Tool(), {}, project_id="project-1", task_id="task-1")
    assert requirements.tool_name == "tool-a"
    assert requirements.project_id == "project-1"
    assert requirements.task_id == "task-1"
    assert requirements.network is True


def test_malformed_requirements_fail_closed() -> None:
    class Tool:
        name = "tool-a"

        def capability_requirements_for_args(self, args):
            return {"unknown": True}

    with pytest.raises(CapabilityRequirementsError):
        requirements_for_tool(Tool(), {})
