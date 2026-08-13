CREATE TABLE retrieval_runs (
    id TEXT PRIMARY KEY,
    model_run_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    query_sha256 TEXT NOT NULL CHECK(length(query_sha256) = 64),
    profile_version TEXT NOT NULL,
    mode TEXT NOT NULL,
    degraded INTEGER NOT NULL CHECK(degraded IN (0, 1)),
    error_codes_json TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK(duration_ms >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE INDEX idx_retrieval_runs_conversation
    ON retrieval_runs(conversation_id, created_at DESC);

CREATE TABLE retrieval_run_items (
    retrieval_run_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('memory', 'raw_user')),
    source_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    lexical_rank INTEGER,
    dense_rank INTEGER,
    fused_rank INTEGER,
    reranker_score REAL,
    final_rank INTEGER,
    injected INTEGER NOT NULL CHECK(injected IN (0, 1)),
    rejection_code TEXT,
    PRIMARY KEY(retrieval_run_id, source_type, source_id),
    FOREIGN KEY(retrieval_run_id) REFERENCES retrieval_runs(id) ON DELETE CASCADE
);

CREATE INDEX idx_retrieval_run_items_injected
    ON retrieval_run_items(retrieval_run_id, injected, final_rank);
