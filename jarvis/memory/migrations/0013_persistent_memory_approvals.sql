-- 0013_persistent_memory_approvals.sql
BEGIN;

CREATE TABLE IF NOT EXISTS memory_persistent_approvals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id      INTEGER NOT NULL UNIQUE,
    content_sha256    TEXT NOT NULL,
    governance_class  TEXT NOT NULL,
    proposal_digest   TEXT NOT NULL UNIQUE,
    created_ms        INTEGER NOT NULL,
    updated_ms        INTEGER NOT NULL,
    expires_ms        INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending',
            'approved',
            'rejected',
            'applied',
            'expired',
            'failed'
        )),
    decision_ms       INTEGER,
    applied_ms        INTEGER,
    failure_reason    TEXT,
    FOREIGN KEY (candidate_id)
        REFERENCES wiki_candidate_journal(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_persistent_approvals_status
    ON memory_persistent_approvals (status, expires_ms, id);

COMMIT;
