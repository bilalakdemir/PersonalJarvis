-- 0011_memory_promotion_queue.sql
BEGIN;

CREATE TABLE IF NOT EXISTS memory_promotion_queue (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key            TEXT    NOT NULL UNIQUE,
    created_ms            INTEGER NOT NULL,
    updated_ms            INTEGER NOT NULL,
    expires_ms            INTEGER NOT NULL,
    candidate_id          INTEGER NOT NULL,
    authority             TEXT    NOT NULL
        CHECK (authority IN ('persistent-memory', 'project-state')),
    project_id            TEXT,
    source_state_revision TEXT,
    current_task          TEXT,
    relation              TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending',
            'proposal-created',
            'approved',
            'rejected',
            'expired',
            'failed'
        ))
);

CREATE INDEX IF NOT EXISTS idx_memory_promotion_queue_pending
    ON memory_promotion_queue (status, expires_ms, id);

CREATE INDEX IF NOT EXISTS idx_memory_promotion_queue_candidate
    ON memory_promotion_queue (candidate_id, authority);

CREATE INDEX IF NOT EXISTS idx_memory_promotion_queue_project
    ON memory_promotion_queue (project_id, status, expires_ms);

COMMIT;
