from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite

from backend.app.db import Database
from backend.app.jobs.models import JobKind, JobRecord, JobStatus


def new_job_id() -> str:
    return f"job_{uuid4().hex}"


class JobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def enqueue(
        self,
        *,
        kind: JobKind,
        dedupe_key: str,
        source_message_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        backfill_id: str | None = None,
        blocked_by_run_id: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: str | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        now = datetime.now(UTC).isoformat()
        identifier = job_id or new_job_id()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute(
                """
                INSERT INTO jobs(
                    id, kind, status, priority, dedupe_key, source_message_id,
                    source_type, source_id, backfill_id, blocked_by_run_id,
                    attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (
                    identifier,
                    kind.value,
                    priority,
                    dedupe_key,
                    source_message_id,
                    source_type,
                    source_id,
                    backfill_id,
                    blocked_by_run_id,
                    max_attempts,
                    available_at or now,
                    now,
                    now,
                ),
            )
            await connection.commit()
            row = await (
                await connection.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (dedupe_key,))
            ).fetchone()
        assert row is not None
        return JobRecord.model_validate(dict(row))

    async def get(self, job_id: str) -> JobRecord | None:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            row = await (
                await connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            ).fetchone()
        return JobRecord.model_validate(dict(row)) if row is not None else None

    async def claim_next(self, *, now: str | None = None) -> JobRecord | None:
        claimed_at = now or datetime.now(UTC).isoformat()
        token = f"lease_{uuid4().hex}"
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await connection.execute(
                        """
                        SELECT j.* FROM jobs AS j
                        WHERE j.status IN ('pending', 'retry')
                          AND j.available_at <= ?
                          AND j.attempts < j.max_attempts
                          AND (
                              j.blocked_by_run_id IS NULL
                              OR NOT EXISTS (
                                  SELECT 1 FROM model_runs AS r
                                  WHERE r.id = j.blocked_by_run_id
                                    AND r.status IN ('starting', 'generating')
                              )
                          )
                        ORDER BY j.priority DESC, j.created_at ASC, j.id ASC
                        LIMIT 1
                        """,
                        (claimed_at,),
                    )
                ).fetchone()
                if row is None:
                    await connection.commit()
                    return None
                await connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'running', attempts = attempts + 1,
                        started_at = ?, completed_at = NULL, error_code = NULL,
                        execution_token = ?, cancel_requested_at = NULL, updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'retry')
                    """,
                    (claimed_at, token, claimed_at, row["id"]),
                )
                claimed = await (
                    await connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],))
                ).fetchone()
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        assert claimed is not None
        return JobRecord.model_validate(dict(claimed))

    async def complete(self, job_id: str, execution_token: str) -> bool:
        async def no_result(_connection: aiosqlite.Connection) -> None:
            return None

        return await self.commit_result(job_id, execution_token, no_result)

    async def commit_result(
        self,
        job_id: str,
        execution_token: str,
        persist: Callable[[aiosqlite.Connection], Awaitable[None]],
    ) -> bool:
        """Atomically validate the lease, persist output, and complete the job."""

        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                lease = await (
                    await connection.execute(
                        """
                        SELECT id FROM jobs
                        WHERE id = ? AND status = 'running' AND execution_token = ?
                        """,
                        (job_id, execution_token),
                    )
                ).fetchone()
                if lease is None:
                    await connection.commit()
                    return False
                await persist(connection)
                cursor = await connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'complete', completed_at = ?, execution_token = NULL,
                        error_code = NULL, updated_at = ?
                    WHERE id = ? AND status = 'running' AND execution_token = ?
                    """,
                    (now, now, job_id, execution_token),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("job lease changed inside result transaction")
                await connection.commit()
                return True
            except Exception:
                await connection.rollback()
                raise

    async def fail(self, job_id: str, execution_token: str, error_code: str) -> bool:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await connection.execute(
                        """
                        SELECT attempts, max_attempts FROM jobs
                        WHERE id = ? AND status = 'running' AND execution_token = ?
                        """,
                        (job_id, execution_token),
                    )
                ).fetchone()
                if row is None:
                    await connection.commit()
                    return False
                terminal = int(row["attempts"]) >= int(row["max_attempts"])
                await connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, available_at = ?, started_at = NULL,
                        completed_at = ?, execution_token = NULL, error_code = ?, updated_at = ?
                    WHERE id = ? AND status = 'running' AND execution_token = ?
                    """,
                    (
                        JobStatus.FAILED.value if terminal else JobStatus.RETRY.value,
                        now,
                        now if terminal else None,
                        error_code,
                        now,
                        job_id,
                        execution_token,
                    ),
                )
                await connection.commit()
                return True
            except Exception:
                await connection.rollback()
                raise

    async def preempt(self, job_id: str, execution_token: str) -> bool:
        """Invalidate a lease before cancelling work; preemption does not consume an attempt."""

        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                UPDATE jobs
                SET status = 'retry',
                    attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,
                    available_at = ?, started_at = NULL, completed_at = NULL,
                    execution_token = NULL, cancel_requested_at = ?,
                    error_code = 'BACKGROUND_PREEMPTED', updated_at = ?
                WHERE id = ? AND status = 'running' AND execution_token = ?
                """,
                (now, now, now, job_id, execution_token),
            )
            await connection.commit()
        return cursor.rowcount == 1

    async def recover_interrupted(self) -> int:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                UPDATE jobs
                SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retry' END,
                    available_at = ?, started_at = NULL,
                    completed_at = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END,
                    execution_token = NULL, error_code = 'PROCESS_INTERRUPTED', updated_at = ?
                WHERE status = 'running'
                """,
                (now, now, now),
            )
            await connection.commit()
        return cursor.rowcount
