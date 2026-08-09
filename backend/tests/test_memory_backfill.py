from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.jobs import BackgroundJobCoordinator, JobRepository
from backend.app.memory import MemoryExtractor, MemoryRepository
from backend.app.runs import RunRepository
from backend.tests.test_memory_extractor import StructuredBackend


def settings_for(path: Path) -> Settings:
    return Settings(
        data_directory=path.parent / "runtime",
        database_path=path,
        model_expected_sha256="a" * 64,
        security_enabled=False,
        memory_background_idle_seconds=1,
        memory_backfill_enqueue_batch_size=1,
    )


async def seed_historical_user_messages(database: Database) -> tuple[str, list[str]]:
    now = datetime.now(UTC).isoformat()
    async with database.connect() as connection:
        await connection.execute(
            """
            INSERT INTO conversations(id, next_sequence_no, created_at, updated_at)
            VALUES ('conv_backfill', 5, ?, ?)
            """,
            (now, now),
        )
        for sequence, message_id in ((1, "msg_backfill_1"), (3, "msg_backfill_2")):
            await connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, sequence_no, role, content, input_type,
                    status, created_at, updated_at
                ) VALUES (?, 'conv_backfill', ?, 'user', ?, 'text', 'complete', ?, ?)
                """,
                (message_id, sequence, "Je préfère les réponses détaillées.", now, now),
            )
        await connection.commit()
    return "conv_backfill", ["msg_backfill_1", "msg_backfill_2"]


@pytest.mark.asyncio
async def test_opt_in_backfill_previews_batches_and_completes_without_duplicates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite"
    settings = settings_for(path)
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    conversation_id, message_ids = await seed_historical_user_messages(database)
    memory = MemoryRepository(database)

    preview = await memory.preview_backfill(
        conversation_ids=[conversation_id],
        created_after=None,
        created_before=None,
        all_eligible=False,
        extractor_version=settings.memory_extraction_prompt_version,
    )
    assert preview["conversations"] == 1
    assert preview["eligible_messages"] == 2

    backfill = await memory.start_backfill(
        preview=preview,
        batch_size=settings.memory_backfill_enqueue_batch_size,
        max_attempts=settings.memory_job_max_attempts,
    )
    extractor = MemoryExtractor(
        settings,
        StructuredBackend("Je préfère les réponses détaillées."),
        memory,
        RunRepository(path),
        REPOSITORY_ROOT,
    )
    coordinator = BackgroundJobCoordinator(
        JobRepository(database), idle_seconds=0, runner=extractor.run, persister=extractor.persist
    )
    for _ in range(8):
        if not await coordinator.run_once():
            break
        await coordinator.wait_until_idle()

    progress = await memory.backfill_progress(backfill["id"])
    assert progress == {
        "id": backfill["id"],
        "status": "complete",
        "scope_kind": "conversations",
        "eligible": 2,
        "processed": 2,
        "pending": 0,
        "failed": 0,
        "memories_created": 2,
        "created_at": progress["created_at"],
    }
    async with database.connect() as connection:
        source_ids = await (
            await connection.execute(
                "SELECT DISTINCT source_message_id FROM jobs WHERE backfill_id = ?",
                (backfill["id"],),
            )
        ).fetchall()
    assert {row[0] for row in source_ids if row[0] is not None} == set(message_ids)


@pytest.mark.asyncio
async def test_memory_audit_is_aggregate_and_reports_no_assistant_contamination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite"
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    await seed_historical_user_messages(database)
    metrics = await MemoryRepository(database).audit_metrics()

    assert metrics["user_messages_processed"] == 0
    assert metrics["assistant_contamination_count"] == 0
    assert metrics["wrong_entity_merge_count"] == 0
    assert "content" not in metrics
