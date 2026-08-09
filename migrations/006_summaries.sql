CREATE TABLE summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    summary_type TEXT NOT NULL CHECK(summary_type = 'rolling'),
    schema_version TEXT NOT NULL,
    parent_summary_id TEXT,
    content_json TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    model_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    FOREIGN KEY(parent_summary_id) REFERENCES summaries(id),
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE INDEX idx_summaries_conversation_created
    ON summaries(conversation_id, created_at DESC);
