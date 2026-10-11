"""Owner-only WebAuthn endpoints for exact AERION project-state decisions.

Never trust Host/Origin from request headers for RP configuration. All
cryptographic verification happens server-side against a pinned origin.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from jarvis.memory.project_approval_passkeys import PasskeyApprovalError

from .control_auth import require_control_key_or_session

router = APIRouter(
    prefix="/api/memory/governance/project-state",
    tags=["project-owner-passkey"],
    dependencies=[Depends(require_control_key_or_session)],
)


class EnrollmentStart(BaseModel):
    bootstrap_secret: str


class EnrollmentFinish(EnrollmentStart):
    challenge: str
    credential: dict[str, Any]


class OwnerAssertion(BaseModel):
    challenge: str
    credential: dict[str, Any]


def passkey_service(request: Request):
    service = getattr(request.app.state, "project_approval_passkeys", None)
    if service is None:
        raise HTTPException(
            status_code=503, detail="owner passkey is not configured"
        )
    return service


def governance_lifecycle(request: Request):
    handle = getattr(request.app.state, "memory_governance_handle", None)
    lifecycle = getattr(handle, "project_state_approval_lifecycle", None)
    if lifecycle is None:
        raise HTTPException(
            status_code=503, detail="project-state approval is not available"
        )
    return lifecycle


async def exact_pending_project(
    request: Request, queue_item_id: int,
    transaction_id: str, proposal_digest: str,
) -> str:
    """Read-only identity check before minting or accepting an assertion."""
    lifecycle = governance_lifecycle(request)
    if queue_item_id < 1:
        raise HTTPException(status_code=409, detail="invalid project approval")
    stored = await lifecycle._proposals.get(queue_item_id)  # noqa: SLF001
    if (
        stored is None or stored.status != "awaiting-approval"
        or stored.transaction_id != transaction_id
        or stored.proposal_digest != proposal_digest
    ):
        raise HTTPException(
            status_code=409, detail="exact pending approval not found"
        )
    return stored.project_id


@router.post("/owner-passkey/enroll/begin",
             openapi_extra={"x-jarvis-dangerous": True})
async def start_owner_enrollment(body: EnrollmentStart, request: Request):
    service = passkey_service(request)
    try:
        return await asyncio.to_thread(
            service.begin_enrollment, bootstrap_secret=body.bootstrap_secret
        )
    except PasskeyApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/owner-passkey/enroll/finish",
             openapi_extra={"x-jarvis-dangerous": True})
async def finish_owner_enrollment(body: EnrollmentFinish, request: Request):
    service = passkey_service(request)
    try:
        credential_id = await asyncio.to_thread(
            service.finish_enrollment, bootstrap_secret=body.bootstrap_secret,
            challenge=body.challenge, credential=body.credential,
        )
    except PasskeyApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "enrolled": bool(credential_id)}


@router.post("/{queue_item_id}/{transaction_id}/{proposal_digest}/passkey/begin",
             openapi_extra={"x-jarvis-dangerous": True})
async def start_owner_approval(
    queue_item_id: int, transaction_id: str, proposal_digest: str,
    request: Request,
):
    service = passkey_service(request)
    project_id = await exact_pending_project(
        request, queue_item_id, transaction_id, proposal_digest
    )
    try:
        return await asyncio.to_thread(
            service.begin_approval, project_id=project_id,
            queue_item_id=queue_item_id, transaction_id=transaction_id,
            proposal_digest=proposal_digest,
        )
    except PasskeyApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = [
    "router", "OwnerAssertion", "passkey_service",
    "exact_pending_project", "governance_lifecycle",
]
