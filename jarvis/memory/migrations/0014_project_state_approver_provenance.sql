-- 0014_project_state_approver_provenance.sql
-- Store only public WebAuthn keys, single-use challenges, and bound grants.
-- Old applied approvals retain NULL fields: never retroactively trust them.
BEGIN;
ALTER TABLE memory_project_state_proposals ADD COLUMN approved_by_credential_id TEXT;
ALTER TABLE memory_project_state_proposals ADD COLUMN approved_via TEXT;
ALTER TABLE memory_project_state_proposals ADD COLUMN approval_assertion_digest TEXT;
CREATE TABLE IF NOT EXISTS memory_pm_owner_passkeys (
    credential_id TEXT PRIMARY KEY,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL CHECK(sign_count >= 0),
    enrolled_ms INTEGER NOT NULL CHECK(enrolled_ms > 0)
);
CREATE TABLE IF NOT EXISTS memory_pm_passkey_challenges (
    challenge_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('enroll', 'approve')),
    project_id TEXT,
    queue_item_id INTEGER,
    transaction_id TEXT,
    proposal_digest TEXT,
    expires_ms INTEGER NOT NULL,
    used_ms INTEGER
);
CREATE TABLE IF NOT EXISTS memory_pm_passkey_grants (
    token_hash TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL REFERENCES memory_pm_owner_passkeys(credential_id),
    project_id TEXT NOT NULL,
    queue_item_id INTEGER NOT NULL,
    transaction_id TEXT NOT NULL,
    proposal_digest TEXT NOT NULL,
    assertion_digest TEXT NOT NULL,
    expires_ms INTEGER NOT NULL,
    consumed_ms INTEGER
);
COMMIT;
