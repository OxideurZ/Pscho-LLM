CREATE TABLE jobs_g13 (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN (
        'memory_extract', 'memory_consolidate', 'memory_backfill',
        'retrieval_index_message', 'retrieval_index_memory', 'retrieval_reindex'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'running', 'retry', 'complete', 'failed', 'cancelled'
    )),
    priority INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_message_id TEXT,
    source_type TEXT CHECK(source_type IS NULL OR source_type IN ('memory', 'raw_user')),
    source_id TEXT,
    backfill_id TEXT,
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
    CHECK(
        (source_type IS NULL AND source_id IS NULL)
        OR (source_type IS NOT NULL AND source_id IS NOT NULL)
    ),
    FOREIGN KEY(source_message_id) REFERENCES messages(id),
    FOREIGN KEY(backfill_id) REFERENCES memory_backfills(id),
    FOREIGN KEY(blocked_by_run_id) REFERENCES model_runs(id)
);

INSERT INTO jobs_g13(
    id, kind, status, priority, dedupe_key, source_message_id, backfill_id,
    blocked_by_run_id, attempts, max_attempts, available_at, started_at,
    completed_at, error_code, execution_token, cancel_requested_at, created_at, updated_at
)
SELECT
    id, kind, status, priority, dedupe_key, source_message_id, backfill_id,
    blocked_by_run_id, attempts, max_attempts, available_at, started_at,
    completed_at, error_code, execution_token, cancel_requested_at, created_at, updated_at
FROM jobs;

DROP TABLE jobs;
ALTER TABLE jobs_g13 RENAME TO jobs;

CREATE INDEX idx_jobs_claim
    ON jobs(status, available_at, priority DESC, created_at);
CREATE INDEX idx_jobs_source_message ON jobs(source_message_id);
CREATE INDEX idx_jobs_source ON jobs(source_type, source_id, status);
CREATE INDEX idx_jobs_blocked_run ON jobs(blocked_by_run_id);
CREATE INDEX idx_jobs_backfill ON jobs(backfill_id, status, created_at);

CREATE VIRTUAL TABLE retrieval_fts USING fts5(
    source_type UNINDEXED,
    source_id UNINDEXED,
    content,
    content_sha256 UNINDEXED,
    updated_at UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE retrieval_embeddings (
    source_type TEXT NOT NULL CHECK(source_type IN ('memory', 'raw_user')),
    source_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    instruction_version TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL CHECK(length(configuration_sha256) = 64),
    dimensions INTEGER NOT NULL CHECK(dimensions BETWEEN 32 AND 4096),
    dtype TEXT NOT NULL CHECK(dtype IN ('float32')),
    vector_blob BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_type, source_id),
    CHECK(length(vector_blob) = dimensions * 4)
);

CREATE INDEX idx_retrieval_embeddings_profile
    ON retrieval_embeddings(
        embedding_model, embedding_revision, instruction_version,
        configuration_sha256, dimensions, dtype
    );

CREATE TABLE retrieval_profiles (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    configuration_json TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL UNIQUE CHECK(length(configuration_sha256) = 64),
    created_at TEXT NOT NULL
);
