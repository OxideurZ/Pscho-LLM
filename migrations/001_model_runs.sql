CREATE TABLE IF NOT EXISTS model_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_sha256 TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    backend_version TEXT NOT NULL,
    backend_build TEXT,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    generation_config_json TEXT NOT NULL,
    seed INTEGER,
    app_version TEXT NOT NULL,
    app_git_commit TEXT,
    runtime_info_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    ttft_ms INTEGER,
    prompt_eval_ms INTEGER,
    generation_ms INTEGER,
    total_ms INTEGER,
    tokens_per_second REAL,
    context_size INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_runs_started_at ON model_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_model_runs_status ON model_runs(status);

