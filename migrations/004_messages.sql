CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    session_id TEXT,
    sequence_no INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    input_type TEXT NOT NULL CHECK(input_type IN ('text', 'voice', 'generated')),
    status TEXT NOT NULL CHECK(status IN (
        'complete', 'streaming', 'interrupted', 'failed', 'deleted'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    model_run_id TEXT,
    client_turn_id TEXT,
    excluded_from_ai INTEGER NOT NULL DEFAULT 0 CHECK(excluded_from_ai IN (0, 1)),
    UNIQUE(conversation_id, sequence_no),
    UNIQUE(conversation_id, client_turn_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    FOREIGN KEY(session_id) REFERENCES sessions(id),
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE INDEX idx_messages_conversation_sequence
    ON messages(conversation_id, sequence_no);
CREATE INDEX idx_messages_model_run ON messages(model_run_id);
CREATE INDEX idx_messages_session ON messages(session_id);
