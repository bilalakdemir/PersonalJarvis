"""Exact-identity decision routes for governed memory/project-state proposals.

The HUD remains a projection. These POST endpoints belong to the owning
memory/project governance domain and only forward an exact durable identity to
the N-14 approval lifecycles already responsible for the decision.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from jarvis.memory.persistent_approval import PersistentMemoryApprovalError
from jarvis.memory.project_state_approval import ProjectStateProposalLifecycleError

from .control_auth import require_control_key_or_session

router = APIRouter(
    prefix="/api/memory/governance",
    tags=["memory-governance"],
    dependencies=[Depends(require_control_key_or_session)],
)


def _handle(request: Request) -> Any:
    handle = getattr(request.app.state, "memory_governance_handle", None)
    if handle is None:
        raise HTTPException(status_code=503, detail="memory governance is not available")
    return handle


def _conflict(exc: Exception) -> HTTPException:
    detail = str(exc or "governance decision conflict").strip()
    return HTTPException(
        status_code=409,
        detail=(detail[:240] or "governance decision conflict"),
    )


@router.post(
    "/persistent/{candidate_id}/{proposal_digest}/approve",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def approve_persistent_memory(
    candidate_id: int,
    proposal_digest: str,
    request: Request,
) -> dict[str, Any]:
    handle = _handle(request)
    queue = handle.persistent_approval_queue
    if queue is None:
        raise HTTPException(
            status_code=503,
            detail="persistent-memory approval is not available",
        )
    try:
        item = await queue.approve(
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
        )
    except PersistentMemoryApprovalError as exc:
        raise _conflict(exc) from exc
    resume_scheduled = bool(handle.request_governed_memory_resume())
    return {
        "ok": True,
        "status": item.status,
        "candidate_id": item.candidate_id,
        "proposal_digest": item.proposal_digest,
        "resume_scheduled": resume_scheduled,
    }


@router.post(
    "/persistent/{candidate_id}/{proposal_digest}/reject",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def reject_persistent_memory(
    candidate_id: int,
    proposal_digest: str,
    request: Request,
) -> dict[str, Any]:
    handle = _handle(request)
    queue = handle.persistent_approval_queue
    if queue is None:
        raise HTTPException(
            status_code=503,
            detail="persistent-memory approval is not available",
        )
    try:
        item = await queue.reject(
            candidate_id=candidate_id,
            proposal_digest=proposal_digest,
        )
    except PersistentMemoryApprovalError as exc:
        raise _conflict(exc) from exc
    resume_scheduled = bool(handle.request_governed_memory_resume())
    return {
        "ok": True,
        "status": item.status,
        "candidate_id": item.candidate_id,
        "proposal_digest": item.proposal_digest,
        "resume_scheduled": resume_scheduled,
    }


@router.post(
    "/project-state/{queue_item_id}/{transaction_id}/{proposal_digest}/approve",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def approve_project_state_memory(
    queue_item_id: int,
    transaction_id: str,
    proposal_digest: str,
    request: Request,
) -> dict[str, Any]:
    handle = _handle(request)
    lifecycle = handle.project_state_approval_lifecycle
    if lifecycle is None:
        raise HTTPException(
            status_code=503,
            detail="project-state memory approval is not available",
        )
    try:
        result = await lifecycle.approve_and_apply(
            queue_item_id=queue_item_id,
            transaction_id=transaction_id,
            proposal_digest=proposal_digest,
        )
    except ProjectStateProposalLifecycleError as exc:
        raise _conflict(exc) from exc
    return {
        "ok": True,
        "status": result.status,
        "transaction_id": transaction_id,
        "proposal_digest": proposal_digest,
        "resulting_state_revision": result.resulting_state_revision,
    }


@router.post(
    "/project-state/{queue_item_id}/{transaction_id}/{proposal_digest}/reject",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def reject_project_state_memory(
    queue_item_id: int,
    transaction_id: str,
    proposal_digest: str,
    request: Request,
) -> dict[str, Any]:
    handle = _handle(request)
    lifecycle = handle.project_state_approval_lifecycle
    if lifecycle is None:
        raise HTTPException(
            status_code=503,
            detail="project-state memory approval is not available",
        )
    try:
        stored = await lifecycle.reject(
            queue_item_id=queue_item_id,
            transaction_id=transaction_id,
            proposal_digest=proposal_digest,
        )
    except ProjectStateProposalLifecycleError as exc:
        raise _conflict(exc) from exc
    return {
        "ok": True,
        "status": stored.status,
        "queue_item_id": stored.queue_item_id,
        "transaction_id": stored.transaction_id,
        "proposal_digest": stored.proposal_digest,
    }


__all__ = ["router"]
