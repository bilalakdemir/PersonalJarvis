"""Pure governance rules for promoting temporary memory."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .wiki.constants import FactBasis
from .wiki.secret_guard import contains_secret


class MemoryPromotionOutcome(str, Enum):
    TEMPORARY = "TEMPORARY"
    AUTO_PERSIST = "AUTO-PERSIST"
    APPROVAL_REQUIRED = "APPROVAL-REQUIRED"
    ROUTE_TO_PROJECT_STATE = "ROUTE-TO-PROJECT-STATE"
    REJECT = "REJECT"


class MemoryGovernanceClass(str, Enum):
    ORDINARY = "ordinary"
    SOP = "sop"
    STANDARD = "standard"
    OPERATING_RULE = "operating-rule"
    LONG_TERM_INSTRUCTION = "long-term-instruction"
    GOVERNANCE_RULE = "governance-rule"
    USER_WIDE_DECISION = "user-wide-decision"
    CROSS_PROJECT_PRIORITY = "cross-project-priority"
    BEHAVIORAL_POLICY = "behavioral-policy"


class ProjectMemoryRelation(str, Enum):
    NONE = "none"
    BACKGROUND = "background"
    EXECUTION_STATUS = "execution-status"
    TASK = "task"
    DECISION = "decision"
    STANDARD = "standard"
    CURRENT_TASK = "current-task"
    COMPLETION_STATE = "completion-state"
    BLOCKER = "blocker"
    NEXT_TASK = "next-task"
    MILESTONE_STATUS = "milestone-status"


class MemoryAuthority(str, Enum):
    PERSISTENT_MEMORY = "persistent-memory"
    PROJECT_STATE = "project-state"
    UNRESOLVED_PROJECT = "unresolved-project"


_PROJECT_STATE_RELATIONS = frozenset(
    {
        ProjectMemoryRelation.EXECUTION_STATUS,
        ProjectMemoryRelation.TASK,
        ProjectMemoryRelation.DECISION,
        ProjectMemoryRelation.STANDARD,
        ProjectMemoryRelation.CURRENT_TASK,
        ProjectMemoryRelation.COMPLETION_STATE,
        ProjectMemoryRelation.BLOCKER,
        ProjectMemoryRelation.NEXT_TASK,
        ProjectMemoryRelation.MILESTONE_STATUS,
    }
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    content: str
    basis: FactBasis = "explicit"
    durable: bool = False
    immediate_task_context: bool = False
    governance_class: MemoryGovernanceClass = MemoryGovernanceClass.ORDINARY
    project_relation: ProjectMemoryRelation = ProjectMemoryRelation.NONE
    project_id: str | None = None
    explicit_remember: bool = False
    user_confirmed: bool = False
    supporting_user_turns: int = 1
    duplicate: bool = False
    conflict: bool = False
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class MemoryAuthorityDecision:
    authority: MemoryAuthority
    project_id: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MemoryPromotionDecision:
    outcome: MemoryPromotionOutcome
    reason: str
    project_id: str | None = None


class MemoryAuthorityResolver:
    """Resolve storage authority without guessing missing project identity."""

    def resolve(self, candidate: MemoryCandidate) -> MemoryAuthorityDecision:
        if candidate.project_relation in _PROJECT_STATE_RELATIONS:
            project_id = (candidate.project_id or "").strip() or None
            if project_id is None:
                return MemoryAuthorityDecision(
                    MemoryAuthority.UNRESOLVED_PROJECT,
                    reason="project-state-authority-unresolved",
                )
            return MemoryAuthorityDecision(
                MemoryAuthority.PROJECT_STATE,
                project_id=project_id,
                reason="canonical-project-state-authority",
            )

        return MemoryAuthorityDecision(
            MemoryAuthority.PERSISTENT_MEMORY,
            project_id=(candidate.project_id or "").strip() or None,
            reason="memory-authority",
        )


class MemoryPromotionGate:
    """Conservative deterministic promotion gate."""

    def __init__(
        self,
        authority_resolver: MemoryAuthorityResolver | None = None,
    ) -> None:
        self._authority = authority_resolver or MemoryAuthorityResolver()

    def classify(self, candidate: MemoryCandidate) -> MemoryPromotionDecision:
        content = candidate.content.strip()
        if not content:
            return self._decision(
                MemoryPromotionOutcome.REJECT,
                "empty-candidate",
            )

        if contains_secret(content):
            return self._decision(
                MemoryPromotionOutcome.REJECT,
                "secret-shaped-content",
            )

        if candidate.duplicate:
            return self._decision(
                MemoryPromotionOutcome.REJECT,
                "duplicate",
            )

        authority = self._authority.resolve(candidate)

        if authority.authority is MemoryAuthority.UNRESOLVED_PROJECT:
            return self._decision(
                MemoryPromotionOutcome.TEMPORARY,
                authority.reason,
            )

        if authority.authority is MemoryAuthority.PROJECT_STATE:
            return self._decision(
                MemoryPromotionOutcome.ROUTE_TO_PROJECT_STATE,
                authority.reason,
                project_id=authority.project_id,
            )

        # A direct remember/save instruction is explicit approval for
        # non-project persistent memory, but does not override ambiguity or
        # conflict resolution.
        if candidate.explicit_remember:
            if candidate.conflict:
                return self._decision(
                    MemoryPromotionOutcome.APPROVAL_REQUIRED,
                    "explicit-request-conflicts-with-existing-memory",
                )
            if candidate.ambiguous:
                return self._decision(
                    MemoryPromotionOutcome.APPROVAL_REQUIRED,
                    "explicit-request-is-ambiguous",
                )
            return self._decision(
                MemoryPromotionOutcome.AUTO_PERSIST,
                "explicit-persistence-request",
            )

        if not candidate.durable:
            return self._decision(
                MemoryPromotionOutcome.TEMPORARY,
                "not-durable",
            )

        if candidate.immediate_task_context:
            return self._decision(
                MemoryPromotionOutcome.TEMPORARY,
                "immediate-task-context",
            )

        if candidate.conflict:
            return self._decision(
                MemoryPromotionOutcome.APPROVAL_REQUIRED,
                "persistent-memory-conflict",
            )

        if candidate.ambiguous:
            return self._decision(
                MemoryPromotionOutcome.APPROVAL_REQUIRED,
                "materially-ambiguous",
            )

        if candidate.governance_class is not MemoryGovernanceClass.ORDINARY:
            return self._decision(
                MemoryPromotionOutcome.APPROVAL_REQUIRED,
                "governed-persistent-memory",
            )

        if candidate.basis == "explicit":
            return self._decision(
                MemoryPromotionOutcome.AUTO_PERSIST,
                "explicit-durable-fact",
            )

        if candidate.basis == "behavioral":
            if candidate.user_confirmed:
                return self._decision(
                    MemoryPromotionOutcome.AUTO_PERSIST,
                    "behavioral-fact-user-confirmed",
                )
            if candidate.supporting_user_turns >= 2:
                return self._decision(
                    MemoryPromotionOutcome.AUTO_PERSIST,
                    "behavioral-fact-multi-turn-supported",
                )
            return self._decision(
                MemoryPromotionOutcome.TEMPORARY,
                "behavioral-fact-insufficient-support",
            )

        if candidate.basis == "inferred":
            if candidate.user_confirmed:
                return self._decision(
                    MemoryPromotionOutcome.AUTO_PERSIST,
                    "inferred-fact-user-confirmed",
                )
            return self._decision(
                MemoryPromotionOutcome.TEMPORARY,
                "inferred-fact-unconfirmed",
            )

        return self._decision(
            MemoryPromotionOutcome.REJECT,
            "unsupported-evidence-basis",
        )

    @staticmethod
    def _decision(
        outcome: MemoryPromotionOutcome,
        reason: str,
        *,
        project_id: str | None = None,
    ) -> MemoryPromotionDecision:
        return MemoryPromotionDecision(
            outcome=outcome,
            reason=reason,
            project_id=project_id,
        )


__all__ = [
    "MemoryAuthority",
    "MemoryAuthorityDecision",
    "MemoryAuthorityResolver",
    "MemoryCandidate",
    "MemoryGovernanceClass",
    "MemoryPromotionDecision",
    "MemoryPromotionGate",
    "MemoryPromotionOutcome",
    "ProjectMemoryRelation",
]
