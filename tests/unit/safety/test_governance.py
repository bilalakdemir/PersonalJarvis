from __future__ import annotations

from jarvis.safety.capabilities import CapabilityRequirements
from jarvis.safety.governance import GovernanceDenyCode, evaluate_governance


def test_canonical_project_state_write_is_denied() -> None:
    decision = evaluate_governance(
        CapabilityRequirements(
            tool_name="generic-write",
            write_paths=("/projects/jarvis/STATE.md",),
        ),
        project_root="/projects/jarvis",
    )
    assert decision.allowed is False
    assert decision.code == GovernanceDenyCode.CANONICAL_STATE_MUTATION
    assert "ProjectStateStore" in decision.reason


def test_canonical_project_state_read_is_allowed() -> None:
    decision = evaluate_governance(
        CapabilityRequirements(
            tool_name="generic-read",
            read_paths=("/projects/jarvis/STATE.md",),
        ),
        project_root="/projects/jarvis",
    )
    assert decision.allowed is True


def test_noncanonical_write_is_allowed() -> None:
    decision = evaluate_governance(
        CapabilityRequirements(
            tool_name="generic-write",
            write_paths=("/projects/jarvis/notes.md",),
        ),
        project_root="/projects/jarvis",
    )
    assert decision.allowed is True


def test_nested_same_name_is_not_the_project_root_state_file() -> None:
    decision = evaluate_governance(
        CapabilityRequirements(
            tool_name="generic-write",
            write_paths=("/projects/jarvis/archive/STATE.md",),
        ),
        project_root="/projects/jarvis",
    )
    assert decision.allowed is True


def test_canonical_name_without_project_root_fails_closed() -> None:
    decision = evaluate_governance(
        CapabilityRequirements(
            tool_name="generic-write",
            write_paths=("/unknown/PROJECT.md",),
        ),
    )
    assert decision.code == GovernanceDenyCode.CANONICAL_STATE_MUTATION
