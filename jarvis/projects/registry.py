"""Read-only registry for managed Personal Jarvis projects."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ProjectRegistryEntry, ProjectStatus

_ALLOWED_STATUSES = frozenset({"active", "paused", "completed", "archived"})


class ProjectRegistryError(ValueError):
    """Registry data is missing, malformed, or internally inconsistent."""


class ProjectNotRegisteredError(ProjectRegistryError):
    """A project root is not present in the managed-project registry."""


class AmbiguousProjectError(ProjectRegistryError):
    """An exact project lookup key points at more than one project."""


def _normalise_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def default_registry_path() -> Path:
    """Return the runtime registry location without creating it."""
    from jarvis.core.config import DATA_DIR

    return DATA_DIR / "projects" / "registry.json"


def _required_string(raw: dict[str, Any], key: str, *, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectRegistryError(f"projects[{index}].{key} must be a non-empty string")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str, *, index: int) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectRegistryError(f"projects[{index}].{key} must be a non-empty string when set")
    return value.strip()


def _parse_entry(raw: Any, *, index: int) -> ProjectRegistryEntry:
    if not isinstance(raw, dict):
        raise ProjectRegistryError(f"projects[{index}] must be an object")

    project_id = _required_string(raw, "project_id", index=index)
    project_name = _required_string(raw, "project_name", index=index)
    root_raw = _required_string(raw, "root_path", index=index)
    status_raw = _required_string(raw, "status", index=index).casefold()
    if status_raw not in _ALLOWED_STATUSES:
        allowed = ", ".join(sorted(_ALLOWED_STATUSES))
        raise ProjectRegistryError(f"projects[{index}].status must be one of: {allowed}")

    aliases_raw = raw.get("aliases", [])
    if not isinstance(aliases_raw, list) or any(not isinstance(item, str) for item in aliases_raw):
        raise ProjectRegistryError(f"projects[{index}].aliases must be an array of strings")
    aliases = tuple(alias.strip() for alias in aliases_raw if alias.strip())

    root_path = Path(root_raw).expanduser()
    if not root_path.is_absolute():
        raise ProjectRegistryError(f"projects[{index}].root_path must be absolute")

    return ProjectRegistryEntry(
        project_id=project_id,
        project_name=project_name,
        aliases=aliases,
        root_path=root_path.resolve(strict=False),
        status=status_raw,  # type: ignore[arg-type]
        created_at=_optional_string(raw, "created_at", index=index),
        last_opened_at=_optional_string(raw, "last_opened_at", index=index),
        archived_at=_optional_string(raw, "archived_at", index=index),
    )


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    """Validated, read-only managed-project registry."""

    entries: tuple[ProjectRegistryEntry, ...]

    def resolve_exact(self, value: str) -> ProjectRegistryEntry | None:
        """Resolve an exact ID, project name, or alias match."""
        key = _normalise_key(value)
        if not key:
            return None
        matches = [entry for entry in self.entries if key in _entry_keys(entry)]
        if len(matches) > 1:
            ids = ", ".join(sorted(entry.project_id for entry in matches))
            raise AmbiguousProjectError(f"project key {value!r} is ambiguous: {ids}")
        return matches[0] if matches else None

    def find_by_root(self, root_path: Path) -> ProjectRegistryEntry | None:
        resolved = root_path.expanduser().resolve(strict=False)
        matches = [entry for entry in self.entries if entry.root_path == resolved]
        if len(matches) > 1:
            ids = ", ".join(sorted(entry.project_id for entry in matches))
            raise AmbiguousProjectError(f"project root {resolved} is ambiguous: {ids}")
        return matches[0] if matches else None

    def require_root(self, root_path: Path) -> ProjectRegistryEntry:
        entry = self.find_by_root(root_path)
        if entry is None:
            raise ProjectNotRegisteredError(
                f"project root is not registered: {root_path.expanduser().resolve(strict=False)}"
            )
        return entry


def _entry_keys(entry: ProjectRegistryEntry) -> frozenset[str]:
    values = (entry.project_id, entry.project_name, *entry.aliases)
    return frozenset(key for value in values if (key := _normalise_key(value)))


def load_registry(path: Path | None = None) -> ProjectRegistry:
    """Load and validate the managed-project registry without mutating it."""
    registry_path = default_registry_path() if path is None else path
    try:
        text = registry_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ProjectRegistryError(f"project registry not found: {registry_path}") from exc
    except OSError as exc:
        raise ProjectRegistryError(f"project registry is not readable: {registry_path}: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectRegistryError(f"project registry is invalid JSON: {registry_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProjectRegistryError("project registry root must be an object")
    schema_version = payload.get("schema_version", 1)
    if schema_version != 1:
        raise ProjectRegistryError(f"unsupported project registry schema_version: {schema_version!r}")
    projects = payload.get("projects")
    if not isinstance(projects, list):
        raise ProjectRegistryError("project registry must contain a projects array")

    entries = tuple(_parse_entry(item, index=index) for index, item in enumerate(projects))

    seen_ids: dict[str, str] = {}
    key_owners: dict[str, str] = {}
    root_owners: dict[Path, str] = {}
    for entry in entries:
        project_id_key = _normalise_key(entry.project_id)
        if project_id_key in seen_ids:
            raise ProjectRegistryError(
                f"duplicate project_id: {entry.project_id!r} conflicts with {seen_ids[project_id_key]!r}"
            )
        seen_ids[project_id_key] = entry.project_id

        for key in _entry_keys(entry):
            owner = key_owners.get(key)
            if owner is not None and owner != entry.project_id:
                raise AmbiguousProjectError(
                    f"project key {key!r} is shared by {owner!r} and {entry.project_id!r}"
                )
            key_owners[key] = entry.project_id

        owner = root_owners.get(entry.root_path)
        if owner is not None and owner != entry.project_id:
            raise AmbiguousProjectError(
                f"project root {entry.root_path} is shared by {owner!r} and {entry.project_id!r}"
            )
        root_owners[entry.root_path] = entry.project_id

    return ProjectRegistry(entries=entries)
