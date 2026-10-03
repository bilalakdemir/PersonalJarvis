"""Governed release lifecycle for Personal Jarvis.

This module is deliberately separate from the existing updater.  The updater
owns transport, checksum/tag verification and the mechanics of applying an
already-selected release.  This module owns the higher-level authority around
that mechanism: immutable release identity, verification state, explicit
production promotion, validation, known-good retention and rollback policy.

No function in this module downloads code, switches a checkout, restores user
data or executes a migration.  It records whether those actions are authorized
and preserves the evidence required to make or audit the decision.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir

STATE_SCHEMA = 1
DEFAULT_VALIDATION_DAYS = 7

# These signals protect the systems whose corruption would make "seven days
# elapsed" meaningless.  Optional runtime domains can explicitly report N/A.
CORE_VALIDATION_SIGNALS = frozenset(
    {
        "startup",
        "event_bus",
        "approval_correctness",
        "project_state_integrity",
        "memory_integrity",
    }
)
OPTIONAL_VALIDATION_SIGNALS = frozenset(
    {
        "tool_execution",
        "computer_use",
        "voice_realtime",
        "hud_synchronization",
        "performance",
    }
)
ALL_VALIDATION_SIGNALS = CORE_VALIDATION_SIGNALS | OPTIONAL_VALIDATION_SIGNALS

EMERGENCY_ROLLBACK_REASONS = frozenset(
    {
        "startup_failure",
        "crash_loop",
        "fatal_runtime_initialization",
        "failed_health_check_preventing_operation",
        "failed_deployment_switch",
    }
)


class ReleaseGovernanceError(RuntimeError):
    """A release-governance invariant would be violated."""


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReleaseStage(str, Enum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    STAGED = "staged"
    PRODUCTION_VALIDATING = "production_validating"
    KNOWN_GOOD = "known_good"
    DEGRADED = "degraded"
    FAILED = "failed"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class SignalState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class RollbackPolicy(str, Enum):
    AUTOMATIC_ALLOWED = "automatic_allowed"
    APPROVAL_REQUIRED = "approval_required"
    DESTRUCTIVE_RESTORE_APPROVAL_REQUIRED = "destructive_restore_approval_required"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ReleaseGovernanceError("release timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseGovernanceError(f"invalid release timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ReleaseGovernanceError("release timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass
class JarvisReleaseManifest:
    """Immutable release identity plus mutable governance evidence."""

    release_id: str
    created_at: str
    upstream_version: str
    upstream_commit: str
    jarvis_commit: str
    build_id: str
    dependency_lock_hash: str
    project_state_schema_version: str
    memory_schema_version: str
    configuration_schema_version: str
    risk: RiskLevel = RiskLevel.MEDIUM

    previous_known_good_release: str | None = None
    test_report: dict[str, Any] = field(default_factory=dict)
    migration_report: dict[str, Any] = field(default_factory=dict)
    known_issues: list[str] = field(default_factory=list)
    integrity_verified: bool = False

    stage: ReleaseStage = ReleaseStage.CANDIDATE
    validation_started_at: str | None = None
    validation_completed_at: str | None = None
    validation_window_days: int = DEFAULT_VALIDATION_DAYS
    validation_signals: dict[str, SignalState] = field(default_factory=dict)
    rollback_reason: str | None = None

    def __post_init__(self) -> None:
        required = {
            "release_id": self.release_id,
            "created_at": self.created_at,
            "upstream_version": self.upstream_version,
            "upstream_commit": self.upstream_commit,
            "jarvis_commit": self.jarvis_commit,
            "build_id": self.build_id,
            "dependency_lock_hash": self.dependency_lock_hash,
            "project_state_schema_version": self.project_state_schema_version,
            "memory_schema_version": self.memory_schema_version,
            "configuration_schema_version": self.configuration_schema_version,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ReleaseGovernanceError(
                "release manifest is missing required identity fields: " + ", ".join(missing)
            )
        _parse_iso(self.created_at)
        if self.validation_window_days < 1:
            raise ReleaseGovernanceError("validation window must be at least one day")

    @property
    def identity(self) -> tuple[str, ...]:
        """Fields that must never change for an existing release ID."""
        return (
            self.release_id,
            self.created_at,
            self.upstream_version,
            self.upstream_commit,
            self.jarvis_commit,
            self.build_id,
            self.dependency_lock_hash,
            self.project_state_schema_version,
            self.memory_schema_version,
            self.configuration_schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "created_at": self.created_at,
            "upstream_version": self.upstream_version,
            "upstream_commit": self.upstream_commit,
            "jarvis_commit": self.jarvis_commit,
            "build_id": self.build_id,
            "dependency_lock_hash": self.dependency_lock_hash,
            "project_state_schema_version": self.project_state_schema_version,
            "memory_schema_version": self.memory_schema_version,
            "configuration_schema_version": self.configuration_schema_version,
            "risk": self.risk.value,
            "previous_known_good_release": self.previous_known_good_release,
            "test_report": self.test_report,
            "migration_report": self.migration_report,
            "known_issues": list(self.known_issues),
            "integrity_verified": self.integrity_verified,
            "stage": self.stage.value,
            "validation_started_at": self.validation_started_at,
            "validation_completed_at": self.validation_completed_at,
            "validation_window_days": self.validation_window_days,
            "validation_signals": {
                name: state.value for name, state in sorted(self.validation_signals.items())
            },
            "rollback_reason": self.rollback_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JarvisReleaseManifest":
        try:
            signals = {
                str(name): SignalState(str(state))
                for name, state in dict(payload.get("validation_signals") or {}).items()
            }
            return cls(
                release_id=str(payload["release_id"]),
                created_at=str(payload["created_at"]),
                upstream_version=str(payload["upstream_version"]),
                upstream_commit=str(payload["upstream_commit"]),
                jarvis_commit=str(payload["jarvis_commit"]),
                build_id=str(payload["build_id"]),
                dependency_lock_hash=str(payload["dependency_lock_hash"]),
                project_state_schema_version=str(payload["project_state_schema_version"]),
                memory_schema_version=str(payload["memory_schema_version"]),
                configuration_schema_version=str(payload["configuration_schema_version"]),
                risk=RiskLevel(str(payload.get("risk", RiskLevel.MEDIUM.value))),
                previous_known_good_release=payload.get("previous_known_good_release"),
                test_report=dict(payload.get("test_report") or {}),
                migration_report=dict(payload.get("migration_report") or {}),
                known_issues=[str(item) for item in payload.get("known_issues") or []],
                integrity_verified=bool(payload.get("integrity_verified", False)),
                stage=ReleaseStage(str(payload.get("stage", ReleaseStage.CANDIDATE.value))),
                validation_started_at=payload.get("validation_started_at"),
                validation_completed_at=payload.get("validation_completed_at"),
                validation_window_days=int(
                    payload.get("validation_window_days", DEFAULT_VALIDATION_DAYS)
                ),
                validation_signals=signals,
                rollback_reason=payload.get("rollback_reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReleaseGovernanceError("invalid release manifest payload") from exc


@dataclass
class ReleaseGovernanceState:
    schema: int = STATE_SCHEMA
    current_production: str | None = None
    previous_known_good: str | None = None
    manifests: dict[str, JarvisReleaseManifest] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "current_production": self.current_production,
            "previous_known_good": self.previous_known_good,
            "manifests": {
                release_id: manifest.to_dict()
                for release_id, manifest in sorted(self.manifests.items())
            },
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReleaseGovernanceState":
        if payload.get("schema") != STATE_SCHEMA:
            raise ReleaseGovernanceError("unsupported release-governance state schema")
        raw_manifests = payload.get("manifests")
        if not isinstance(raw_manifests, dict):
            raise ReleaseGovernanceError("release-governance manifests must be an object")
        manifests: dict[str, JarvisReleaseManifest] = {}
        for release_id, raw_manifest in raw_manifests.items():
            if not isinstance(raw_manifest, dict):
                raise ReleaseGovernanceError("release manifest entry must be an object")
            manifest = JarvisReleaseManifest.from_dict(raw_manifest)
            if manifest.release_id != release_id:
                raise ReleaseGovernanceError("release manifest key does not match release_id")
            manifests[release_id] = manifest

        state = cls(
            schema=STATE_SCHEMA,
            current_production=payload.get("current_production"),
            previous_known_good=payload.get("previous_known_good"),
            manifests=manifests,
            history=list(payload.get("history") or []),
        )
        for release_id in (state.current_production, state.previous_known_good):
            if release_id is not None and release_id not in manifests:
                raise ReleaseGovernanceError(
                    "release-governance state points at an unknown release"
                )
        return state


class ReleaseGovernanceStore:
    """Durable release authority with atomic JSON persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            path
            if path is not None
            else user_data_dir() / "release-governance" / "state.json"
        )

    def load(self) -> ReleaseGovernanceState:
        if not self.path.exists():
            return ReleaseGovernanceState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseGovernanceError("could not read release-governance state") from exc
        if not isinstance(payload, dict):
            raise ReleaseGovernanceError("release-governance state must be an object")
        return ReleaseGovernanceState.from_dict(payload)

    def save(self, state: ReleaseGovernanceState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ReleaseGovernanceError("could not persist release-governance state") from exc

    def add_candidate(self, manifest: JarvisReleaseManifest) -> ReleaseGovernanceState:
        state = self.load()
        existing = state.manifests.get(manifest.release_id)
        if existing is not None:
            if existing.identity != manifest.identity:
                raise ReleaseGovernanceError(
                    "release_id already exists with different immutable identity"
                )
            raise ReleaseGovernanceError("release_id already exists")
        state.manifests[manifest.release_id] = manifest
        self._event(state, manifest.release_id, "candidate_added")
        self.save(state)
        return state

    def bootstrap_known_good(self, manifest: JarvisReleaseManifest) -> ReleaseGovernanceState:
        """Register the pre-governance baseline without pretending it was promoted."""
        state = self.load()
        if state.current_production is not None:
            raise ReleaseGovernanceError("current production is already registered")
        if manifest.release_id in state.manifests:
            raise ReleaseGovernanceError("release_id already exists")
        manifest.integrity_verified = True
        manifest.stage = ReleaseStage.KNOWN_GOOD
        state.manifests[manifest.release_id] = manifest
        state.current_production = manifest.release_id
        self._event(state, manifest.release_id, "baseline_registered")
        self.save(state)
        return state

    def record_verification(
        self,
        release_id: str,
        *,
        tests_passed: bool,
        integrity_verified: bool,
        test_report: dict[str, Any],
        migration_report: dict[str, Any] | None = None,
        known_issues: list[str] | None = None,
    ) -> ReleaseGovernanceState:
        state = self.load()
        manifest = self._manifest(state, release_id)
        if manifest.stage not in {ReleaseStage.CANDIDATE, ReleaseStage.FAILED}:
            raise ReleaseGovernanceError(
                f"cannot verify release while it is {manifest.stage.value}"
            )
        manifest.test_report = dict(test_report)
        manifest.migration_report = dict(migration_report or {})
        manifest.known_issues = list(known_issues or [])
        manifest.integrity_verified = integrity_verified
        manifest.stage = (
            ReleaseStage.VERIFIED
            if tests_passed and integrity_verified
            else ReleaseStage.FAILED
        )
        self._event(
            state,
            release_id,
            "verification_passed" if manifest.stage is ReleaseStage.VERIFIED else "verification_failed",
        )
        self.save(state)
        return state

    def stage(self, release_id: str) -> ReleaseGovernanceState:
        state = self.load()
        manifest = self._manifest(state, release_id)
        if manifest.stage is not ReleaseStage.VERIFIED or not manifest.integrity_verified:
            raise ReleaseGovernanceError("only an integrity-verified release can be staged")
        manifest.stage = ReleaseStage.STAGED
        self._event(state, release_id, "staged")
        self.save(state)
        return state

    def reject(self, release_id: str, *, reason: str) -> ReleaseGovernanceState:
        state = self.load()
        manifest = self._manifest(state, release_id)
        if release_id in {state.current_production, state.previous_known_good}:
            raise ReleaseGovernanceError("current or previous known-good release cannot be rejected")
        manifest.stage = ReleaseStage.REJECTED
        self._event(state, release_id, "rejected", reason=reason)
        self.save(state)
        return state

    def promotion_summary(self, release_id: str) -> dict[str, Any]:
        state = self.load()
        manifest = self._manifest(state, release_id)
        current = (
            state.manifests.get(state.current_production)
            if state.current_production is not None
            else None
        )
        return {
            "current_release": state.current_production,
            "current_upstream_version": (
                current.upstream_version if current is not None else None
            ),
            "proposed_release": manifest.release_id,
            "proposed_upstream_version": manifest.upstream_version,
            "upstream_commit": manifest.upstream_commit,
            "jarvis_commit": manifest.jarvis_commit,
            "risk": manifest.risk.value,
            "verification_passed": manifest.stage is ReleaseStage.STAGED,
            "test_report": manifest.test_report,
            "known_issues": list(manifest.known_issues),
            "migration_report": manifest.migration_report,
            "rollback_target": state.current_production,
        }

    def promote(
        self,
        release_id: str,
        *,
        approved_release_id: str,
        now: datetime | None = None,
    ) -> ReleaseGovernanceState:
        """Record exact explicit production authorization.

        The caller must pass back the exact release ID that was shown for
        approval.  This prevents a stale approval from authorizing a different
        candidate that appeared later.
        """
        if approved_release_id != release_id:
            raise ReleaseGovernanceError("promotion approval does not match release_id")
        state = self.load()
        manifest = self._manifest(state, release_id)
        if manifest.stage is not ReleaseStage.STAGED:
            raise ReleaseGovernanceError("only a staged release can be promoted")
        if not manifest.integrity_verified:
            raise ReleaseGovernanceError("release integrity is not verified")
        if state.current_production == release_id:
            raise ReleaseGovernanceError("release is already current production")

        previous = state.current_production
        manifest.previous_known_good_release = previous
        manifest.stage = ReleaseStage.PRODUCTION_VALIDATING
        manifest.validation_started_at = _iso(now or _utc_now())
        manifest.validation_completed_at = None
        manifest.validation_signals = {}
        state.previous_known_good = previous
        state.current_production = release_id
        self._event(state, release_id, "production_promoted", previous=previous)
        self.save(state)
        return state

    def record_validation_signal(
        self,
        release_id: str,
        signal: str,
        result: SignalState,
        *,
        detail: str | None = None,
    ) -> ReleaseGovernanceState:
        state = self.load()
        manifest = self._manifest(state, release_id)
        if release_id != state.current_production:
            raise ReleaseGovernanceError("validation signals apply only to current production")
        if manifest.stage not in {
            ReleaseStage.PRODUCTION_VALIDATING,
            ReleaseStage.DEGRADED,
        }:
            raise ReleaseGovernanceError(
                f"release is not in validation: {manifest.stage.value}"
            )
        if signal not in ALL_VALIDATION_SIGNALS:
            raise ReleaseGovernanceError(f"unknown validation signal: {signal}")
        if signal in CORE_VALIDATION_SIGNALS and result is SignalState.NOT_APPLICABLE:
            raise ReleaseGovernanceError(f"core validation signal cannot be N/A: {signal}")

        manifest.validation_signals[signal] = result
        if result is SignalState.FAIL:
            manifest.stage = ReleaseStage.DEGRADED
        self._event(
            state,
            release_id,
            "validation_signal",
            signal=signal,
            result=result.value,
            detail=detail,
        )
        self.save(state)
        return state

    def validation_readiness(
        self,
        release_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[bool, list[str]]:
        state = self.load()
        manifest = self._manifest(state, release_id)
        reasons: list[str] = []
        if release_id != state.current_production:
            reasons.append("release is not current production")
        if manifest.stage is not ReleaseStage.PRODUCTION_VALIDATING:
            reasons.append(f"release stage is {manifest.stage.value}")
        if manifest.validation_started_at is None:
            reasons.append("validation has not started")
        else:
            started = _parse_iso(manifest.validation_started_at)
            eligible_at = started + timedelta(days=manifest.validation_window_days)
            if (now or _utc_now()).astimezone(timezone.utc) < eligible_at:
                reasons.append("validation window has not elapsed")

        for signal in sorted(CORE_VALIDATION_SIGNALS):
            if manifest.validation_signals.get(signal) is not SignalState.PASS:
                reasons.append(f"core validation signal is not passing: {signal}")
        for signal in sorted(OPTIONAL_VALIDATION_SIGNALS):
            if signal not in manifest.validation_signals:
                reasons.append(f"validation signal has not been assessed: {signal}")
            elif manifest.validation_signals[signal] is SignalState.FAIL:
                reasons.append(f"validation signal failed: {signal}")
        return not reasons, reasons

    def complete_validation(
        self,
        release_id: str,
        *,
        now: datetime | None = None,
    ) -> ReleaseGovernanceState:
        ready, reasons = self.validation_readiness(release_id, now=now)
        if not ready:
            raise ReleaseGovernanceError(
                "release is not ready to become known-good: " + "; ".join(reasons)
            )
        state = self.load()
        manifest = self._manifest(state, release_id)
        manifest.stage = ReleaseStage.KNOWN_GOOD
        manifest.validation_completed_at = _iso(now or _utc_now())
        self._event(state, release_id, "validation_completed")
        self.save(state)
        return state

    def mark_failed(self, release_id: str, *, reason: str) -> ReleaseGovernanceState:
        state = self.load()
        manifest = self._manifest(state, release_id)
        manifest.stage = ReleaseStage.FAILED
        self._event(state, release_id, "release_failed", reason=reason)
        self.save(state)
        return state

    @staticmethod
    def rollback_policy(
        reason: str,
        *,
        requires_state_restore: bool = False,
    ) -> RollbackPolicy:
        if requires_state_restore:
            return RollbackPolicy.DESTRUCTIVE_RESTORE_APPROVAL_REQUIRED
        if reason in EMERGENCY_ROLLBACK_REASONS:
            return RollbackPolicy.AUTOMATIC_ALLOWED
        return RollbackPolicy.APPROVAL_REQUIRED

    def record_rollback(
        self,
        release_id: str,
        *,
        reason: str,
        approved: bool = False,
        requires_state_restore: bool = False,
    ) -> ReleaseGovernanceState:
        """Record a code rollback after the deployment mechanism switched versions.

        This method never restores data.  If user-state restoration is required,
        explicit approval is mandatory even for an emergency code rollback.
        """
        state = self.load()
        manifest = self._manifest(state, release_id)
        if state.current_production != release_id:
            raise ReleaseGovernanceError("only current production can be rolled back")
        target = state.previous_known_good
        if target is None:
            raise ReleaseGovernanceError("no previous known-good release is available")

        policy = self.rollback_policy(
            reason, requires_state_restore=requires_state_restore
        )
        if policy is not RollbackPolicy.AUTOMATIC_ALLOWED and not approved:
            raise ReleaseGovernanceError("rollback requires explicit approval")

        manifest.stage = ReleaseStage.ROLLED_BACK
        manifest.rollback_reason = reason
        state.current_production = target
        prior = state.manifests[target].previous_known_good_release
        state.previous_known_good = prior
        self._event(
            state,
            release_id,
            "rolled_back",
            target=target,
            reason=reason,
            policy=policy.value,
        )
        self.save(state)
        return state

    def cleanup_eligible(self, release_id: str) -> bool:
        """Whether a full runnable artifact may be removed.

        Lightweight manifest/history records are never deleted by this decision.
        """
        state = self.load()
        manifest = self._manifest(state, release_id)
        if release_id in {state.current_production, state.previous_known_good}:
            return False
        return manifest.stage in {
            ReleaseStage.KNOWN_GOOD,
            ReleaseStage.REJECTED,
            ReleaseStage.FAILED,
            ReleaseStage.ROLLED_BACK,
        }

    @staticmethod
    def _manifest(
        state: ReleaseGovernanceState, release_id: str
    ) -> JarvisReleaseManifest:
        try:
            return state.manifests[release_id]
        except KeyError as exc:
            raise ReleaseGovernanceError(f"unknown release_id: {release_id}") from exc

    @staticmethod
    def _event(
        state: ReleaseGovernanceState,
        release_id: str,
        action: str,
        **details: Any,
    ) -> None:
        event: dict[str, Any] = {
            "at": _iso(_utc_now()),
            "release_id": release_id,
            "action": action,
        }
        event.update({key: value for key, value in details.items() if value is not None})
        state.history.append(event)
