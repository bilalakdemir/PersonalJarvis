from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jarvis.core.release_governance import (
    ALL_VALIDATION_SIGNALS,
    CORE_VALIDATION_SIGNALS,
    JarvisReleaseManifest,
    ReleaseGovernanceError,
    ReleaseGovernanceStore,
    ReleaseStage,
    RiskLevel,
    RollbackPolicy,
    SignalState,
)


T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _manifest(release_id: str, *, commit: str | None = None) -> JarvisReleaseManifest:
    return JarvisReleaseManifest(
        release_id=release_id,
        created_at=T0.isoformat(),
        upstream_version="2.4.0",
        upstream_commit="u-" + release_id,
        jarvis_commit=commit or ("j-" + release_id),
        build_id="build-" + release_id,
        dependency_lock_hash="lock-" + release_id,
        project_state_schema_version="1",
        memory_schema_version="1",
        configuration_schema_version="1",
        risk=RiskLevel.MEDIUM,
    )


def _store(tmp_path: Path) -> ReleaseGovernanceStore:
    return ReleaseGovernanceStore(tmp_path / "release-state.json")


def _verify_and_stage(store: ReleaseGovernanceStore, release_id: str) -> None:
    store.record_verification(
        release_id,
        tests_passed=True,
        integrity_verified=True,
        test_report={"ci": "pass", "critical_tests": 700},
        migration_report={"backward_compatible": True},
    )
    store.stage(release_id)


def _record_green_validation(
    store: ReleaseGovernanceStore, release_id: str
) -> None:
    for signal in sorted(ALL_VALIDATION_SIGNALS):
        result = (
            SignalState.PASS
            if signal in CORE_VALIDATION_SIGNALS
            else SignalState.NOT_APPLICABLE
        )
        store.record_validation_signal(release_id, signal, result)


def test_candidate_identity_persists_and_cannot_be_reused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = _manifest("J-13")
    store.add_candidate(candidate)

    loaded = store.load().manifests["J-13"]
    assert loaded.identity == candidate.identity
    assert loaded.stage is ReleaseStage.CANDIDATE

    with pytest.raises(ReleaseGovernanceError, match="already exists"):
        store.add_candidate(_manifest("J-13"))

    changed = _manifest("J-13", commit="different")
    with pytest.raises(ReleaseGovernanceError, match="different immutable identity"):
        store.add_candidate(changed)


def test_invalid_or_failed_verification_cannot_stage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_candidate(_manifest("J-13"))

    with pytest.raises(ReleaseGovernanceError, match="integrity-verified"):
        store.stage("J-13")

    state = store.record_verification(
        "J-13",
        tests_passed=True,
        integrity_verified=False,
        test_report={"ci": "pass"},
    )
    assert state.manifests["J-13"].stage is ReleaseStage.FAILED

    with pytest.raises(ReleaseGovernanceError, match="integrity-verified"):
        store.stage("J-13")


def test_promotion_is_bound_to_exact_approved_release_and_retains_rollback(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")

    summary = store.promotion_summary("J-13")
    assert summary["current_release"] == "J-12"
    assert summary["proposed_release"] == "J-13"
    assert summary["rollback_target"] == "J-12"
    assert summary["verification_passed"] is True

    with pytest.raises(ReleaseGovernanceError, match="does not match"):
        store.promote("J-13", approved_release_id="J-14", now=T0)

    state = store.promote("J-13", approved_release_id="J-13", now=T0)
    assert state.current_production == "J-13"
    assert state.previous_known_good == "J-12"
    assert state.manifests["J-13"].stage is ReleaseStage.PRODUCTION_VALIDATING
    assert state.manifests["J-13"].previous_known_good_release == "J-12"


def test_elapsed_time_alone_never_completes_validation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)

    with pytest.raises(ReleaseGovernanceError, match="signal"):
        store.complete_validation("J-13", now=T0 + timedelta(days=8))


def test_validation_requires_window_and_all_signal_assessments(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)
    _record_green_validation(store, "J-13")

    ready, reasons = store.validation_readiness(
        "J-13", now=T0 + timedelta(days=6, hours=23)
    )
    assert ready is False
    assert "validation window has not elapsed" in reasons

    state = store.complete_validation("J-13", now=T0 + timedelta(days=7))
    assert state.manifests["J-13"].stage is ReleaseStage.KNOWN_GOOD
    assert state.manifests["J-13"].validation_completed_at is not None
    # The previous runnable release remains protected after validation.
    assert state.previous_known_good == "J-12"


def test_failed_validation_signal_marks_release_degraded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)

    state = store.record_validation_signal(
        "J-13", "event_bus", SignalState.FAIL, detail="subscriber crash loop"
    )
    assert state.manifests["J-13"].stage is ReleaseStage.DEGRADED

    with pytest.raises(ReleaseGovernanceError, match="stage is degraded"):
        store.complete_validation("J-13", now=T0 + timedelta(days=8))


def test_core_signal_cannot_be_marked_not_applicable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)

    with pytest.raises(ReleaseGovernanceError, match="cannot be N/A"):
        store.record_validation_signal(
            "J-13", "memory_integrity", SignalState.NOT_APPLICABLE
        )


def test_rollback_policy_separates_emergency_degradation_and_data_restore() -> None:
    assert (
        ReleaseGovernanceStore.rollback_policy("crash_loop")
        is RollbackPolicy.AUTOMATIC_ALLOWED
    )
    assert (
        ReleaseGovernanceStore.rollback_policy("performance_regression")
        is RollbackPolicy.APPROVAL_REQUIRED
    )
    assert (
        ReleaseGovernanceStore.rollback_policy(
            "crash_loop", requires_state_restore=True
        )
        is RollbackPolicy.DESTRUCTIVE_RESTORE_APPROVAL_REQUIRED
    )


def test_bounded_emergency_rollback_restores_previous_known_good(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)

    state = store.record_rollback("J-13", reason="startup_failure")
    assert state.current_production == "J-12"
    assert state.manifests["J-13"].stage is ReleaseStage.ROLLED_BACK
    assert state.manifests["J-13"].rollback_reason == "startup_failure"


def test_noncritical_and_state_restore_rollbacks_require_approval(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-12"))
    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote("J-13", approved_release_id="J-13", now=T0)

    with pytest.raises(ReleaseGovernanceError, match="explicit approval"):
        store.record_rollback("J-13", reason="performance_regression")

    with pytest.raises(ReleaseGovernanceError, match="explicit approval"):
        store.record_rollback(
            "J-13", reason="crash_loop", requires_state_restore=True
        )

    state = store.record_rollback(
        "J-13",
        reason="performance_regression",
        approved=True,
    )
    assert state.current_production == "J-12"


def test_known_good_rotation_makes_only_old_unreferenced_artifact_cleanup_eligible(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bootstrap_known_good(_manifest("J-11"))

    store.add_candidate(_manifest("J-12"))
    _verify_and_stage(store, "J-12")
    store.promote("J-12", approved_release_id="J-12", now=T0)
    _record_green_validation(store, "J-12")
    store.complete_validation("J-12", now=T0 + timedelta(days=7))

    store.add_candidate(_manifest("J-13"))
    _verify_and_stage(store, "J-13")
    store.promote(
        "J-13",
        approved_release_id="J-13",
        now=T0 + timedelta(days=8),
    )

    state = store.load()
    assert state.current_production == "J-13"
    assert state.previous_known_good == "J-12"
    assert store.cleanup_eligible("J-13") is False
    assert store.cleanup_eligible("J-12") is False
    assert store.cleanup_eligible("J-11") is True


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "release-state.json"
    path.write_text('{"schema": 999, "manifests": {}}\n', encoding="utf-8")
    store = ReleaseGovernanceStore(path)

    with pytest.raises(ReleaseGovernanceError, match="unsupported"):
        store.load()
