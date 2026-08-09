import asyncio
from pathlib import Path

import aiosqlite
import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.jobs import BackgroundJobCoordinator, JobKind, JobRepository, JobStatus


async def repository_for(path: Path) -> JobRepository:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    return JobRepository(database)


@pytest.mark.asyncio
async def test_coordinator_waits_for_idle_and_runs_one_job(tmp_path: Path) -> None:
    repository = await repository_for(tmp_path / "app.sqlite")
    await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="one")
    clock = [10.0]
    release = asyncio.Event()
    started = asyncio.Event()
    persisted: list[str] = []

    async def runner(_job, _cancel_event):  # type: ignore[no-untyped-def]
        started.set()
        await release.wait()
        return "result"

    async def persister(_connection, job, result):  # type: ignore[no-untyped-def]
        persisted.append(f"{job.id}:{result}")

    coordinator = BackgroundJobCoordinator(
        repository,
        idle_seconds=120,
        runner=runner,
        persister=persister,
        clock=lambda: clock[0],
    )

    assert await coordinator.run_once() is False
    clock[0] = 130.0
    assert await coordinator.run_once() is True
    await started.wait()
    assert await coordinator.run_once() is False
    release.set()
    await coordinator.wait_until_idle()

    assert len(persisted) == 1
    job = await repository.claim_next()
    assert job is None


@pytest.mark.asyncio
async def test_interactive_activity_discards_late_preempted_result(tmp_path: Path) -> None:
    repository = await repository_for(tmp_path / "app.sqlite")
    queued = await repository.enqueue(kind=JobKind.MEMORY_BACKFILL, dedupe_key="late")
    clock = [200.0]
    running = asyncio.Event()
    persisted = False

    async def runner(_job, _cancel_event):  # type: ignore[no-untyped-def]
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "late-result"

    async def persister(
        _connection: aiosqlite.Connection,
        _job,
        _result,  # type: ignore[no-untyped-def]
    ) -> None:
        nonlocal persisted
        persisted = True

    coordinator = BackgroundJobCoordinator(
        repository,
        idle_seconds=0,
        runner=runner,
        persister=persister,
        clock=lambda: clock[0],
    )
    assert await coordinator.run_once() is True
    await running.wait()

    clock[0] = 201.0
    await coordinator.note_interactive_activity()
    await coordinator.wait_until_idle()
    job = await repository.get(queued.id)

    assert persisted is False
    assert job is not None
    assert job.status is JobStatus.RETRY
    assert job.attempts == 0
    assert job.error_code == "BACKGROUND_PREEMPTED"


@pytest.mark.asyncio
async def test_result_persistence_rolls_back_if_writer_fails(tmp_path: Path) -> None:
    repository = await repository_for(tmp_path / "app.sqlite")
    queued = await repository.enqueue(
        kind=JobKind.MEMORY_BACKFILL, dedupe_key="writer-failure", max_attempts=1
    )

    async def runner(_job, _cancel_event):  # type: ignore[no-untyped-def]
        return "result"

    async def persister(connection, _job, _result):  # type: ignore[no-untyped-def]
        await connection.execute(
            "UPDATE jobs SET error_code = 'SHOULD_ROLL_BACK' WHERE id = ?", (queued.id,)
        )
        raise RuntimeError("persist failed")

    coordinator = BackgroundJobCoordinator(
        repository, idle_seconds=0, runner=runner, persister=persister
    )
    assert await coordinator.run_once() is True
    await coordinator.wait_until_idle()
    job = await repository.get(queued.id)

    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_code == "RuntimeError"
