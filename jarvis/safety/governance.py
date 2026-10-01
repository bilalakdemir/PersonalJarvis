"""Project-state governance rules enforced before risk/approval.

Canonical project state files are reserved mutation surfaces. Generic tools may
read them, but writes must go through ``ProjectStateStore`` so proposal,
approval, validation, rollback, and audit guarantees remain intact.
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass
from enum import StrEnum

from jarvis.projects.models import CANONICAL_PROJECT_FILES

from .capabilities import CapabilityRequirements, path_is_direct_child


class GovernanceDenyCode(StrEnum):
    CANONICAL_STATE_MUTATION = "canonical_state_mutation"
    MALFORMED_PATH = "malformed_path"


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    allowed: bool
    code: GovernanceDenyCode | None = None
    reason: str = ""


def _deny(code: GovernanceDenyCode, reason: str) -> GovernanceDecision:
    return GovernanceDecision(False, code, reason)


def evaluate_governance(
    requirements: CapabilityRequirements,
    *,
    project_root: str | None = None,
) -> GovernanceDecision:
    """Block generic mutation of canonical project files."""

    for path in requirements.write_paths:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            return _deny(GovernanceDenyCode.MALFORMED_PATH, "write path is malformed")
        basename = ntpath.basename(path.replace("/", "\\"))
        if basename not in CANONICAL_PROJECT_FILES:
            continue
        if project_root is None or path_is_direct_child(path, project_root):
            return _deny(
                GovernanceDenyCode.CANONICAL_STATE_MUTATION,
                "Canonical project state must be changed through ProjectStateStore.",
            )
    return GovernanceDecision(True)


__all__ = [
    "GovernanceDecision",
    "GovernanceDenyCode",
    "evaluate_governance",
]
