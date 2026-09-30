"""Managed-project state primitives for Personal Jarvis."""
from .loader import compute_state_revision, load_project_context
from .models import (
    CANONICAL_PROJECT_FILES,
    ProjectContextSnapshot,
    ProjectDecision,
    ProjectLoadResult,
    ProjectRegistryEntry,
    ProjectValidationIssue,
    ProjectValidationResult,
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
    "ProjectLoadResult",
    "ProjectNotRegisteredError",
    "ProjectRegistry",
    "ProjectRegistryEntry",
    "ProjectRegistryError",
    "ProjectValidationIssue",
    "ProjectValidationResult",
    "compute_state_revision",
    "default_registry_path",
    "load_project_context",
    "load_registry",
]
