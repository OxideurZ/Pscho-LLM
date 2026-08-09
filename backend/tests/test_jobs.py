from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.conversations import ConversationRepository
from backend.app.conversations.repository import new_id
from backend.app.db import Database
from backend.app.jobs import JobKind, JobRepository, JobStatus


async def migrated_database(path: Path) -> Database:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    return database


def run_metadata() -> dict[str, object]:
    return {
        "model_name": "model",
        "model_sha256": "a" * 64,
        "backend_name": "llama.cpp",
        "backend_version": "b9637",
        "backend_build": "commit",
        "prompt_id": "conversation_system",
        "prompt_version": "0.1.2",
        "prompt_sha256": "b" * 64,
        "generation_config": {"max_tokens": 800, "seed": 42},
        "seed": 42,
        "app_version": "test",
        "app_git_commit": "commit",
        "runtime_info": {},
        "context_size": 32768,
    }


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_claim_is_leased(tmp_path: Path) -> None:
    repository = JobRepository(await migrated_database(tmp_path / "app.sqlite"))

    first = await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="backfill:one")
    duplicate = await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="backfill:one")
    claimed = await repository.claim_next()

    assert duplicate.id == first.id
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.execution_token is not None
    assert await repository.complete(claimed.id, claimed.execution_token) is True
    assert (await repository.get(claimed.id)).status is JobStatus.COMPLETE  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_startup_recovers_interrupted_job_without_leaving_running(tmp_path: Path) -> None:
    repository = JobRepository(await migrated_database(tmp_path / "app.sqlite"))
    await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="backfill:recover")
    claimed = await repository.claim_next()
    assert claimed is not None

    assert await repository.recover_interrupted() == 1
    recovered = await repository.get(claimed.id)

    assert recovered is not None
    assert recovered.status is JobStatus.RETRY
    assert recovered.error_code == "PROCESS_INTERRUPTED"
    assert recovered.execution_token is None


@pytest.mark.asyncio
async def test_preemption_invalidates_lease_and_does_not_consume_attempt(tmp_path: Path) -> None:
    repository = JobRepository(await migrated_database(tmp_path / "app.sqlite"))
    await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="backfill:preempt")
    claimed = await repository.claim_next()
    assert claimed is not None and claimed.execution_token is not None

    assert await repository.preempt(claimed.id, claimed.execution_token) is True
    preempted = await repository.get(claimed.id)

    assert preempted is not None
    assert preempted.status is JobStatus.RETRY
    assert preempted.attempts == 0
    assert preempted.error_code == "BACKGROUND_PREEMPTED"
    assert await repository.complete(claimed.id, claimed.execution_token) is False


@pytest.mark.asyncio
async def test_retry_is_bounded_by_max_attempts(tmp_path: Path) -> None:
    repository = JobRepository(await migrated_database(tmp_path / "app.sqlite"))
    await repository.enqueue(
        kind=JobKind.MEMORY_BACKFILL,
        dedupe_key="backfill:fail",
        max_attempts=1,
    )
    claimed = await repository.claim_next()
    assert claimed is not None and claimed.execution_token is not None

    assert await repository.fail(claimed.id, claimed.execution_token, "TEST_FAILURE") is True
    failed = await repository.get(claimed.id)

    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "TEST_FAILURE"
    assert await repository.claim_next() is None


@pytest.mark.asyncio
async def test_user_message_and_memory_job_commit_atomically_and_wait_for_chat(
    tmp_path: Path,
) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    conversations = ConversationRepository(database)
    jobs = JobRepository(database)
    conversation = await conversations.create()
    identifiers = {
        "user_message_id": new_id("msg"),
        "assistant_message_id": new_id("msg"),
        "session_id": new_id("session"),
        "run_id": new_id("run"),
    }
    job_id = new_id("job")
    dedupe_key = f"memory_extract:0.1.2:{identifiers['user_message_id']}"

    await conversations.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Je préfère les réponses détaillées.",
        input_type="text",
        ids=identifiers,
        run_metadata=run_metadata(),
        memory_job={
            "id": job_id,
            "priority": 0,
            "dedupe_key": dedupe_key,
            "max_attempts": 3,
        },
    )

    job = await jobs.get(job_id)
    assert job is not None
    assert job.source_message_id == identifiers["user_message_id"]
    assert job.blocked_by_run_id == identifiers["run_id"]
    assert await jobs.claim_next() is None

    async with database.connect() as connection:
        await connection.execute(
            "UPDATE model_runs SET status = 'complete' WHERE id = ?", (identifiers["run_id"],)
        )
        await connection.commit()

    claimed = await jobs.claim_next()
    assert claimed is not None
    assert claimed.id == job_id
