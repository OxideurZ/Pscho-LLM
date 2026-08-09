CREATE TABLE summary_sources (
    summary_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY(summary_id, message_id),
    FOREIGN KEY(summary_id) REFERENCES summaries(id),
    FOREIGN KEY(message_id) REFERENCES messages(id)
);

CREATE INDEX idx_summary_sources_message ON summary_sources(message_id);
