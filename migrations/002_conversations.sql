CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    next_sequence_no INTEGER NOT NULL DEFAULT 1 CHECK(next_sequence_no >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    deleted_at TEXT
);

CREATE INDEX idx_conversations_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_conversations_archived_at ON conversations(archived_at);
CREATE INDEX idx_conversations_deleted_at ON conversations(deleted_at);
