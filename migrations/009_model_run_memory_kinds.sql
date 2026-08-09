PRAGMA defer_foreign_keys = ON;

CREATE TABLE model_runs_f (
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
    error_code TEXT,
    run_kind TEXT NOT NULL DEFAULT 'chat' CHECK(run_kind IN (
        'chat', 'rolling_summary', 'memory_extract', 'memory_consolidation'
    ))
);

INSERT INTO model_runs_f(
    id, status, model_name, model_sha256, backend_name, backend_version, backend_build,
    prompt_id, prompt_version, prompt_sha256, generation_config_json, seed, app_version,
    app_git_commit, runtime_info_json, input_tokens, output_tokens, ttft_ms, prompt_eval_ms,
    generation_ms, total_ms, tokens_per_second, context_size, started_at, completed_at,
    error_code, run_kind
)
SELECT
    id, status, model_name, model_sha256, backend_name, backend_version, backend_build,
    prompt_id, prompt_version, prompt_sha256, generation_config_json, seed, app_version,
    app_git_commit, runtime_info_json, input_tokens, output_tokens, ttft_ms, prompt_eval_ms,
    generation_ms, total_ms, tokens_per_second, context_size, started_at, completed_at,
    error_code, run_kind
FROM model_runs;

DROP TABLE model_runs;
ALTER TABLE model_runs_f RENAME TO model_runs;

CREATE INDEX idx_model_runs_started_at ON model_runs(started_at);
CREATE INDEX idx_model_runs_status ON model_runs(status);
CREATE INDEX idx_model_runs_kind_started ON model_runs(run_kind, started_at);
