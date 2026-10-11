"""Route contract tests for exact governed-memory decisions."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.memory.persistent_approval import PersistentMemoryApprovalError
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.memory_governance_routes import router


class _PersistentQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int, str]] = []

    async def approve(self, *, candidate_id: int, proposal_digest: str):
        self.calls.append(("approve", candidate_id, proposal_digest))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status="approved",
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
        )

    async def reject(self, *, candidate_id: int, proposal_digest: str):
        self.calls.append(("reject", candidate_id, proposal_digest))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status="rejected",
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
        )


class _ProjectLifecycle:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int, str, str]] = []

    async def approve_and_apply(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest: str,
    ):
        self.calls.append(
            ("approve", queue_item_id, transaction_id, proposal_digest)
        )
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status="COMMITTED",
            resulting_state_revision="a" * 64,
        )

    async def reject(
        self,
        *,
        queue_item_id: int,
        transaction_id: str,
        proposal_digest: str,
    ):
        self.calls.append(
            ("reject", queue_item_id, transaction_id, proposal_digest)
        )
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status="rejected",
            queue_item_id=queue_item_id,
            transaction_id=transaction_id,
            proposal_digest=proposal_digest,
        )


class _Handle:
    def __init__(
        self,
        *,
        persistent=None,
        project=None,
        resume: bool = True,
    ) -> None:
        self.persistent_approval_queue = persistent
        self.project_state_approval_lifecycle = project
        self.resume = resume
        self.resume_calls = 0

    def request_governed_memory_resume(self) -> bool:
        self.resume_calls += 1
        return self.resume


def _client(handle, *, passkeys=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.memory_governance_handle = handle
    app.state.project_approval_passkeys = passkeys
    app.dependency_overrides[require_control_key_or_session] = lambda: None
    return TestClient(app)


def test_missing_runtime_is_service_unavailable() -> None:
    response = _client(None).post(
        "/api/memory/governance/persistent/7/deadbeef/approve"
    )
    assert response.status_code == 503


def test_persistent_approve_binds_exact_identity_and_resumes_stage2() -> None:
    queue = _PersistentQueue()
    handle = _Handle(persistent=queue)
    response = _client(handle).post(
        "/api/memory/governance/persistent/7/deadbeef/approve"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert queue.calls == [("approve", 7, "deadbeef")]
    assert handle.resume_calls == 1
    assert response.json()["resume_scheduled"] is True


def test_persistent_reject_binds_exact_identity() -> None:
    queue = _PersistentQueue()
    handle = _Handle(persistent=queue)
    response = _client(handle).post(
        "/api/memory/governance/persistent/8/cafebabe/reject"
    )

    assert response.status_code == 200
    assert queue.calls == [("reject", 8, "cafebabe")]
    assert handle.resume_calls == 1


def test_persistent_identity_conflict_is_409() -> None:
    queue = _PersistentQueue(
        error=PersistentMemoryApprovalError("proposal digest mismatch")
    )
    response = _client(_Handle(persistent=queue)).post(
        "/api/memory/governance/persistent/7/wrong/approve"
    )

    assert response.status_code == 409
    assert "digest" in response.json()["detail"]


def test_project_approve_without_passkey_is_rejected() -> None:
    lifecycle = _ProjectLifecycle()
    client = _client(_Handle(project=lifecycle))
    url = "/api/memory/governance/project-state/13/tx-123/feedface/approve"
    response = client.post(url)
    assert response.status_code == 422
    assert lifecycle.calls == []
    valid_shape_without_service = client.post(url, json={
        "challenge": "unverified", "credential": {"id": "forged"},
    })
    assert valid_shape_without_service.status_code == 503
    assert lifecycle.calls == []


def test_project_reject_binds_all_three_identifiers() -> None:
    lifecycle = _ProjectLifecycle()
    response = _client(_Handle(project=lifecycle)).post(
        "/api/memory/governance/project-state/14/tx-456/decafbad/reject"
    )

    assert response.status_code == 200
    assert lifecycle.calls == [("reject", 14, "tx-456", "decafbad")]
    assert response.json()["status"] == "rejected"


def test_project_identity_conflict_is_409() -> None:
    class StoredProposals:
        async def get(self, queue_item_id: int):
            assert queue_item_id == 14
            return SimpleNamespace(
                status="awaiting-approval", transaction_id="tx-456",
                proposal_digest="expected", project_id="example",
            )

    lifecycle = _ProjectLifecycle()
    lifecycle._proposals = StoredProposals()
    response = _client(_Handle(project=lifecycle), passkeys=object()).post(
        "/api/memory/governance/project-state/14/tx-456/wrong/approve",
        json={"challenge": "not-a-proof", "credential": {}},
    )

    assert response.status_code == 409
    assert "exact pending approval" in response.json()["detail"]
    assert lifecycle.calls == []


def test_mutating_routes_are_marked_dangerous() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert all(
        operation["post"]["x-jarvis-dangerous"] is True
        for operation in paths.values()
    )
