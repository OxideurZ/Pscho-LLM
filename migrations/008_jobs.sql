CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN (
        'memory_extract', 'memory_consolidate', 'memory_backfill'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'running', 'retry', 'complete', 'failed', 'cancelled'
    )),
    priority INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_message_id TEXT,
    blocked_by_run_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts > 0),
    available_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    execution_token TEXT,
    cancel_requested_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_message_id) REFERENCES messages(id),
    FOREIGN KEY(blocked_by_run_id) REFERENCES model_runs(id)
);

CREATE INDEX idx_jobs_claim
    ON jobs(status, available_at, priority DESC, created_at);
CREATE INDEX idx_jobs_source_message ON jobs(source_message_id);
CREATE INDEX idx_jobs_blocked_run ON jobs(blocked_by_run_id);
