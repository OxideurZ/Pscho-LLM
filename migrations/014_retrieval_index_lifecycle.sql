CREATE TRIGGER retrieval_message_index_after_insert
AFTER INSERT ON messages
FOR EACH ROW
WHEN NEW.role = 'user' AND NEW.status = 'complete' AND NEW.excluded_from_ai = 0
BEGIN
    INSERT INTO jobs(
        id, kind, status, priority, dedupe_key, source_message_id,
        source_type, source_id, attempts, max_attempts, available_at,
        created_at, updated_at
    ) VALUES (
        'job_' || lower(hex(randomblob(16))),
        'retrieval_index_message', 'pending', -10,
        'retrieval_index_message:' || NEW.id || ':' || NEW.updated_at,
        NEW.id, 'raw_user', NEW.id, 0, 3, NEW.updated_at, NEW.updated_at, NEW.updated_at
    ) ON CONFLICT(dedupe_key) DO NOTHING;
END;

CREATE TRIGGER retrieval_message_index_after_update
AFTER UPDATE OF content, status, excluded_from_ai, updated_at ON messages
FOR EACH ROW
WHEN NEW.role = 'user'
BEGIN
    INSERT INTO jobs(
        id, kind, status, priority, dedupe_key, source_message_id,
        source_type, source_id, attempts, max_attempts, available_at,
        created_at, updated_at
    ) VALUES (
        'job_' || lower(hex(randomblob(16))),
        'retrieval_index_message', 'pending', -10,
        'retrieval_index_message:' || NEW.id || ':' || NEW.updated_at,
        NEW.id, 'raw_user', NEW.id, 0, 3, NEW.updated_at, NEW.updated_at, NEW.updated_at
    ) ON CONFLICT(dedupe_key) DO NOTHING;
END;

CREATE TRIGGER retrieval_memory_index_after_insert
AFTER INSERT ON memory_items
FOR EACH ROW
BEGIN
    INSERT INTO jobs(
        id, kind, status, priority, dedupe_key, source_type, source_id,
        attempts, max_attempts, available_at, created_at, updated_at
    ) VALUES (
        'job_' || lower(hex(randomblob(16))),
        'retrieval_index_memory', 'pending', -10,
        'retrieval_index_memory:' || NEW.id || ':' || NEW.updated_at,
        'memory', NEW.id, 0, 3, NEW.updated_at, NEW.updated_at, NEW.updated_at
    ) ON CONFLICT(dedupe_key) DO NOTHING;
END;

CREATE TRIGGER retrieval_conversation_delete_after_update
AFTER UPDATE OF deleted_at ON conversations
FOR EACH ROW
WHEN OLD.deleted_at IS NOT NEW.deleted_at
BEGIN
    INSERT INTO jobs(
        id, kind, status, priority, dedupe_key, source_message_id,
        source_type, source_id, attempts, max_attempts, available_at,
        created_at, updated_at
    )
    SELECT
        'job_' || lower(hex(randomblob(16))),
        'retrieval_index_message', 'pending', -10,
        'retrieval_index_message:' || message.id || ':conversation:' || NEW.updated_at,
        message.id, 'raw_user', message.id, 0, 3,
        NEW.updated_at, NEW.updated_at, NEW.updated_at
    FROM messages AS message
    WHERE message.conversation_id = NEW.id AND message.role = 'user'
    ON CONFLICT(dedupe_key) DO NOTHING;
END;

CREATE TRIGGER retrieval_memory_index_after_update
AFTER UPDATE OF content, status, updated_at ON memory_items
FOR EACH ROW
BEGIN
    INSERT INTO jobs(
        id, kind, status, priority, dedupe_key, source_type, source_id,
        attempts, max_attempts, available_at, created_at, updated_at
    ) VALUES (
        'job_' || lower(hex(randomblob(16))),
        'retrieval_index_memory', 'pending', -10,
        'retrieval_index_memory:' || NEW.id || ':' || NEW.updated_at,
        'memory', NEW.id, 0, 3, NEW.updated_at, NEW.updated_at, NEW.updated_at
    ) ON CONFLICT(dedupe_key) DO NOTHING;
END;
