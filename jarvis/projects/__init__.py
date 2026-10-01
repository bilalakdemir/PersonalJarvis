"""Managed-project state primitives for Personal Jarvis."""
from .loader import (
    compute_state_revision,
    compute_state_revision_from_bytes,
    evaluate_project_documents,
    load_project_context,
    project_documents_from_bytes,
)
from .models import (
    CANONICAL_PROJECT_FILES,
    ProjectContextSnapshot,
    ProjectDecision,
    ProjectFileChange,
    ProjectLoadResult,
    ProjectRegistryEntry,
    ProjectStateApproval,
    ProjectStateChangeProposal,
    ProjectStateTransactionResult,
    ProjectValidationIssue,
    ProjectValidationResult,
)
from .proposal import (
    ProjectStateProposalError,
    build_project_state_proposal,
    proposal_digest,
)
from .registry import (
    AmbiguousProjectError,
    ProjectNotRegisteredError,
    ProjectRegistry,
    ProjectRegistryError,
    default_registry_path,
    load_registry,
)

__all__ = [
    "CANONICAL_PROJECT_FILES",
    "AmbiguousProjectError",
    "ProjectContextSnapshot",
    "ProjectDecision",
    "ProjectFileChange",
    "ProjectLoadResult",
    "ProjectNotRegisteredError",
    "ProjectRegistry",
    "ProjectRegistryEntry",
    "ProjectRegistryError",
    "ProjectStateApproval",
    "ProjectStateChangeProposal",
    "ProjectStateProposalError",
    "ProjectStateTransactionResult",
    "ProjectValidationIssue",
    "ProjectValidationResult",
    "build_project_state_proposal",
    "compute_state_revision",
    "compute_state_revision_from_bytes",
    "default_registry_path",
    "evaluate_project_documents",
    "load_project_context",
    "load_registry",
    "project_documents_from_bytes",
    "proposal_digest",
]
