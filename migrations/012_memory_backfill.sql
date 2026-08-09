CREATE TABLE memory_backfills (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('pending', 'complete', 'failed', 'cancelled')),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('conversations', 'date_range', 'all_eligible')),
    conversation_ids_json TEXT NOT NULL DEFAULT '[]',
    created_after TEXT,
    created_before TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    eligible_messages INTEGER NOT NULL CHECK(eligible_messages >= 0),
    batch_size INTEGER NOT NULL CHECK(batch_size BETWEEN 1 AND 50),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE jobs ADD COLUMN backfill_id TEXT REFERENCES memory_backfills(id);

CREATE INDEX idx_jobs_backfill ON jobs(backfill_id, status, created_at);
CREATE INDEX idx_memory_backfills_status ON memory_backfills(status, created_at);
