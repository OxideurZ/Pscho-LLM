import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Any

import aiosqlite

from backend.app.jobs.models import JobKind, JobRecord
from backend.app.jobs.repository import JobRepository

JobRunner = Callable[[JobRecord, asyncio.Event], Awaitable[Any]]
JobPersister = Callable[[aiosqlite.Connection, JobRecord, Any], Awaitable[None]]


@dataclass
class ActiveBackgroundJob:
    job: JobRecord
    cancel_event: asyncio.Event
    task: asyncio.Task[None]


class BackgroundJobCoordinator:
    """Run at most one leased background job after interactive idle time."""

    def __init__(
        self,
        repository: JobRepository,
        *,
        idle_seconds: float,
        runner: JobRunner | None = None,
        persister: JobPersister | None = None,
        poll_seconds: float = 1.0,
        clock: Callable[[], float] = monotonic,
        kinds: set[JobKind] | None = None,
    ) -> None:
        if (runner is None) != (persister is None):
            raise ValueError("runner and persister must be configured together")
        self.repository = repository
        self.idle_seconds = idle_seconds
        self.runner = runner
        self.persister = persister
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.kinds = kinds or {
            JobKind.MEMORY_EXTRACT,
            JobKind.MEMORY_CONSOLIDATE,
            JobKind.MEMORY_BACKFILL,
        }
        self._last_interactive_at = clock()
        self._active: ActiveBackgroundJob | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._wakeup = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._shutdown = False

    @property
    def active_job_id(self) -> str | None:
        return self._active.job.id if self._active else None

    async def start(self) -> None:
        if self._loop_task is None:
            self._shutdown = False
            self._loop_task = asyncio.create_task(self._run_loop(), name="background-job-worker")

    async def wait_until_idle(self) -> None:
        await self._idle.wait()

    async def shutdown(self) -> None:
        self._shutdown = True
        self._wakeup.set()
        await self.note_interactive_activity()
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None

    async def note_interactive_activity(self) -> None:
        """Reset idle time and durably invalidate active work before cancelling it."""

        self._last_interactive_at = self.clock()
        async with self._lock:
            active = self._active
            if active is not None:
                token = active.job.execution_token
                if token is not None:
                    await self.repository.preempt(active.job.id, token)
                active.cancel_event.set()
                if not active.task.done():
                    active.task.cancel()
        self._wakeup.set()

    async def run_once(self) -> bool:
        if self.runner is None or self.persister is None or self._shutdown:
            return False
        if self.clock() - self._last_interactive_at < self.idle_seconds:
            return False
        async with self._lock:
            if self._active is not None:
                return False
            job = await self.repository.claim_next(kinds=self.kinds)
            if job is None:
                return False
            cancel_event = asyncio.Event()
            task = asyncio.create_task(
                self._execute(job, cancel_event), name=f"background-{job.kind}-{job.id}"
            )
            self._idle.clear()
            self._active = ActiveBackgroundJob(job, cancel_event, task)
            return True

    async def _execute(self, job: JobRecord, cancel_event: asyncio.Event) -> None:
        token = job.execution_token
        assert token is not None
        try:
            assert self.runner is not None
            result = await self.runner(job, cancel_event)
            if cancel_event.is_set():
                await self.repository.preempt(job.id, token)
                return
            assert self.persister is not None

            async def persist(connection: aiosqlite.Connection) -> None:
                await self.persister(connection, job, result)

            await self.repository.commit_result(job.id, token, persist)
        except asyncio.CancelledError:
            cancel_event.set()
            await self.repository.preempt(job.id, token)
        except Exception as error:
            await self.repository.fail(job.id, token, type(error).__name__)
        finally:
            async with self._lock:
                if self._active is not None and self._active.job.id == job.id:
                    self._active = None
                    self._idle.set()
            self._wakeup.set()

    async def _run_loop(self) -> None:
        while not self._shutdown:
            await self.run_once()
            self._wakeup.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_seconds)
