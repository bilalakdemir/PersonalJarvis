"""PM-002 fault-injection regression: fresh SQLite instances and concurrent replay.

All canonical files and journals live under pytest tmp_path. These tests
exercise recovery mechanics, NOT approval provenance or live QA identity.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.projects.models import ProjectRegistryEntry
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.state_store import ProjectStateStore
from jarvis.projects.task_journal import (
    ProjectTaskJournal, TaskRevisionConflict,
)
from jarvis.projects.task_reconciliation import ReconciliationBlocked
from tests.unit.projects.test_task_reconciliation import (
    Verifier, _project, _proposal, _reconciler,
)


@pytest.fixture
def recoverable(tmp_path):
    root = tmp_path / "alpha"
    _project(root, "Alpha")
    registry = ProjectRegistry(entries=(
        ProjectRegistryEntry("alpha", "Alpha", (), root, "active"),
    ))
    db_path = tmp_path / "shared-task-journal.sqlite3"
    journal = ProjectTaskJournal(db_path, registry)
    revision = journal.add_task(
        "alpha", "P-001", expected_revision=0, actor="pm", reason="plan",
    )
    revision = journal.add_task(
        "alpha", "P-002", expected_revision=revision, actor="pm", reason="plan",
    )
    revision = journal.transition(
        "alpha", "P-001", "NEXT", expected_revision=revision,
        actor="pm", reason="prepare",
    )
    revision = journal.transition(
        "alpha", "P-001", "CURRENT", expected_revision=revision,
        actor="pm", reason="start",
    )
    revision = journal.transition(
        "alpha", "P-002", "NEXT", expected_revision=revision,
        actor="pm", reason="prepare",
    )
    original_revision = journal.transition(
        "alpha", "P-001", "VERIFYING", expected_revision=revision,
        actor="pm", reason="verify",
    )
    proposal, approval = _proposal(registry, root)
    return registry, db_path, journal, original_revision, proposal, approval


def _commit(recoverable):
    registry, _, _, _, proposal, approval = recoverable
    result = asyncio.run(
        ProjectStateStore(registry).apply_approved(proposal, approval)
    )
    assert result.status == "COMMITTED"


def _replay(registry, db_path, revision, proposal, approval):
    # Fresh journal object opens brand-new SQLite connections as after restart.
    journal = ProjectTaskJournal(db_path, registry)
    result = _reconciler(registry, journal).recover(
        "alpha", "P-001", "P-002", proposal, approval,
        actor="pm", original_journal_revision=revision,
    )
    return result


def test_restart_after_canonical_commit_is_replay_safe(recoverable):
    registry, db_path, journal, revision, proposal, approval = recoverable
    _commit(recoverable)
    assert journal.snapshot("alpha").active_task == "P-001"
    result = _replay(registry, db_path, revision, proposal, approval)
    assert _replay(registry, db_path, revision, proposal, approval) == result
    fresh = ProjectTaskJournal(db_path, registry)
    assert fresh.snapshot("alpha").active_task == "P-002"
    assert len(fresh.audit("alpha")) == result
    done = [x for x in fresh.audit("alpha") if x.new_state == "DONE"]
    assert len(done) == 1 and done[0].actor == "independent-qa"


def test_restart_between_done_and_next_current_replays_once(recoverable):
    registry, db_path, journal, original, proposal, approval = recoverable
    _commit(recoverable)
    evidence = Verifier().verify(
        project_id="alpha", task_id="P-001",
        canonical_revision=proposal.source_state_revision,
        journal_revision=original,
    )
    journal.transition(
        "alpha", "P-001", "DONE", expected_revision=original,
        actor=evidence.verifier_id, reason="canonical commit verified",
        evidence_ref=_reconciler(registry, journal)._audit_ref(proposal, evidence),
    )
    assert journal.snapshot("alpha").active_task is None
    result = _replay(registry, db_path, original, proposal, approval)
    assert result == original + 2
    assert _replay(registry, db_path, original, proposal, approval) == result
    final = ProjectTaskJournal(db_path, registry)
    assert final.snapshot("alpha").active_task == "P-002"
    assert len([x for x in final.audit("alpha") if x.new_state == "DONE"]) == 1


def test_restart_rejects_untrusted_done_audit(recoverable):
    registry, db_path, journal, original, proposal, approval = recoverable
    _commit(recoverable)
    journal.transition(
        "alpha", "P-001", "DONE", expected_revision=original,
        actor="pm", reason="untrusted fake completion",
        evidence_ref="fake",
    )
    with pytest.raises(ReconciliationBlocked, match="audit"):
        _replay(registry, db_path, original, proposal, approval)
    assert ProjectTaskJournal(db_path, registry).snapshot("alpha").active_task is None


def test_two_simultaneous_recovery_attempts_do_not_duplicate_completion(recoverable):
    registry, db_path, _, original, proposal, approval = recoverable
    _commit(recoverable)

    def attempt(_):
        try:
            return ("ok", _replay(registry, db_path, original, proposal, approval))
        except (TaskRevisionConflict, ReconciliationBlocked) as exc:
            return ("rejected", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(attempt, range(2)))
    assert any(status == "ok" for status, _ in answers), answers
    fresh = ProjectTaskJournal(db_path, registry)
    assert fresh.snapshot("alpha").active_task == "P-002"
    assert fresh.snapshot("alpha").revision == original + 2
    assert len([x for x in fresh.audit("alpha") if x.new_state == "DONE"]) == 1
