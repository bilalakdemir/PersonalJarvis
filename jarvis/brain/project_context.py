"""Deterministic, read-only project-context resolution for brain turns."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jarvis.projects import (
    ProjectContextSnapshot,
    ProjectRegistry,
    ProjectRegistryEntry,
    ProjectRegistryError,
    load_project_context,
    load_registry,
)

_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_SIGNIFICANT_TOKEN_MIN = 3


class ProjectContextResolutionStatus(str, Enum):
    """Outcome of resolving one user turn against canonical project state."""

    RESOLVED = "RESOLVED"
    NO_PROJECT = "NO_PROJECT"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ProjectContextResolution:
    """Immutable project resolution for one turn."""

    status: ProjectContextResolutionStatus
    snapshot: ProjectContextSnapshot | None = None
    project_id: str | None = None
    matched_by: str | None = None
    detail: str | None = None

    @property
    def resolved(self) -> bool:
        return (
            self.status is ProjectContextResolutionStatus.RESOLVED
            and self.snapshot is not None
        )


def _normalise(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_normalise(match.group(0)) for match in _WORD_RE.finditer(value))


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase_tokens = _tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = _tokens(text)
    width = len(phrase_tokens)
    return any(
        text_tokens[index : index + width] == phrase_tokens
        for index in range(len(text_tokens) - width + 1)
    )


def _id_or_name_matches(
    registry: ProjectRegistry,
    value: str,
) -> tuple[ProjectRegistryEntry, ...]:
    key = _normalise(value)
    return tuple(
        entry
        for entry in registry.entries
        if key in {_normalise(entry.project_id), _normalise(entry.project_name)}
    )


def _text_id_or_name_matches(
    registry: ProjectRegistry,
    text: str,
) -> tuple[ProjectRegistryEntry, ...]:
    return tuple(
        entry
        for entry in registry.entries
        if _contains_phrase(text, entry.project_id)
        or _contains_phrase(text, entry.project_name)
    )


def _text_alias_matches(
    registry: ProjectRegistry,
    text: str,
) -> tuple[ProjectRegistryEntry, ...]:
    return tuple(
        entry
        for entry in registry.entries
        if any(_contains_phrase(text, alias) for alias in entry.aliases)
    )


def _unique(
    entries: tuple[ProjectRegistryEntry, ...],
) -> tuple[ProjectRegistryEntry, ...]:
    by_id = {entry.project_id: entry for entry in entries}
    return tuple(by_id[key] for key in sorted(by_id))


def _high_confidence_matches(
    registry: ProjectRegistry,
    text: str,
) -> tuple[ProjectRegistryEntry, ...]:
    """Return deterministic candidates from strong token coverage.

    General fuzzy matching is intentionally avoided. A candidate needs at least
    two significant registry-key tokens and every such token must occur in the
    current user turn. Exact IDs, names, and aliases are handled earlier.
    """

    user_tokens = set(_tokens(text))
    matches: list[ProjectRegistryEntry] = []
    for entry in registry.entries:
        candidate_keys = (entry.project_name, *entry.aliases)
        for key in candidate_keys:
            significant = {
                token
                for token in _tokens(key)
                if len(token) >= _SIGNIFICANT_TOKEN_MIN
            }
            if len(significant) >= 2 and significant.issubset(user_tokens):
                matches.append(entry)
                break
    return _unique(tuple(matches))


def _ambiguous(detail: str) -> ProjectContextResolution:
    return ProjectContextResolution(
        status=ProjectContextResolutionStatus.AMBIGUOUS,
        detail=detail,
    )


def _unavailable(
    detail: str,
    *,
    project_id: str | None = None,
) -> ProjectContextResolution:
    return ProjectContextResolution(
        status=ProjectContextResolutionStatus.UNAVAILABLE,
        project_id=project_id,
        detail=detail,
    )


class ProjectContextResolver:
    """Resolve a turn to canonical project state without mutating project data."""

    def __init__(self, registry_path: Path | None = None) -> None:
        self._registry_path = registry_path

    def _load_registry(self) -> ProjectRegistry | ProjectContextResolution:
        try:
            return load_registry(self._registry_path)
        except ProjectRegistryError as exc:
            return _unavailable(str(exc))

    @staticmethod
    def _load_snapshot(
        entry: ProjectRegistryEntry,
        *,
        matched_by: str,
    ) -> ProjectContextResolution:
        result = load_project_context(entry)
        if not result.validation.valid or result.snapshot is None:
            issues = (
                "; ".join(issue.code for issue in result.validation.issues)
                or "invalid canonical project state"
            )
            return _unavailable(issues, project_id=entry.project_id)
        return ProjectContextResolution(
            status=ProjectContextResolutionStatus.RESOLVED,
            snapshot=result.snapshot,
            project_id=entry.project_id,
            matched_by=matched_by,
        )

    def resolve(
        self,
        user_text: str,
        *,
        explicit_project: str | None = None,
        active_project_id: str | None = None,
        continuation: bool = False,
    ) -> ProjectContextResolution:
        """Resolve one turn using the approved deterministic precedence order."""

        registry_or_error = self._load_registry()
        if isinstance(registry_or_error, ProjectContextResolution):
            return registry_or_error
        registry = registry_or_error

        # 1. Explicit project ID/name. A structured hint wins over text mentions.
        if explicit_project:
            explicit_matches = _unique(
                _id_or_name_matches(registry, explicit_project)
            )
            if len(explicit_matches) > 1:
                return _ambiguous(
                    f"explicit project is ambiguous: {explicit_project!r}"
                )
            if len(explicit_matches) == 1:
                return self._load_snapshot(
                    explicit_matches[0],
                    matched_by="explicit",
                )

        text_matches = _unique(
            _text_id_or_name_matches(registry, user_text)
        )
        if len(text_matches) > 1:
            return _ambiguous(
                "multiple project IDs/names are explicitly referenced"
            )
        if len(text_matches) == 1:
            return self._load_snapshot(
                text_matches[0],
                matched_by="explicit",
            )

        # 2. Clear continuation of an already-active project. The caller must
        # supply both facts; this resolver never infers active state from memory.
        if continuation and active_project_id:
            active_matches = _unique(
                _id_or_name_matches(registry, active_project_id)
            )
            if len(active_matches) != 1:
                if len(active_matches) > 1:
                    return _ambiguous(
                        f"active project is ambiguous: {active_project_id!r}"
                    )
                return _unavailable(
                    f"active project is not registered: {active_project_id!r}",
                    project_id=active_project_id,
                )
            return self._load_snapshot(
                active_matches[0],
                matched_by="active-continuation",
            )

        # 3. Exact registry alias mentioned in the current turn.
        alias_matches = _unique(
            _text_alias_matches(registry, user_text)
        )
        if len(alias_matches) > 1:
            return _ambiguous("multiple project aliases are referenced")
        if len(alias_matches) == 1:
            return self._load_snapshot(
                alias_matches[0],
                matched_by="alias",
            )

        # A structured hint may itself be an alias; it intentionally reaches
        # this stage only after ID/name and active-continuation checks.
        if explicit_project:
            alias_hint_matches = _unique(
                tuple(
                    entry
                    for entry in registry.entries
                    if _normalise(explicit_project)
                    in {_normalise(alias) for alias in entry.aliases}
                )
            )
            if len(alias_hint_matches) > 1:
                return _ambiguous(
                    f"project alias is ambiguous: {explicit_project!r}"
                )
            if len(alias_hint_matches) == 1:
                return self._load_snapshot(
                    alias_hint_matches[0],
                    matched_by="alias",
                )

        # 4. Deterministic unique high-confidence registry match.
        confidence_matches = _high_confidence_matches(
            registry,
            user_text,
        )
        if len(confidence_matches) > 1:
            return _ambiguous(
                "multiple high-confidence project matches"
            )
        if len(confidence_matches) == 1:
            return self._load_snapshot(
                confidence_matches[0],
                matched_by="high-confidence",
            )

        # 5. No project. Conversation history and memory are intentionally not
        # consulted here, so they cannot replace canonical state.
        return ProjectContextResolution(
            status=ProjectContextResolutionStatus.NO_PROJECT
        )


_CONTINUATION_RE = re.compile(
    r"\b(?:continue|keep going|carry on|resume|same project|"
    + r"devam(?: et| edelim| edebiliriz)?|ayn[ıi] proj|buradan devam|"
    + r"weiter|weitermachen|fortsetzen)\b",
    re.IGNORECASE,
)


def is_clear_project_continuation(user_text: str) -> bool:
    """Return whether the current turn explicitly asks to continue prior work."""

    return bool(_CONTINUATION_RE.search(user_text))


def render_project_context(resolution: ProjectContextResolution) -> str:
    """Render structural project state for current-turn context only."""

    if resolution.status is ProjectContextResolutionStatus.NO_PROJECT:
        return ""

    if resolution.status is ProjectContextResolutionStatus.AMBIGUOUS:
        return (
            "[PROJECT CONTEXT — AMBIGUOUS]\n"
            "No project has been selected for this turn. Do not choose a project "
            "from conversation history or memory. Ask one targeted clarification "
            "before project-mutating work."
        )

    if resolution.status is ProjectContextResolutionStatus.UNAVAILABLE:
        detail = resolution.detail or "canonical project state is unavailable"
        project = (
            f" Project ID: {resolution.project_id}."
            if resolution.project_id
            else ""
        )
        return (
            "[PROJECT CONTEXT — CANONICAL STATE UNAVAILABLE]\n"
            f"{detail}.{project}\n"
            "Do not reconstruct or replace canonical project state from "
            "conversation history or memory."
        )

    snapshot = resolution.snapshot
    if snapshot is None:
        return ""

    decisions = "; ".join(
        f"{item.decision_id}: {item.title}"
        for item in snapshot.active_decisions
    ) or "None"
    backlog = "; ".join(snapshot.relevant_backlog_items) or "None"
    return "\n".join(
        (
            "[PROJECT CONTEXT — AUTHORITATIVE CANONICAL STATE]",
            f"Project ID: {snapshot.project_id}",
            f"Project: {snapshot.project_name}",
            f"Main goal: {snapshot.main_goal}",
            f"Phase: {snapshot.phase}",
            f"CURRENT task: {snapshot.current_task or 'None'}",
            f"Last completed: {snapshot.last_completed}",
            f"Next logical step: {snapshot.next_step}",
            f"Blockers: {snapshot.blockers}",
            f"Active decisions: {decisions}",
            f"Relevant backlog: {backlog}",
            f"State revision: {snapshot.state_revision}",
            (
                "Authority rule: this canonical project state overrides conflicting "
                "conversation history or memory for project execution state."
            ),
        )
    )


class ProjectTurnContext:
    """Conversation-scoped routing hints; canonical state is always reloaded."""

    def __init__(self, resolver: ProjectContextResolver) -> None:
        self._resolver = resolver
        self._active_by_conversation: dict[str, str] = {}

    def resolve_turn(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        explicit_project: str | None = None,
    ) -> ProjectContextResolution:
        active_project_id = (
            self._active_by_conversation.get(conversation_id)
            if conversation_id
            else None
        )
        resolution = self._resolver.resolve(
            user_text,
            explicit_project=explicit_project,
            active_project_id=active_project_id,
            continuation=bool(
                active_project_id
                and is_clear_project_continuation(user_text)
            ),
        )
        if (
            conversation_id
            and resolution.status is ProjectContextResolutionStatus.RESOLVED
            and resolution.project_id
        ):
            self._active_by_conversation[conversation_id] = resolution.project_id
        return resolution

    def build_turn_block(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        explicit_project: str | None = None,
    ) -> str:
        return render_project_context(
            self.resolve_turn(
                user_text,
                conversation_id=conversation_id,
                explicit_project=explicit_project,
            )
        )
