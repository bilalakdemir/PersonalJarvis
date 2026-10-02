-- 0010_governed_temporary_memory.sql
-- General non-conversation temporary context for the governed two-layer
-- Personal Jarvis memory model. This is disposable working memory, not Wiki
-- persistence and not canonical project state.

BEGIN;

CREATE TABLE IF NOT EXISTS temporary_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ms       INTEGER NOT NULL,
    updated_ms       INTEGER NOT NULL,
    expires_ms       INTEGER NOT NULL,
    source           TEXT    NOT NULL,
    kind             TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    project_id       TEXT,
    promotion_state  TEXT    NOT NULL DEFAULT 'unreviewed'
        CHECK (promotion_state IN (
            'unreviewed',
            'temporary',
            'candidate',
            'approval_pending',
            'promoted',
            'rejected'
        )),
    evidence_json    TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_temporary_memory_expires
    ON temporary_memory (expires_ms, id);

CREATE INDEX IF NOT EXISTS idx_temporary_memory_promotion
    ON temporary_memory (promotion_state, expires_ms, id);

CREATE INDEX IF NOT EXISTS idx_temporary_memory_project
    ON temporary_memory (project_id, expires_ms, id);

COMMIT;
