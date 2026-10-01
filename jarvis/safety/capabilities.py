"""Pure capability-grant policy for delegated tool execution.

This module deliberately performs no I/O. Filesystem checks are lexical only;
the concrete filesystem boundary must still resolve symlinks/real paths before
performing an actual mutation.
"""
from __future__ import annotations

import ntpath
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

_ALLOWED_REQUIREMENT_KEYS = frozenset(
    {"read_paths", "write_paths", "network", "external_services", "credentials"}
)
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class CapabilityRequirementsError(ValueError):
    """Raised when a tool declares malformed capability requirements."""


class CapabilityDenyCode(StrEnum):
    MISSING_GRANT = "missing_grant"
    INVALID_GRANT = "invalid_grant"
    EXPIRED = "expired"
    TOOL_NOT_GRANTED = "tool_not_granted"
    PROJECT_NOT_GRANTED = "project_not_granted"
    TASK_NOT_GRANTED = "task_not_granted"
    READ_PATH_NOT_GRANTED = "read_path_not_granted"
    WRITE_PATH_NOT_GRANTED = "write_path_not_granted"
    NETWORK_NOT_GRANTED = "network_not_granted"
    SERVICE_NOT_GRANTED = "service_not_granted"
    CREDENTIAL_NOT_GRANTED = "credential_not_granted"
    MALFORMED_REQUIREMENTS = "malformed_requirements"
    CHILD_WIDENS_PARENT = "child_widens_parent"


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Immutable, explicit authority delegated to one execution scope."""

    grant_id: str
    tools: frozenset[str]
    expires_at: datetime
    project_ids: frozenset[str] = field(default_factory=frozenset)
    task_ids: frozenset[str] = field(default_factory=frozenset)
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    allow_network: bool = False
    external_services: frozenset[str] = field(default_factory=frozenset)
    credentials: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CapabilityRequirements:
    """Authority required by one concrete tool call."""

    tool_name: str
    project_id: str | None = None
    task_id: str | None = None
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    network: bool = False
    external_services: frozenset[str] = field(default_factory=frozenset)
    credentials: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    code: CapabilityDenyCode | None = None
    reason: str = ""


def _deny(code: CapabilityDenyCode, reason: str) -> CapabilityDecision:
    return CapabilityDecision(False, code, reason)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _validate_names(values: frozenset[str], label: str) -> str | None:
    for value in values:
        if not isinstance(value, str) or not value.strip() or value.strip() == "*":
            return f"{label} must contain explicit non-empty names"
    return None


def _path_module(path: str):
    if _WINDOWS_ABS.match(path) or path.startswith("\\\\"):
        return ntpath
    return posixpath


def _normalize_absolute_path(path: str) -> tuple[Any, str]:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("path must be a non-empty string")
    raw = path.strip()
    module = _path_module(raw)
    if module is ntpath:
        parts = raw.replace("/", "\\").split("\\")
    else:
        parts = raw.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("path traversal is not permitted")
    normalized = module.normpath(raw)
    if not module.isabs(normalized):
        raise ValueError("path must be absolute")
    if module is ntpath:
        normalized = module.normcase(normalized)
    return module, normalized


def path_is_within_root(path: str, root: str) -> bool:
    """Lexical containment only; callers doing I/O must also resolve symlinks."""

    try:
        path_module, normalized_path = _normalize_absolute_path(path)
        root_module, normalized_root = _normalize_absolute_path(root)
    except ValueError:
        return False
    if path_module is not root_module:
        return False
    try:
        common = path_module.commonpath([normalized_path, normalized_root])
    except ValueError:
        return False
    if path_module is ntpath:
        return ntpath.normcase(common) == ntpath.normcase(normalized_root)
    return common == normalized_root


def path_is_direct_child(path: str, root: str) -> bool:
    try:
        path_module, normalized_path = _normalize_absolute_path(path)
        root_module, normalized_root = _normalize_absolute_path(root)
    except ValueError:
        return False
    if path_module is not root_module:
        return False
    return path_module.dirname(normalized_path) == normalized_root


def _validate_grant(grant: CapabilityGrant) -> str | None:
    if not isinstance(grant.grant_id, str) or not grant.grant_id.strip():
        return "grant_id must be non-empty"
    if not isinstance(grant.expires_at, datetime) or not _aware(grant.expires_at):
        return "expires_at must be timezone-aware"
    for values, label in (
        (grant.tools, "tools"),
        (grant.project_ids, "project_ids"),
        (grant.task_ids, "task_ids"),
        (grant.external_services, "external_services"),
        (grant.credentials, "credentials"),
    ):
        problem = _validate_names(values, label)
        if problem:
            return problem
    for root in (*grant.read_roots, *grant.write_roots):
        try:
            _normalize_absolute_path(root)
        except ValueError as exc:
            return f"invalid grant root {root!r}: {exc}"
    return None


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise CapabilityRequirementsError(f"{label} must be a list or tuple")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CapabilityRequirementsError(f"{label} entries must be non-empty strings")
        out.append(item)
    return tuple(out)


def _string_set(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise CapabilityRequirementsError(f"{label} must be a string collection")
    out: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or item.strip() == "*":
            raise CapabilityRequirementsError(
                f"{label} entries must be explicit non-empty names"
            )
        out.add(item)
    return frozenset(out)


def requirements_for_tool(
    tool: Any,
    args: dict[str, Any],
    *,
    project_id: str | None = None,
    task_id: str | None = None,
) -> CapabilityRequirements:
    """Build requirements from one optional, explicit per-tool hook."""

    tool_name = str(getattr(tool, "name", "") or "").strip()
    if not tool_name:
        raise CapabilityRequirementsError("tool name is empty")
    hook = getattr(tool, "capability_requirements_for_args", None)
    raw: Mapping[str, Any] = {}
    if callable(hook):
        try:
            declared = hook(dict(args))
        except Exception as exc:
            raise CapabilityRequirementsError(
                f"capability requirement hook failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(declared, Mapping):
            raise CapabilityRequirementsError("capability requirement hook must return a mapping")
        unknown = set(declared) - _ALLOWED_REQUIREMENT_KEYS
        if unknown:
            raise CapabilityRequirementsError(
                f"unknown capability requirement keys: {sorted(unknown)!r}"
            )
        raw = declared

    network = raw.get("network", False)
    if not isinstance(network, bool):
        raise CapabilityRequirementsError("network must be boolean")

    return CapabilityRequirements(
        tool_name=tool_name,
        project_id=project_id,
        task_id=task_id,
        read_paths=_string_tuple(raw.get("read_paths", ()), "read_paths"),
        write_paths=_string_tuple(raw.get("write_paths", ()), "write_paths"),
        network=network,
        external_services=_string_set(raw.get("external_services", ()), "external_services"),
        credentials=_string_set(raw.get("credentials", ()), "credentials"),
    )


def _all_paths_within(paths: tuple[str, ...], roots: tuple[str, ...]) -> bool:
    if not paths:
        return True
    if not roots:
        return False
    return all(any(path_is_within_root(path, root) for root in roots) for path in paths)


def evaluate_capability(
    grant: CapabilityGrant | None,
    requirements: CapabilityRequirements,
    *,
    now: datetime | None = None,
) -> CapabilityDecision:
    """Fail-closed evaluation of one delegated call against one immutable grant."""

    if grant is None:
        return _deny(CapabilityDenyCode.MISSING_GRANT, "delegated call has no grant")
    if not isinstance(grant, CapabilityGrant):
        return _deny(CapabilityDenyCode.INVALID_GRANT, "grant has an invalid type")
    invalid = _validate_grant(grant)
    if invalid:
        return _deny(CapabilityDenyCode.INVALID_GRANT, invalid)

    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or not _aware(current):
        return _deny(CapabilityDenyCode.INVALID_GRANT, "current time must be timezone-aware")
    if grant.expires_at <= current:
        return _deny(CapabilityDenyCode.EXPIRED, "grant has expired")

    if requirements.tool_name not in grant.tools:
        return _deny(CapabilityDenyCode.TOOL_NOT_GRANTED, f"tool {requirements.tool_name!r} is outside the grant")
    if requirements.project_id is not None and requirements.project_id not in grant.project_ids:
        return _deny(CapabilityDenyCode.PROJECT_NOT_GRANTED, f"project {requirements.project_id!r} is outside the grant")
    if requirements.task_id is not None and requirements.task_id not in grant.task_ids:
        return _deny(CapabilityDenyCode.TASK_NOT_GRANTED, f"task {requirements.task_id!r} is outside the grant")

    for path in (*requirements.read_paths, *requirements.write_paths):
        try:
            _normalize_absolute_path(path)
        except ValueError as exc:
            return _deny(CapabilityDenyCode.MALFORMED_REQUIREMENTS, str(exc))
    if not _all_paths_within(requirements.read_paths, grant.read_roots):
        return _deny(CapabilityDenyCode.READ_PATH_NOT_GRANTED, "one or more read paths are outside granted roots")
    if not _all_paths_within(requirements.write_paths, grant.write_roots):
        return _deny(CapabilityDenyCode.WRITE_PATH_NOT_GRANTED, "one or more write paths are outside granted roots")

    needs_network = requirements.network or bool(requirements.external_services)
    if needs_network and not grant.allow_network:
        return _deny(CapabilityDenyCode.NETWORK_NOT_GRANTED, "network access is not granted")
    if not requirements.external_services.issubset(grant.external_services):
        return _deny(CapabilityDenyCode.SERVICE_NOT_GRANTED, "one or more external services are outside the grant")
    if not requirements.credentials.issubset(grant.credentials):
        return _deny(CapabilityDenyCode.CREDENTIAL_NOT_GRANTED, "one or more credentials are outside the grant")
    return CapabilityDecision(True)


def validate_child_grant(parent: CapabilityGrant, child: CapabilityGrant) -> CapabilityDecision:
    """A child may narrow parent authority, never widen it."""

    for candidate, label in ((parent, "parent"), (child, "child")):
        if not isinstance(candidate, CapabilityGrant):
            return _deny(CapabilityDenyCode.INVALID_GRANT, f"{label}: invalid grant type")
        invalid = _validate_grant(candidate)
        if invalid:
            return _deny(CapabilityDenyCode.INVALID_GRANT, f"{label}: {invalid}")

    if child.expires_at > parent.expires_at:
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child expiry exceeds parent")
    if not child.tools.issubset(parent.tools):
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child tools widen parent")
    if not child.project_ids.issubset(parent.project_ids):
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child projects widen parent")
    if not child.task_ids.issubset(parent.task_ids):
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child tasks widen parent")
    if child.allow_network and not parent.allow_network:
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child enables network")
    if not child.external_services.issubset(parent.external_services):
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child services widen parent")
    if not child.credentials.issubset(parent.credentials):
        return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child credentials widen parent")
    for root in child.read_roots:
        if not any(path_is_within_root(root, parent_root) for parent_root in parent.read_roots):
            return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child read roots widen parent")
    for root in child.write_roots:
        if not any(path_is_within_root(root, parent_root) for parent_root in parent.write_roots):
            return _deny(CapabilityDenyCode.CHILD_WIDENS_PARENT, "child write roots widen parent")
    return CapabilityDecision(True)


__all__ = [
    "CapabilityDecision",
    "CapabilityDenyCode",
    "CapabilityGrant",
    "CapabilityRequirements",
    "CapabilityRequirementsError",
    "evaluate_capability",
    "path_is_direct_child",
    "path_is_within_root",
    "requirements_for_tool",
    "validate_child_grant",
]
