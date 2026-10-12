"""End-to-end ASGI binding for owner WebAuthn project approval.

Real ES256 test authenticator signs the exact server-issued challenge; the
server verifies origin/RP and consumes an approval grant before applying.
No enrolled owner credentials or signing secrets are committed to source.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI

from jarvis.memory.project_approval_passkeys import ProjectApprovalPasskeys
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.memory_governance_routes import router as decisions
from jarvis.ui.web.project_passkey_routes import router as passkeys
from tests.unit.memory.test_project_passkey_approved_lifecycle import (
    _close,
    _prepared,
)
from tests.unit.memory.test_project_passkey_crypto import (
    BOOTSTRAP,
    ORIGIN,
    RP,
    _assertion,
    _registration,
)
from tests.unit.memory.test_project_state_approval import _setup


def _app(lifecycle, service) -> FastAPI:
    app = FastAPI()
    app.include_router(decisions)
    app.include_router(passkeys)
    app.state.project_approval_passkeys = service
    app.state.memory_governance_handle = SimpleNamespace(
        project_state_approval_lifecycle=lifecycle,
    )
    app.dependency_overrides[require_control_key_or_session] = lambda: None
    return app


@pytest.mark.asyncio
async def test_signed_http_approval_requires_exact_user_assertion(tmp_path, monkeypatch):
    root, journal, queue, item, lifecycle, _, proposal, service, signer, cid = (
        await _prepared(tmp_path, monkeypatch)
    )
    path = (
        f"/api/memory/governance/project-state/{item.id}/"
        f"{proposal.transaction_id}/{proposal.proposal_digest}"
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(lifecycle, service)),
            base_url=ORIGIN,
        ) as client:
            assert (await client.post(path + "/approve")).status_code == 422
            assert (
                await client.post(path + "/approve", json={
                    "challenge": "not-signed", "credential": {"id": "forged"},
                })
            ).status_code == 409
            incorrect = await client.post(
                path.replace(proposal.proposal_digest, "0" * 64)
                + "/passkey/begin",
            )
            assert incorrect.status_code == 409
            assert (root / "STATE.md").read_text(encoding="utf-8").find(
                "N-10 — Project State Transaction Engine"
            ) != -1

            options = await client.post(path + "/passkey/begin")
            assert options.status_code == 200, options.text
            challenge = options.json()["challenge"]
            credential = _assertion(signer, cid, challenge)
            approval = await client.post(
                path + "/approve", json={
                    "challenge": challenge, "credential": credential,
                },
            )
            assert approval.status_code == 200, approval.text
            assert approval.json()["status"] == "COMMITTED"
            record = await lifecycle._proposals.get(item.id)
            assert record.status == "applied"
            assert record.approved_via == "webauthn"
            assert record.approved_by_credential_id

            replay = await client.post(
                path + "/approve", json={
                    "challenge": challenge, "credential": credential,
                },
            )
            assert replay.status_code == 409
    finally:
        await _close(journal, queue, lifecycle)


@pytest.mark.asyncio
async def test_owner_enrollment_http_accepts_only_real_signed_registration(
    tmp_path, monkeypatch,
):
    root, journal, queue, item, lifecycle, db_path = await _setup(tmp_path)
    monkeypatch.setenv("AERION_PASSKEY_ENROLLMENT_SECRET", BOOTSTRAP)
    service = ProjectApprovalPasskeys(db_path, rp_id=RP, origin=ORIGIN)
    prefix = "/api/memory/governance/project-state/owner-passkey/enroll"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(lifecycle, service)),
            base_url=ORIGIN,
        ) as client:
            denied = await client.post(
                prefix + "/begin", json={"bootstrap_secret": "incorrect"}
            )
            assert denied.status_code == 409
            begun = await client.post(
                prefix + "/begin", json={"bootstrap_secret": BOOTSTRAP}
            )
            assert begun.status_code == 200, begun.text
            challenge = begun.json()["challenge"]
            owner = ec.generate_private_key(ec.SECP256R1())
            credential = _registration(owner, b"test-http-owner", challenge)
            result = await client.post(prefix + "/finish", json={
                "bootstrap_secret": BOOTSTRAP,
                "challenge": challenge,
                "credential": credential,
            })
            assert result.status_code == 200, result.text
            assert result.json()["enrolled"] is True
            repeated = await client.post(prefix + "/finish", json={
                "bootstrap_secret": BOOTSTRAP,
                "challenge": challenge,
                "credential": credential,
            })
            assert repeated.status_code == 409
    finally:
        await _close(journal, queue, lifecycle)
