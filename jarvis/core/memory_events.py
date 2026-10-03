"""Typed, metadata-only events for governed memory lifecycle."""
from __future__ import annotations

from dataclasses import dataclass

from .events import Event


@dataclass(frozen=True, slots=True)
class TemporaryMemoryStored(Event):
    item_id: int = 0
    kind: str = ""
    project_id: str | None = None
    expires_ms: int = 0


@dataclass(frozen=True, slots=True)
class MemoryPreExpiryReviewRequired(Event):
    item_id: int = 0
    kind: str = ""
    project_id: str | None = None
    expires_ms: int = 0


@dataclass(frozen=True, slots=True)
class TemporaryMemoryExpired(Event):
    item_id: int = 0
    kind: str = ""
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryCandidateCreated(Event):
    candidate_id: int = 0
    kind: str = ""
    basis: str = ""


@dataclass(frozen=True, slots=True)
class MemoryPromotionProposed(Event):
    candidate_id: int = 0
    proposal_digest: str = ""
    governance_class: str = ""
    expires_ms: int = 0


@dataclass(frozen=True, slots=True)
class MemoryPromotionApproved(Event):
    candidate_id: int = 0
    proposal_digest: str = ""


@dataclass(frozen=True, slots=True)
class MemoryPromotionRejected(Event):
    candidate_id: int = 0
    proposal_digest: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PersistentMemoryChanged(Event):
    candidate_id: int = 0
    change_kind: str = ""
    target_ref: str = ""


@dataclass(frozen=True, slots=True)
class MemoryConflictDetected(Event):
    candidate_id: int = 0
    conflict_ref: str = ""


@dataclass(frozen=True, slots=True)
class MemoryDeleted(Event):
    memory_kind: str = ""
    memory_id: str = ""
    reason: str = ""
