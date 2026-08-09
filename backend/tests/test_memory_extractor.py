import json
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest

from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.conversations import ConversationRepository
from backend.app.conversations.repository import new_id
from backend.app.db import Database
from backend.app.jobs import BackgroundJobCoordinator, JobRepository
from backend.app.memory import MemoryExtractor, MemoryRepository
from backend.app.runs import RunRepository
from backend.tests.fakes import FakeBackend


def settings_for(path: Path) -> Settings:
    return Settings(
        data_directory=path.parent / "runtime",
        database_path=path,
        model_expected_sha256="a" * 64,
        security_enabled=False,
        memory_background_idle_seconds=1,
    )


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


class StructuredBackend(FakeBackend):
    def __init__(self, quote: str) -> None:
        super().__init__()
        self.quote = quote
        self.structured_requests = []

    async def generate_structured(  # type: ignore[no-untyped-def]
        self, messages, schema, cancel_event=None
    ):
        self.structured_requests.append(messages)
        target = json.loads(messages[1].content)["user_messages"][-1]
        return schema.model_validate(
            {
                "schema_version": "1.0",
                "candidates": [
                    {
                        "kind": "preference",
                        "content": "L'utilisateur préfère les réponses détaillées.",
                        "epistemic_status": "stated",
                        "source_spans": [
                            {
                                "message_id": target["message_id"],
                                "start_char": 0,
                                "end_char": max(len(self.quote) - 1, 1),
                                "quote": self.quote,
                            }
                        ],
                        "time": {
                            "text": None,
                            "start_at": None,
                            "end_at": None,
                            "precision": None,
                        },
                        "entities": [],
                    }
                ],
            }
        )


async def queued_turn(path: Path) -> tuple[Database, dict[str, str]]:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    conversations = ConversationRepository(database)
    conversation = await conversations.create()
    identifiers = {
        "conversation_id": conversation.id,
        "user_message_id": new_id("msg"),
        "assistant_message_id": new_id("msg"),
        "session_id": new_id("session"),
        "run_id": new_id("run"),
        "job_id": new_id("job"),
    }
    await conversations.begin_turn(
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
    async with database.connect() as connection:
        await connection.execute(
            "UPDATE model_runs SET status = 'complete' WHERE id = ?", (identifiers["run_id"],)
        )
        await connection.execute(
            "UPDATE messages SET status = 'complete', content = 'Réponse.' WHERE id = ?",
            (identifiers["assistant_message_id"],),
        )
        await connection.commit()
    return database, identifiers


async def run_extractor(database: Database, backend: StructuredBackend, settings: Settings) -> None:
    jobs = JobRepository(database)
    memory = MemoryRepository(database)
    extractor = MemoryExtractor(
        settings,
        backend,
        memory,
        RunRepository(settings.database_path),
        REPOSITORY_ROOT,
    )
    coordinator = BackgroundJobCoordinator(
        jobs,
        idle_seconds=0,
        runner=extractor.run,
        persister=extractor.persist,
    )
    assert await coordinator.run_once() is True
    await coordinator.wait_until_idle()


@pytest.mark.asyncio
async def test_extractor_uses_user_only_context_and_creates_active_memory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite"
    database, ids = await queued_turn(path)
    backend = StructuredBackend("Je préfère les réponses détaillées.")

    await run_extractor(database, backend, settings_for(path))

    payload = json.loads(backend.structured_requests[0][1].content)
    assert payload["target_message_id"] == ids["user_message_id"]
    assert [item["content"] for item in payload["user_messages"]] == [
        "Je préfère les réponses détaillées."
    ]
    async with database.connect() as connection:
        connection.row_factory = aiosqlite.Row
        candidate = await (await connection.execute("SELECT * FROM memory_candidates")).fetchone()
        memory = await (await connection.execute("SELECT * FROM memory_items")).fetchone()
        source = await (await connection.execute("SELECT * FROM memory_sources")).fetchone()
        run = await (
            await connection.execute(
                """
                SELECT run_kind, status, prompt_version FROM model_runs
                WHERE run_kind = 'memory_extract'
                """
            )
        ).fetchone()

    assert candidate["status"] == "accepted"
    assert memory["status"] == "active"
    assert source["message_id"] == ids["user_message_id"]
    assert source["end_char"] == len("Je préfère les réponses détaillées.")
    assert tuple(run) == ("memory_extract", "complete", "0.1.2")


@pytest.mark.asyncio
async def test_invalid_exact_quote_is_audited_without_creating_memory(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"
    database, _ids = await queued_turn(path)

    await run_extractor(database, StructuredBackend("Information absente"), settings_for(path))

    async with database.connect() as connection:
        candidate = await (
            await connection.execute("SELECT status, rejection_code FROM memory_candidates")
        ).fetchone()
        memory_count = await (
            await connection.execute("SELECT COUNT(*) FROM memory_items")
        ).fetchone()

    assert candidate == ("invalid", "SOURCE_SPAN_MISMATCH")
    assert memory_count == (0,)


@pytest.mark.asyncio
async def test_exact_duplicate_merges_into_one_active_memory_with_two_sources(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite"
    database, first_ids = await queued_turn(path)
    await run_extractor(
        database,
        StructuredBackend("Je préfère les réponses détaillées."),
        settings_for(path),
    )
    conversations = ConversationRepository(database)
    second_ids = {
        "user_message_id": new_id("msg"),
        "assistant_message_id": new_id("msg"),
        "session_id": new_id("session"),
        "run_id": new_id("run"),
        "job_id": new_id("job"),
    }
    await conversations.begin_turn(
        conversation_id=first_ids["conversation_id"],
        client_turn_id=str(uuid4()),
        content="Je préfère les réponses détaillées.",
        input_type="text",
        ids=second_ids,
        run_metadata=metadata(),
        memory_job={
            "id": second_ids["job_id"],
            "priority": 0,
            "dedupe_key": f"memory_extract:0.1.2:{second_ids['user_message_id']}",
            "max_attempts": 3,
        },
    )
    async with database.connect() as connection:
        await connection.execute(
            "UPDATE model_runs SET status = 'complete' WHERE id = ?", (second_ids["run_id"],)
        )
        await connection.execute(
            "UPDATE messages SET status = 'complete', content = 'Réponse.' WHERE id = ?",
            (second_ids["assistant_message_id"],),
        )
        await connection.commit()

    await run_extractor(
        database,
        StructuredBackend("Je préfère les réponses détaillées."),
        settings_for(path),
    )

    async with database.connect() as connection:
        statuses = await (
            await connection.execute(
                "SELECT status, COUNT(*) FROM memory_items GROUP BY status ORDER BY status"
            )
        ).fetchall()
        active_sources = await (
            await connection.execute(
                """
                SELECT COUNT(*) FROM memory_sources AS source
                JOIN memory_items AS memory ON memory.id = source.memory_id
                WHERE memory.status = 'active'
                """
            )
        ).fetchone()

    assert statuses == [("active", 1), ("merged", 1)]
    assert active_sources == (2,)
