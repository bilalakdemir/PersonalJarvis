"""Contract test for the canonical AERION project governance documents."""
from pathlib import Path

from jarvis.projects.loader import load_project_context
from jarvis.projects.models import ProjectRegistryEntry


def test_aerion_project_documents_are_readable_by_canonical_loader():
    root = Path(__file__).resolve().parents[3]
    entry = ProjectRegistryEntry(
        project_id="aerion",
        project_name="AERION",
        aliases=(),
        root_path=root,
        status="active",
    )
    loaded = load_project_context(entry)
    assert loaded.validation.valid, [
        (issue.code, issue.message) for issue in loaded.validation.issues
    ]
    snapshot = loaded.snapshot
    assert snapshot is not None
    assert snapshot.project_name == "AERION"
    assert snapshot.current_task is not None
    assert snapshot.current_task.startswith("PM-001")
    assert len(snapshot.active_decisions) == 8
    assert snapshot.next_step
    assert snapshot.blockers
