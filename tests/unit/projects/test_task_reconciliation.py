"""Approval-bound reconciliation tests: all writes stay in temporary projects."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.projects.models import ProjectRegistryEntry, ProjectStateApproval
from jarvis.projects.proposal import build_project_state_proposal
from jarvis.projects.registry import ProjectRegistry
from jarvis.projects.state_store import ProjectStateStore
from jarvis.projects.task_journal import ProjectTaskJournal
from jarvis.projects.task_reconciliation import (
    CompletionEvidence, ProjectTaskReconciler, ReconciliationBlocked,
)


def _project(root: Path, name: str):
    root.mkdir()
    (root / "PROJECT.md").write_text(
        f"# Project\n{name}\n\n## Main Goal\nDeliver safely.\n", encoding="utf-8")
    (root / "STATE.md").write_text(
        f"# State\nProject: {name}\nPhase: Build\n"
        "\n## CURRENT TASK\nP-001 — Work on milestone\n"
        "\n## Last Completed\nNone.\n"
        "\n## Next Logical Step\nAdvance.\n"
        "\n## Blockers\nNone.\n", encoding="utf-8")
    (root / "TASKS.md").write_text(
        "# Tasks\n\n### CURRENT — P-001 — Work on milestone\n",
        encoding="utf-8")
    (root / "DECISIONS.md").write_text("# Decisions\n", encoding="utf-8")
    (root / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")


class Verifier:
    def __init__(self, *, allowed=True, verifier_id="independent-qa"):
        self.allowed = allowed
        self.verifier_id = verifier_id

    def verify(self, *, project_id, task_id, canonical_revision, journal_revision):
        return CompletionEvidence(
            project_id, task_id, canonical_revision, journal_revision,
            self.verifier_id, "ci:test-evidence", self.allowed,
        )


@pytest.fixture
def scope(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _project(a, "Alpha")
    _project(b, "Beta")
    registry = ProjectRegistry(entries=(
        ProjectRegistryEntry("alpha", "Alpha", (), a, "active"),
        ProjectRegistryEntry("beta", "Beta", (), b, "active"),
    ))
    journal = ProjectTaskJournal(tmp_path / "journal.sqlite3", registry)
    for project in ("alpha", "beta"):
        rev = journal.add_task(project, "P-001", expected_revision=0,
                               actor="pm", reason="plan")
        rev = journal.add_task(project, "P-002", expected_revision=rev,
                               actor="pm", reason="plan")
        rev = journal.transition(project, "P-001", "NEXT",
                                 expected_revision=rev, actor="pm", reason="plan")
        rev = journal.transition(project, "P-001", "CURRENT",
                                 expected_revision=rev, actor="pm", reason="activate")
        rev = journal.transition(project, "P-002", "NEXT",
                                 expected_revision=rev, actor="pm", reason="plan")
        journal.transition(project, "P-001", "VERIFYING",
                           expected_revision=rev, actor="pm", reason="verify")
    return registry, journal, a, b


def _proposal(registry, root, project="alpha"):
    entry = registry.resolve_exact(project)
    replacements = {
        name: (root / name).read_text(encoding="utf-8").replace("P-001", "P-002")
        for name in ("STATE.md", "TASKS.md")
    }
    proposal = build_project_state_proposal(
        entry, replacements, reason="approved verified completion",
        expected_current_task="P-002 — Work on milestone",
        expected_effect="Promote next task after verified completion",
    )
    approval = ProjectStateApproval(proposal.transaction_id, proposal.proposal_digest)
    return proposal, approval


def _reconciler(registry, journal, verifier=None):
    return ProjectTaskReconciler(
        registry, ProjectStateStore(registry), journal, verifier or Verifier()
    )


def test_approved_commit_advances_only_one_project(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    rev = asyncio.run(_reconciler(registry, journal).advance(
        "alpha", "P-001", "P-002", proposal, approval, actor="pm"))
    assert rev == journal.snapshot("alpha").revision
    assert journal.snapshot("alpha").active_task == "P-002"
    assert journal.snapshot("beta").active_task == "P-001"
    audit = journal.audit("alpha")
    assert audit[-2].new_state == "DONE"
    assert audit[-2].actor == "independent-qa"
    assert audit[-2].evidence_ref.startswith("approved:")
    assert "ci:test-evidence" not in audit[-2].evidence_ref
    assert journal.snapshot("alpha").revision == 8


@pytest.mark.parametrize("verifier", [
    Verifier(allowed=False), Verifier(verifier_id="pm"),
])
def test_verifier_denied_before_canonical_write(scope, verifier):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    before = (a / "STATE.md").read_bytes()
    with pytest.raises(ReconciliationBlocked, match="verification"):
        asyncio.run(_reconciler(registry, journal, verifier).advance(
            "alpha", "P-001", "P-002", proposal, approval, actor="pm"))
    assert (a / "STATE.md").read_bytes() == before
    assert journal.snapshot("alpha").active_task == "P-001"


def test_mismatched_approval_fails_closed(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    wrong = ProjectStateApproval(approval.transaction_id, "0" * 64)
    with pytest.raises(ReconciliationBlocked, match="approval"):
        asyncio.run(_reconciler(registry, journal).advance(
            "alpha", "P-001", "P-002", proposal, wrong, actor="pm"))
    assert journal.snapshot("alpha").active_task == "P-001"


def test_precommit_drift_fails_closed(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    (a / "STATE.md").write_text(
        (a / "STATE.md").read_text(encoding="utf-8") + "\nExtra drift.\n",
        encoding="utf-8",
    )
    with pytest.raises(ReconciliationBlocked, match="pre-state"):
        asyncio.run(_reconciler(registry, journal).advance(
            "alpha", "P-001", "P-002", proposal, approval, actor="pm"))


def test_recover_after_crash_between_canonical_commit_and_journal(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    original = journal.snapshot("alpha").revision
    result = asyncio.run(ProjectStateStore(registry).apply_approved(proposal, approval))
    assert result.status == "COMMITTED"
    reconciler = _reconciler(registry, journal)
    final = reconciler.recover(
        "alpha", "P-001", "P-002", proposal, approval,
        actor="pm", original_journal_revision=original,
    )
    assert final == journal.snapshot("alpha").revision
    assert journal.snapshot("alpha").active_task == "P-002"
    # Repeated delivery must not create additional transitions.
    assert reconciler.recover(
        "alpha", "P-001", "P-002", proposal, approval,
        actor="pm", original_journal_revision=original,
    ) == final
    assert len(journal.audit("alpha")) == final


def test_missing_manifest_blocks_recovery(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    with pytest.raises(ReconciliationBlocked, match="manifest"):
        _reconciler(registry, journal).recover(
            "alpha", "P-001", "P-002", proposal, approval,
            actor="pm", original_journal_revision=journal.snapshot("alpha").revision,
        )
    assert journal.snapshot("alpha").active_task == "P-001"


def test_partial_journal_recovery_and_replay(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    original = journal.snapshot("alpha").revision
    asyncio.run(ProjectStateStore(registry).apply_approved(proposal, approval))
    reconciler = _reconciler(registry, journal)
    evidence = Verifier().verify(
        project_id="alpha", task_id="P-001",
        canonical_revision=proposal.source_state_revision,
        journal_revision=original,
    )
    journal.transition(
        "alpha", "P-001", "DONE", expected_revision=original,
        actor=evidence.verifier_id, reason="approved canonical commit",
        evidence_ref=reconciler._audit_ref(proposal, evidence),
    )
    final = reconciler.recover(
        "alpha", "P-001", "P-002", proposal, approval,
        actor="pm", original_journal_revision=original,
    )
    assert final == original + 2
    assert reconciler.recover(
        "alpha", "P-001", "P-002", proposal, approval,
        actor="pm", original_journal_revision=original,
    ) == final


def test_tampered_audit_blocks_recovery(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    original = journal.snapshot("alpha").revision
    asyncio.run(ProjectStateStore(registry).apply_approved(proposal, approval))
    journal.transition(
        "alpha", "P-001", "DONE", expected_revision=original,
        actor="pm", reason="untrusted", evidence_ref="ci:untrusted",
    )
    with pytest.raises(ReconciliationBlocked, match="audit"):
        _reconciler(registry, journal).recover(
            "alpha", "P-001", "P-002", proposal, approval,
            actor="pm", original_journal_revision=original,
        )


def test_wrong_project_scope_blocks_advance(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    with pytest.raises(ReconciliationBlocked, match="approval"):
        asyncio.run(_reconciler(registry, journal).advance(
            "beta", "P-001", "P-002", proposal, approval, actor="pm"))
    assert journal.snapshot("beta").active_task == "P-001"


def test_mismatched_verifier_state_blocks_advance(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)

    class WrongVerifier(Verifier):
        def verify(self, **kwargs):
            original = super().verify(**kwargs)
            return CompletionEvidence(
                original.project_id, original.task_id, "0" * 64,
                original.journal_revision, original.verifier_id,
                original.evidence_ref, True,
            )

    with pytest.raises(ReconciliationBlocked, match="verification"):
        asyncio.run(_reconciler(registry, journal, WrongVerifier()).advance(
            "alpha", "P-001", "P-002", proposal, approval, actor="pm"))


def test_manifest_tamper_blocks_recovery(scope):
    import json
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    original = journal.snapshot("alpha").revision
    result = asyncio.run(ProjectStateStore(registry).apply_approved(proposal, approval))
    manifest = result.backup_dir / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["proposal_digest"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReconciliationBlocked, match="manifest"):
        _reconciler(registry, journal).recover(
            "alpha", "P-001", "P-002", proposal, approval,
            actor="pm", original_journal_revision=original,
        )


def test_canonical_drift_after_commit_blocks_recovery(scope):
    registry, journal, a, _ = scope
    proposal, approval = _proposal(registry, a)
    original = journal.snapshot("alpha").revision
    asyncio.run(ProjectStateStore(registry).apply_approved(proposal, approval))
    (a / "STATE.md").write_text(
        (a / "STATE.md").read_text(encoding="utf-8") + "\nNew edit.\n",
        encoding="utf-8",
    )
    with pytest.raises(ReconciliationBlocked, match="canonical"):
        _reconciler(registry, journal).recover(
            "alpha", "P-001", "P-002", proposal, approval,
            actor="pm", original_journal_revision=original,
        )


def test_recovery_rejects_noncanonical_transaction_id(scope):
    from dataclasses import replace
    registry, journal, a, _ = scope
    proposal, _ = _proposal(registry, a)
    bad = replace(proposal, transaction_id="../outside")
    approval = ProjectStateApproval(bad.transaction_id, bad.proposal_digest)
    with pytest.raises((ReconciliationBlocked, ValueError)):
        _reconciler(registry, journal).recover(
            "alpha", "P-001", "P-002", bad, approval,
            actor="pm", original_journal_revision=journal.snapshot("alpha").revision,
        )
