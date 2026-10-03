from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.persistent_approval import (
    PersistentMemoryApprovalError,
    PersistentMemoryApprovalIdentityError,
    PersistentMemoryApprovalQueue,
)
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal


def _candidate(db_path: Path, *, clock) -> tuple[CandidateJournal, int]:
    journal = CandidateJournal(db_path, clock=clock)
    assert journal.append(
        [
            CandidateFact(
                fact="Use the approved release checklist from now on.",
                kind="decision",
                evidence_turn_id="turn-1",
                evidence_excerpt=(
                    "Use the approved release checklist from now on."
                ),
                basis="explicit",
            )
        ],
        source_label="test",
        turn_hash="turn-hash",
    ) == 1
    return journal, journal.pending()[0].id


@pytest.mark.asyncio
async def test_exact_persistent_approval_survives_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "jarvis.db"
    now = [1_000.0]
    journal, candidate_id = _candidate(db_path, clock=lambda: now[0])
    queue = PersistentMemoryApprovalQueue(db_path, clock=lambda: now[0])

    try:
        proposal = await queue.propose(
            candidate_id=candidate_id,
            content="Use the approved release checklist from now on.",
            governance_class="user-wide-decision",
        )
        assert proposal.status == "pending"

        with pytest.raises(PersistentMemoryApprovalIdentityError):
            await queue.approve(
                candidate_id=candidate_id,
                proposal_digest="0" * 64,
            )

        approved = await queue.approve(
            candidate_id=candidate_id,
            proposal_digest=proposal.proposal_digest,
        )
        assert approved.status == "approved"
    finally:
        await queue.close()
        journal.close()

    reopened = PersistentMemoryApprovalQueue(db_path, clock=lambda: now[0])
    try:
        stored = await reopened.get(candidate_id)
        assert stored is not None
        assert stored.status == "approved"
        assert stored.proposal_digest == proposal.proposal_digest
        applied = await reopened.mark_applied(candidate_id)
        assert applied is not None
        assert applied.status == "applied"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_candidate_content_is_immutably_bound(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "jarvis.db"
    journal, candidate_id = _candidate(db_path, clock=lambda: 1_000.0)
    queue = PersistentMemoryApprovalQueue(db_path, clock=lambda: 1_000.0)

    try:
        await queue.propose(
            candidate_id=candidate_id,
            content="Use the approved release checklist from now on.",
            governance_class="user-wide-decision",
        )
        with pytest.raises(PersistentMemoryApprovalIdentityError):
            await queue.propose(
                candidate_id=candidate_id,
                content="A different rule must not replace the proposal.",
                governance_class="user-wide-decision",
            )
    finally:
        await queue.close()
        journal.close()


@pytest.mark.asyncio
async def test_pending_persistent_approval_expires(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "jarvis.db"
    now = [1_000.0]
    journal, candidate_id = _candidate(db_path, clock=lambda: now[0])
    queue = PersistentMemoryApprovalQueue(db_path, clock=lambda: now[0])

    try:
        proposal = await queue.propose(
            candidate_id=candidate_id,
            content="Use the approved release checklist from now on.",
            governance_class="user-wide-decision",
            retention_days=1,
        )
        now[0] += 2 * 86_400
        expired = await queue.expire_due()
        assert expired == (candidate_id,)
        stored = await queue.get(candidate_id)
        assert stored is not None
        assert stored.status == "expired"
        with pytest.raises(PersistentMemoryApprovalError, match="terminal"):
            await queue.approve(
                candidate_id=candidate_id,
                proposal_digest=proposal.proposal_digest,
            )
    finally:
        await queue.close()
        journal.close()
