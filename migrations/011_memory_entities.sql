CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN (
        'person', 'place', 'organization', 'other'
    )),
    resolution_status TEXT NOT NULL CHECK(resolution_status IN (
        'unresolved', 'probable', 'confirmed'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE entity_aliases (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, normalized_alias),
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE memory_entities (
    memory_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('subject', 'related', 'location', 'organization')),
    PRIMARY KEY(memory_id, entity_id, role),
    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE INDEX idx_entity_alias_normalized ON entity_aliases(normalized_alias);
CREATE INDEX idx_memory_entities_entity ON memory_entities(entity_id);
