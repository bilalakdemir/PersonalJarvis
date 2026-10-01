from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.projects import (
    ProjectStateApproval,
    ProjectStateApprovalError,
    ProjectStateProposalError,
    ProjectStateReplayError,
    ProjectStateStaleRevisionError,
    ProjectStateStore,
    build_project_state_proposal,
    load_project_context,
    load_registry,
)
from jarvis.core.project_state_events import (
    CurrentTaskChanged,
    ProjectStateCommitted,
)
import jarvis.projects.transaction as txmod


def _project_text(name: str = "Example Project") -> str:
    return f"""# Project\n{name}\n\n## Main Goal\nBuild safely.\n"""


def _state_text(current: str = "N-10 — Project State Transaction Engine") -> str:
    return f"""# Current Project State\n\nProject: Example Project\n\nPhase:\nCore Implementation\n\n## Current Position\nReady.\n\n## Last Completed\nN-09 — Project State Read Foundation\n\n## CURRENT TASK\n{current}\n\n## Next Logical Step\nImplement transactions.\n\n## Blockers\nNone.\n"""


def _tasks_text(current: str = "N-10 Project State Transaction Engine") -> str:
    return f"""# Tasks\n\n## NOW\n\n### CURRENT — {current}\n\nObjective:\nImplement.\n\n## COMPLETED\n\n### N-09 Project State Read Foundation\n\nStatus:\nCOMPLETED\n"""


def _decisions_text() -> str:
    return """# Decisions\n\n## D-001 — One\n\nDecision:\nKeep safe.\n\nReason:\nSafety.\n\nStatus:\nACTIVE\n"""


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PROJECT.md").write_text(_project_text(), encoding="utf-8")
    (root / "STATE.md").write_text(_state_text(), encoding="utf-8")
    (root / "TASKS.md").write_text(_tasks_text(), encoding="utf-8")
    (root / "DECISIONS.md").write_text(_decisions_text(), encoding="utf-8")
    (root / "BACKLOG.md").write_text("# Backlog\n\n## FUTURE\n\n- Later\n", encoding="utf-8")


def _registry(root: Path, path: Path):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [
                    {
                        "project_id": "example",
                        "project_name": "Example Project",
                        "aliases": ["ex"],
                        "root_path": str(root),
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_registry(path)


def _valid_replacements(
    root: Path,
    *,
    next_task: str = "N-11 — Capability & Governance Enforcement",
):
    state = (root / "STATE.md").read_text(encoding="utf-8").replace(
        "N-10 — Project State Transaction Engine", next_task
    )
    tasks = (root / "TASKS.md").read_text(encoding="utf-8").replace(
        "### CURRENT — N-10 Project State Transaction Engine",
        f"### CURRENT — {next_task}",
    )
    return {"STATE.md": state, "TASKS.md": tasks}


def _proposal(
    root: Path,
    registry_path: Path,
    *,
    next_task="N-11 — Capability & Governance Enforcement",
):
    registry = _registry(root, registry_path)
    entry = registry.resolve_exact("example")
    assert entry is not None
    proposal = build_project_state_proposal(
        entry,
        _valid_replacements(root, next_task=next_task),
        reason="Advance after verified completion.",
        expected_current_task=next_task,
        expected_effect="Complete N-10 and promote N-11.",
        transaction_id="00000000-0000-4000-8000-000000000001",
    )
    return registry, entry, proposal


def test_proposal_is_read_only_and_contains_exact_diff(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    _, _, proposal = _proposal(root, tmp_path / "registry.json")
    loaded = load_project_context(
        _registry(root, tmp_path / "r2.json").resolve_exact("example")
    )
    assert loaded.snapshot is not None
    assert proposal.source_state_revision == loaded.snapshot.state_revision
    assert proposal.files_affected == ("STATE.md", "TASKS.md")
    assert all(change.unified_diff for change in proposal.changes)
    assert "N-10 — Project State Transaction Engine" in (
        root / "STATE.md"
    ).read_text()


def test_noncanonical_proposal_file_rejected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg = _registry(root, tmp_path / "r.json")
    entry = reg.resolve_exact("example")
    assert entry
    with pytest.raises(ProjectStateProposalError, match="non-canonical"):
        build_project_state_proposal(
            entry,
            {"README.md": "x"},
            reason="x",
            expected_current_task="N-10 — Project State Transaction Engine",
            expected_effect="x",
        )


def test_wrong_approval_id_does_not_write(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    before = (root / "STATE.md").read_bytes()
    store = ProjectStateStore(reg)
    with pytest.raises(ProjectStateApprovalError):
        asyncio.run(
            store.apply_approved(
                proposal,
                ProjectStateApproval("wrong", proposal.proposal_digest),
            )
        )
    assert (root / "STATE.md").read_bytes() == before


def test_wrong_approval_digest_does_not_write(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    store = ProjectStateStore(reg)
    with pytest.raises(ProjectStateApprovalError):
        asyncio.run(
            store.apply_approved(
                proposal,
                ProjectStateApproval(proposal.transaction_id, "bad"),
            )
        )


def test_stale_revision_rejected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    (root / "BACKLOG.md").write_text("# Backlog\nchanged\n", encoding="utf-8")
    store = ProjectStateStore(reg)
    with pytest.raises(ProjectStateStaleRevisionError):
        asyncio.run(
            store.apply_approved(
                proposal,
                ProjectStateApproval(
                    proposal.transaction_id,
                    proposal.proposal_digest,
                ),
            )
        )


def test_successful_multifile_commit_and_events(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    bus = EventBus()
    events = []

    async def capture(event):
        events.append(event)

    bus.subscribe_all(capture)
    store = ProjectStateStore(reg, bus=bus)
    result = asyncio.run(
        store.apply_approved(
            proposal,
            ProjectStateApproval(
                proposal.transaction_id,
                proposal.proposal_digest,
            ),
        )
    )
    assert result.status == "COMMITTED"
    assert set(result.changed_files) == {"STATE.md", "TASKS.md"}
    assert result.backup_dir and (result.backup_dir / "STATE.md").exists()
    manifest = json.loads((result.backup_dir / "manifest.json").read_text())
    assert manifest["status"] == "COMMITTED"
    assert any(isinstance(event, ProjectStateCommitted) for event in events)
    assert any(isinstance(event, CurrentTaskChanged) for event in events)


def test_replay_rejected_after_commit(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    store = ProjectStateStore(reg)
    approval = ProjectStateApproval(
        proposal.transaction_id,
        proposal.proposal_digest,
    )
    result = asyncio.run(store.apply_approved(proposal, approval))
    assert result.status == "COMMITTED"
    with pytest.raises(ProjectStateReplayError):
        asyncio.run(store.apply_approved(proposal, approval))


def test_second_replace_failure_rolls_back_first_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    store = ProjectStateStore(reg)
    before = {
        name: (root / name).read_bytes()
        for name in ("STATE.md", "TASKS.md")
    }
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        dst_path = Path(dst)
        if dst_path.parent == root and dst_path.name in ("STATE.md", "TASKS.md"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated second replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(txmod.os, "replace", flaky)
    result = asyncio.run(
        store.apply_approved(
            proposal,
            ProjectStateApproval(
                proposal.transaction_id,
                proposal.proposal_digest,
            ),
        )
    )
    assert result.status == "ROLLED_BACK"
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


def test_postwrite_validation_failure_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg, _, proposal = _proposal(root, tmp_path / "r.json")
    store = ProjectStateStore(reg)
    before = (root / "STATE.md").read_bytes()
    real_eval = txmod.evaluate_project_documents
    calls = {"n": 0}

    def flaky_eval(entry_arg, docs):
        calls["n"] += 1
        out = real_eval(entry_arg, docs)
        if calls["n"] == 2:
            from jarvis.projects.models import (
                ProjectLoadResult,
                ProjectValidationIssue,
                ProjectValidationResult,
            )

            return ProjectLoadResult(
                snapshot=out.snapshot,
                validation=ProjectValidationResult(
                    (ProjectValidationIssue("simulated", "bad"),)
                ),
            )
        return out

    monkeypatch.setattr(txmod, "evaluate_project_documents", flaky_eval)
    result = asyncio.run(
        store.apply_approved(
            proposal,
            ProjectStateApproval(
                proposal.transaction_id,
                proposal.proposal_digest,
            ),
        )
    )
    assert result.status == "ROLLED_BACK"
    assert (root / "STATE.md").read_bytes() == before


def test_symlink_canonical_file_rejected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    outside = tmp_path / "outside.md"
    outside.write_text((root / "STATE.md").read_text(), encoding="utf-8")
    (root / "STATE.md").unlink()
    try:
        (root / "STATE.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    reg = _registry(root, tmp_path / "r.json")
    entry = reg.resolve_exact("example")
    assert entry
    with pytest.raises(ProjectStateProposalError, match="symlink"):
        build_project_state_proposal(
            entry,
            {"STATE.md": outside.read_text()},
            reason="repair",
            expected_current_task="N-10 — Project State Transaction Engine",
            expected_effect="repair",
        )


def test_missing_canonical_file_can_be_repaired_in_proposal_and_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "p"
    _write_project(root)
    missing = root / "BACKLOG.md"
    previous = missing.read_text()
    missing.unlink()
    reg = _registry(root, tmp_path / "r.json")
    entry = reg.resolve_exact("example")
    assert entry
    proposal = build_project_state_proposal(
        entry,
        {"BACKLOG.md": previous},
        reason="repair missing file",
        expected_current_task="N-10 — Project State Transaction Engine",
        expected_effect="restore canonical file",
        transaction_id="00000000-0000-4000-8000-000000000002",
    )
    store = ProjectStateStore(reg)
    result = asyncio.run(
        store.apply_approved(
            proposal,
            ProjectStateApproval(
                proposal.transaction_id,
                proposal.proposal_digest,
            ),
        )
    )
    assert result.status == "COMMITTED"
    assert missing.exists()


def test_propose_publishes_only_metadata_not_contents(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg = _registry(root, tmp_path / "r.json")
    bus = EventBus()
    events = []

    async def capture(event):
        events.append(event)

    bus.subscribe_all(capture)
    store = ProjectStateStore(reg, bus=bus)
    replacements = _valid_replacements(root)
    proposal = asyncio.run(
        store.propose(
            "example",
            replacements,
            reason="advance",
            expected_current_task="N-11 — Capability & Governance Enforcement",
            expected_effect="advance",
        )
    )
    assert proposal.proposal_digest
    rendered = " ".join(repr(event) for event in events)
    assert "Implement transactions." not in rendered


def test_non_uuid_transaction_id_rejected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write_project(root)
    reg = _registry(root, tmp_path / "r.json")
    entry = reg.resolve_exact("example")
    assert entry
    with pytest.raises(ProjectStateProposalError, match="UUID"):
        build_project_state_proposal(
            entry,
            _valid_replacements(root),
            reason="advance",
            expected_current_task="N-11 — Capability & Governance Enforcement",
            expected_effect="advance",
            transaction_id="../escape",
        )
