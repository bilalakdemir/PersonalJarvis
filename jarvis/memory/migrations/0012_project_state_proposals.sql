-- 0012_project_state_proposals.sql
BEGIN;

CREATE TABLE IF NOT EXISTS memory_project_state_proposals (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id            INTEGER NOT NULL UNIQUE,
    candidate_id             INTEGER NOT NULL,
    project_id               TEXT    NOT NULL,
    source_state_revision    TEXT    NOT NULL,
    current_task             TEXT,
    transaction_id           TEXT    NOT NULL UNIQUE,
    proposal_digest          TEXT    NOT NULL,
    proposal_json            TEXT    NOT NULL,
    created_ms               INTEGER NOT NULL,
    updated_ms               INTEGER NOT NULL,
    expires_ms               INTEGER NOT NULL,
    status                   TEXT    NOT NULL DEFAULT 'awaiting-approval'
        CHECK (status IN (
            'awaiting-approval',
            'approved',
            'rejected',
            'applied',
            'expired',
            'failed'
        )),
    decision_ms              INTEGER,
    applied_ms               INTEGER,
    resulting_state_revision TEXT,
    failure_reason           TEXT,
    FOREIGN KEY (queue_item_id)
        REFERENCES memory_promotion_queue(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_project_state_proposals_status
    ON memory_project_state_proposals (status, expires_ms, id);

CREATE INDEX IF NOT EXISTS idx_memory_project_state_proposals_project
    ON memory_project_state_proposals (project_id, status, id);

COMMIT;
