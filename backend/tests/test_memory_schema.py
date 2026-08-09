from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.conversations import ConversationRepository
from backend.app.conversations.repository import new_id
from backend.app.db import Database


def metadata() -> dict[str, object]:
    return {
        "model_name": "model",
        "model_sha256": "a" * 64,
        "backend_name": "llama.cpp",
        "backend_version": "b9637",
        "backend_build": "commit",
        "prompt_id": "conversation_system",
        "prompt_version": "0.1.2",
        "prompt_sha256": "b" * 64,
        "generation_config": {"max_tokens": 800},
        "seed": 42,
        "app_version": "test",
        "app_git_commit": "commit",
        "runtime_info": {},
        "context_size": 32768,
    }


async def database_with_turn(path: Path) -> tuple[Database, dict[str, str]]:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    identifiers = {
        "user_message_id": new_id("msg"),
        "assistant_message_id": new_id("msg"),
        "session_id": new_id("session"),
        "run_id": new_id("run"),
        "job_id": new_id("job"),
    }
    await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Je préfère les réponses détaillées.",
        input_type="text",
        ids=identifiers,
        run_metadata=metadata(),
        memory_job={
            "id": identifiers["job_id"],
            "priority": 0,
            "dedupe_key": f"memory_extract:0.1.2:{identifiers['user_message_id']}",
            "max_attempts": 3,
        },
    )
    return database, identifiers


async def insert_candidate(connection: aiosqlite.Connection, ids: dict[str, str]) -> str:
    now = datetime.now(UTC).isoformat()
    candidate_id = new_id("candidate")
    await connection.execute(
        """
        INSERT INTO memory_candidates(
            id, extraction_job_id, extraction_run_id, kind, content,
            epistemic_status, status, observed_at, created_at, updated_at
        ) VALUES (?, ?, ?, 'preference', ?, 'stated', 'extracted', ?, ?, ?)
        """,
        (
            candidate_id,
            ids["job_id"],
            ids["run_id"],
            "L'utilisateur préfère les réponses détaillées.",
            now,
            now,
            now,
        ),
    )
    return candidate_id


@pytest.mark.asyncio
async def test_memory_model_run_kinds_preserve_existing_foreign_keys(tmp_path: Path) -> None:
    database, ids = await database_with_turn(tmp_path / "app.sqlite")

    async with database.connect() as connection:
        await connection.execute(
            "UPDATE model_runs SET run_kind = 'memory_extract' WHERE id = ?", (ids["run_id"],)
        )
        await connection.commit()
        violations = await (await connection.execute("PRAGMA foreign_key_check")).fetchall()

    assert violations == []


@pytest.mark.asyncio
async def test_database_trigger_refuses_assistant_memory_provenance(tmp_path: Path) -> None:
    database, ids = await database_with_turn(tmp_path / "app.sqlite")

    async with database.connect() as connection:
        candidate_id = await insert_candidate(connection, ids)
        await connection.execute(
            """
            INSERT INTO memory_candidate_sources(
                candidate_id, message_id, start_char, end_char, text_sha256, source_role
            ) VALUES (?, ?, 0, 2, ?, 'target')
            """,
            (candidate_id, ids["user_message_id"], "a" * 64),
        )
        with pytest.raises(aiosqlite.IntegrityError, match="MEMORY_SOURCE_NOT_ELIGIBLE_USER"):
            await connection.execute(
                """
                INSERT INTO memory_candidate_sources(
                    candidate_id, message_id, start_char, end_char, text_sha256, source_role
                ) VALUES (?, ?, 0, 2, ?, 'context')
                """,
                (candidate_id, ids["assistant_message_id"], "b" * 64),
            )
        await connection.rollback()


@pytest.mark.asyncio
async def test_deleted_memory_requires_scrubbed_content_and_tombstone_has_no_content(
    tmp_path: Path,
) -> None:
    database, ids = await database_with_turn(tmp_path / "app.sqlite")
    now = datetime.now(UTC).isoformat()

    async with database.connect() as connection:
        candidate_id = await insert_candidate(connection, ids)
        memory_id = new_id("memory")
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                last_supported_at, created_by_candidate_id, created_at, updated_at
            ) VALUES (?, 'preference', 'active', ?, 'stated', ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                "L'utilisateur préfère les réponses détaillées.",
                now,
                now,
                candidate_id,
                now,
                now,
            ),
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                "UPDATE memory_items SET status = 'deleted', deleted_at = ? WHERE id = ?",
                (now, memory_id),
            )
        await connection.rollback()
        columns = await (
            await connection.execute("PRAGMA table_info(memory_tombstones)")
        ).fetchall()

    assert "content" not in {row[1] for row in columns}


@pytest.mark.asyncio
async def test_same_normalized_alias_can_represent_distinct_people(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    now = datetime.now(UTC).isoformat()

    async with database.connect() as connection:
        for suffix in ("friend", "colleague"):
            entity_id = f"entity_{suffix}"
            await connection.execute(
                """
                INSERT INTO entities(
                    id, display_name, entity_type, resolution_status, created_at, updated_at
                ) VALUES (?, 'Alex', 'person', 'unresolved', ?, ?)
                """,
                (entity_id, now, now),
            )
            await connection.execute(
                """
                INSERT INTO entity_aliases(id, entity_id, alias, normalized_alias, created_at)
                VALUES (?, ?, 'Alex', 'alex', ?)
                """,
                (f"alias_{suffix}", entity_id, now),
            )
        await connection.commit()
        count = await (
            await connection.execute(
                "SELECT COUNT(*) FROM entity_aliases WHERE normalized_alias = 'alex'"
            )
        ).fetchone()

    assert count == (2,)
