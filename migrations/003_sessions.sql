CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE INDEX idx_sessions_conversation_activity
    ON sessions(conversation_id, last_activity_at DESC);
