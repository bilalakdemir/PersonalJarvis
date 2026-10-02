from __future__ import annotations

import pytest

from jarvis.memory.governance import (
    MemoryCandidate,
    MemoryGovernanceClass,
    MemoryPromotionGate,
    MemoryPromotionOutcome,
    ProjectMemoryRelation,
)


@pytest.fixture
def gate() -> MemoryPromotionGate:
    return MemoryPromotionGate()


def test_explicit_durable_ordinary_fact_auto_persists(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user prefers dark mode.",
            basis="explicit",
            durable=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.AUTO_PERSIST


def test_immediate_task_context_remains_temporary(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user wants this paragraph shortened.",
            basis="explicit",
            durable=True,
            immediate_task_context=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.TEMPORARY


@pytest.mark.parametrize(
    "governance_class",
    [
        MemoryGovernanceClass.SOP,
        MemoryGovernanceClass.STANDARD,
        MemoryGovernanceClass.OPERATING_RULE,
        MemoryGovernanceClass.LONG_TERM_INSTRUCTION,
        MemoryGovernanceClass.GOVERNANCE_RULE,
        MemoryGovernanceClass.USER_WIDE_DECISION,
        MemoryGovernanceClass.CROSS_PROJECT_PRIORITY,
        MemoryGovernanceClass.BEHAVIORAL_POLICY,
    ],
)
def test_new_governance_memory_requires_approval(
    gate: MemoryPromotionGate,
    governance_class: MemoryGovernanceClass,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="Use this as a lasting operating rule.",
            durable=True,
            governance_class=governance_class,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.APPROVAL_REQUIRED


def test_explicit_remember_is_approval_for_non_project_rule(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="Always use this review rule.",
            durable=True,
            explicit_remember=True,
            governance_class=MemoryGovernanceClass.OPERATING_RULE,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.AUTO_PERSIST


def test_single_behavioral_observation_remains_temporary(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user appears to enjoy golf.",
            basis="behavioral",
            durable=True,
            supporting_user_turns=1,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.TEMPORARY


def test_multi_turn_behavioral_fact_can_auto_persist(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user regularly plays golf.",
            basis="behavioral",
            durable=True,
            supporting_user_turns=2,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.AUTO_PERSIST


def test_unconfirmed_inference_remains_temporary(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user probably prefers morning meetings.",
            basis="inferred",
            durable=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.TEMPORARY


def test_user_confirmed_inference_can_persist(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user prefers morning meetings.",
            basis="inferred",
            durable=True,
            user_confirmed=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.AUTO_PERSIST


@pytest.mark.parametrize(
    "relation",
    [
        ProjectMemoryRelation.EXECUTION_STATUS,
        ProjectMemoryRelation.TASK,
        ProjectMemoryRelation.DECISION,
        ProjectMemoryRelation.STANDARD,
        ProjectMemoryRelation.CURRENT_TASK,
        ProjectMemoryRelation.COMPLETION_STATE,
        ProjectMemoryRelation.BLOCKER,
        ProjectMemoryRelation.NEXT_TASK,
        ProjectMemoryRelation.MILESTONE_STATUS,
    ],
)
def test_authoritative_project_state_routes_to_project_engine(
    gate: MemoryPromotionGate,
    relation: ProjectMemoryRelation,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="Project execution state changed.",
            durable=True,
            explicit_remember=True,
            project_relation=relation,
            project_id="personal-jarvis",
        )
    )

    assert result.outcome is MemoryPromotionOutcome.ROUTE_TO_PROJECT_STATE
    assert result.project_id == "personal-jarvis"


def test_unresolved_project_authority_fails_closed_as_temporary(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The current project task is finished.",
            durable=True,
            project_relation=ProjectMemoryRelation.CURRENT_TASK,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.TEMPORARY
    assert result.reason == "project-state-authority-unresolved"


def test_project_background_can_remain_normal_memory(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="Personal Jarvis is the user's personal AI assistant project.",
            basis="explicit",
            durable=True,
            project_relation=ProjectMemoryRelation.BACKGROUND,
            project_id="personal-jarvis",
        )
    )

    assert result.outcome is MemoryPromotionOutcome.AUTO_PERSIST


def test_conflict_requires_approval(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user now prefers light mode.",
            basis="explicit",
            durable=True,
            conflict=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.APPROVAL_REQUIRED


def test_material_ambiguity_requires_approval(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="Use that preference permanently.",
            basis="explicit",
            durable=True,
            ambiguous=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.APPROVAL_REQUIRED


def test_duplicate_is_rejected(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="The user prefers dark mode.",
            durable=True,
            duplicate=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.REJECT


def test_secret_shaped_candidate_is_rejected(
    gate: MemoryPromotionGate,
) -> None:
    result = gate.classify(
        MemoryCandidate(
            content="credential sk-proj-" + ("a" * 30),
            durable=True,
        )
    )

    assert result.outcome is MemoryPromotionOutcome.REJECT
