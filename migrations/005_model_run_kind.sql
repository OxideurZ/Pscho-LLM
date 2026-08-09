ALTER TABLE model_runs
    ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'chat'
    CHECK(run_kind IN ('chat', 'rolling_summary'));
