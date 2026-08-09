CREATE TABLE memory_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN (
        'personal_fact', 'event', 'goal', 'preference', 'belief'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'provisional', 'active', 'disabled', 'superseded', 'merged', 'deleted'
    )),
    content TEXT,
    epistemic_status TEXT NOT NULL CHECK(epistemic_status IN (
        'stated', 'interpretation', 'uncertain'
    )),
    observed_at TEXT NOT NULL,
    event_start_at TEXT,
    event_end_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    time_precision TEXT CHECK(time_precision IS NULL OR time_precision IN (
        'exact', 'day', 'week', 'month', 'year', 'relative'
    )),
    time_text TEXT,
    last_supported_at TEXT NOT NULL,
    user_locked INTEGER NOT NULL DEFAULT 0 CHECK(user_locked IN (0, 1)),
    created_by_candidate_id TEXT,
    merged_into_memory_id TEXT,
    superseded_by_memory_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disabled_at TEXT,
    deleted_at TEXT,
    CHECK(
        (status = 'deleted' AND content IS NULL AND deleted_at IS NOT NULL)
        OR (status <> 'deleted' AND content IS NOT NULL AND deleted_at IS NULL)
    ),
    FOREIGN KEY(created_by_candidate_id) REFERENCES memory_candidates(id),
    FOREIGN KEY(merged_into_memory_id) REFERENCES memory_items(id),
    FOREIGN KEY(superseded_by_memory_id) REFERENCES memory_items(id)
);

CREATE TABLE memory_candidates (
    id TEXT PRIMARY KEY,
    extraction_job_id TEXT NOT NULL,
    extraction_run_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN (
        'personal_fact', 'event', 'goal', 'preference', 'belief'
    )),
    content TEXT NOT NULL,
    epistemic_status TEXT NOT NULL CHECK(epistemic_status IN (
        'stated', 'interpretation', 'uncertain'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'extracted', 'invalid', 'rejected', 'suppressed', 'accepted'
    )),
    rejection_code TEXT,
    accepted_memory_id TEXT,
    observed_at TEXT NOT NULL,
    event_start_at TEXT,
    event_end_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    time_precision TEXT CHECK(time_precision IS NULL OR time_precision IN (
        'exact', 'day', 'week', 'month', 'year', 'relative'
    )),
    time_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(extraction_job_id) REFERENCES jobs(id),
    FOREIGN KEY(extraction_run_id) REFERENCES model_runs(id),
    FOREIGN KEY(accepted_memory_id) REFERENCES memory_items(id)
);

CREATE TABLE memory_candidate_sources (
    candidate_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK(start_char >= 0),
    end_char INTEGER NOT NULL CHECK(end_char > start_char),
    text_sha256 TEXT NOT NULL CHECK(length(text_sha256) = 64),
    source_role TEXT NOT NULL CHECK(source_role IN ('target', 'context')),
    PRIMARY KEY(candidate_id, message_id, start_char, end_char),
    FOREIGN KEY(candidate_id) REFERENCES memory_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id)
);

CREATE TABLE memory_sources (
    memory_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK(start_char >= 0),
    end_char INTEGER NOT NULL CHECK(end_char > start_char),
    text_sha256 TEXT NOT NULL CHECK(length(text_sha256) = 64),
    source_role TEXT NOT NULL CHECK(source_role IN ('origin', 'reinforcement', 'update')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(memory_id, message_id, start_char, end_char),
    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id)
);

CREATE TABLE memory_revisions (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL CHECK(revision_no > 0),
    old_content TEXT,
    new_content TEXT,
    old_kind TEXT CHECK(old_kind IS NULL OR old_kind IN (
        'personal_fact', 'event', 'goal', 'preference', 'belief'
    )),
    new_kind TEXT CHECK(new_kind IS NULL OR new_kind IN (
        'personal_fact', 'event', 'goal', 'preference', 'belief'
    )),
    old_epistemic_status TEXT CHECK(old_epistemic_status IS NULL OR old_epistemic_status IN (
        'stated', 'interpretation', 'uncertain'
    )),
    new_epistemic_status TEXT CHECK(new_epistemic_status IS NULL OR new_epistemic_status IN (
        'stated', 'interpretation', 'uncertain'
    )),
    actor TEXT NOT NULL CHECK(actor IN ('user', 'system')),
    created_at TEXT NOT NULL,
    model_run_id TEXT,
    UNIQUE(memory_id, revision_no),
    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE TABLE memory_tombstones (
    id TEXT PRIMARY KEY,
    original_memory_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK(start_char >= 0),
    end_char INTEGER NOT NULL CHECK(end_char > start_char),
    kind TEXT NOT NULL CHECK(kind IN (
        'personal_fact', 'event', 'goal', 'preference', 'belief'
    )),
    deleted_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(source_message_id, start_char, end_char, kind),
    FOREIGN KEY(original_memory_id) REFERENCES memory_items(id),
    FOREIGN KEY(source_message_id) REFERENCES messages(id)
);

CREATE INDEX idx_memory_candidates_job ON memory_candidates(extraction_job_id);
CREATE INDEX idx_memory_candidates_status ON memory_candidates(status, created_at);
CREATE INDEX idx_memory_candidate_sources_message ON memory_candidate_sources(message_id);
CREATE INDEX idx_memory_items_status_kind ON memory_items(status, kind, updated_at DESC);
CREATE INDEX idx_memory_sources_message ON memory_sources(message_id);
CREATE INDEX idx_memory_revisions_memory ON memory_revisions(memory_id, revision_no);
CREATE INDEX idx_memory_tombstones_source
    ON memory_tombstones(source_message_id, start_char, end_char, kind);

CREATE TRIGGER memory_candidate_sources_user_only
BEFORE INSERT ON memory_candidate_sources
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM messages
    WHERE id = NEW.message_id
      AND role = 'user'
      AND status = 'complete'
      AND excluded_from_ai = 0
)
BEGIN
    SELECT RAISE(ABORT, 'MEMORY_SOURCE_NOT_ELIGIBLE_USER');
END;

CREATE TRIGGER memory_sources_user_only
BEFORE INSERT ON memory_sources
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM messages
    WHERE id = NEW.message_id
      AND role = 'user'
      AND status = 'complete'
      AND excluded_from_ai = 0
)
BEGIN
    SELECT RAISE(ABORT, 'MEMORY_SOURCE_NOT_ELIGIBLE_USER');
END;
