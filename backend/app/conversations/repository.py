from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite

from backend.app.conversations.models import Conversation, Message, Turn
from backend.app.db import Database


class ConversationNotFoundError(LookupError):
    code = "CONVERSATION_NOT_FOUND"


class ConversationDeletedError(LookupError):
    code = "CONVERSATION_DELETED"


class ConversationBusyError(RuntimeError):
    code = "CONVERSATION_BUSY"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ConversationRepository:
    def __init__(self, database: Database, session_timeout_seconds: int = 1800) -> None:
        self.database = database
        self.session_timeout = timedelta(seconds=session_timeout_seconds)

    async def create(self, title: str | None = None) -> Conversation:
        now = datetime.now(UTC).isoformat()
        conversation_id = new_id("conv")
        async with self.database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO conversations(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, title, now, now),
            )
            await connection.commit()
        conversation = await self.get(conversation_id)
        assert conversation is not None
        return conversation

    async def get(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            row = await (
                await connection.execute(
                    "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
                )
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        if row["deleted_at"] is not None and not include_deleted:
            raise ConversationDeletedError(conversation_id)
        return Conversation.model_validate(dict(row))

    async def list(
        self, *, limit: int, offset: int, include_archived: bool = False
    ) -> list[Conversation]:
        conditions = ["deleted_at IS NULL"]
        if not include_archived:
            conditions.append("archived_at IS NULL")
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    f"""
                    SELECT * FROM conversations
                    WHERE {" AND ".join(conditions)}
                    ORDER BY updated_at DESC, id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            ).fetchall()
        return [Conversation.model_validate(dict(row)) for row in rows]

    async def update(
        self,
        conversation_id: str,
        *,
        title: str | None | object = ...,
        archived: bool | None = None,
    ) -> Conversation:
        conversation = await self.get(conversation_id)
        now = datetime.now(UTC).isoformat()
        new_title = conversation.title if title is ... else title
        archived_at = conversation.archived_at
        if archived is True and archived_at is None:
            archived_at = now
        elif archived is False:
            archived_at = None
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE conversations
                SET title = ?, archived_at = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (new_title, archived_at, now, conversation_id),
            )
            await connection.commit()
        return await self.get(conversation_id)

    async def soft_delete(self, conversation_id: str) -> None:
        await self.get(conversation_id)
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE conversations SET deleted_at = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (now, now, conversation_id),
            )
            await connection.commit()

    async def messages(
        self, conversation_id: str, *, after_sequence_no: int = 0, limit: int = 100
    ) -> list[Message]:
        await self.get(conversation_id)
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ? AND sequence_no > ?
                    ORDER BY sequence_no ASC
                    LIMIT ?
                    """,
                    (conversation_id, after_sequence_no, limit),
                )
            ).fetchall()
        return [self._message(row) for row in rows]

    async def context_messages(
        self, conversation_id: str, current_message_id: str
    ) -> list[Message]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            current = await (
                await connection.execute(
                    """
                    SELECT sequence_no FROM messages
                    WHERE id = ? AND conversation_id = ? AND role = 'user'
                    """,
                    (current_message_id, conversation_id),
                )
            ).fetchone()
            if current is None:
                raise ValueError("Current USER message is not in this conversation")
            rows = await (
                await connection.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                      AND sequence_no <= ?
                      AND excluded_from_ai = 0
                      AND status != 'deleted'
                      AND (
                        (role = 'user' AND status = 'complete')
                        OR
                        (role = 'assistant' AND (
                          status IN ('complete', 'interrupted')
                          OR (status = 'failed' AND content != '')
                        ))
                      )
                    ORDER BY sequence_no ASC
                    """,
                    (conversation_id, current["sequence_no"]),
                )
            ).fetchall()
        return [self._message(row) for row in rows]

    async def mark_generating(self, run_id: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                "UPDATE model_runs SET status = 'generating' WHERE id = ? AND status = 'starting'",
                (run_id,),
            )
            await connection.commit()

    async def checkpoint(self, assistant_message_id: str, content: str) -> None:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE messages SET content = ?, updated_at = ?
                WHERE id = ? AND status = 'streaming'
                """,
                (content, now, assistant_message_id),
            )
            await connection.commit()

    async def update_run_metrics(self, run_id: str, metrics: dict[str, Any]) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE model_runs SET
                    input_tokens = ?, output_tokens = ?, ttft_ms = ?, prompt_eval_ms = ?,
                    generation_ms = ?, total_ms = ?, tokens_per_second = ?
                WHERE id = ?
                """,
                (
                    metrics.get("input_tokens"),
                    metrics.get("output_tokens"),
                    metrics.get("ttft_ms"),
                    metrics.get("prompt_eval_ms"),
                    metrics.get("generation_ms"),
                    metrics.get("total_ms"),
                    metrics.get("tokens_per_second"),
                    run_id,
                ),
            )
            await connection.commit()

    async def finalize(
        self,
        *,
        conversation_id: str,
        assistant_message_id: str,
        run_id: str,
        content: str,
        message_status: str,
        run_status: str,
        error_code: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    UPDATE messages SET content = ?, status = ?, updated_at = ?
                    WHERE id = ? AND model_run_id = ?
                    """,
                    (content, message_status, now, assistant_message_id, run_id),
                )
                await connection.execute(
                    """
                    UPDATE model_runs
                    SET status = ?, completed_at = ?, error_code = ?
                    WHERE id = ?
                    """,
                    (run_status, now, error_code, run_id),
                )
                await connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
                await connection.execute(
                    """
                    UPDATE sessions SET last_activity_at = ?
                    WHERE id = (SELECT session_id FROM messages WHERE id = ?)
                    """,
                    (now, assistant_message_id),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def reconcile_interrupted_process(self) -> tuple[int, int]:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """
                    UPDATE messages
                    SET status = 'failed', updated_at = ?
                    WHERE status = 'streaming'
                      AND model_run_id IN (
                        SELECT id FROM model_runs WHERE status IN ('starting', 'generating')
                      )
                    """,
                    (now,),
                )
                messages_repaired = cursor.rowcount
                cursor = await connection.execute(
                    """
                    UPDATE model_runs
                    SET status = 'failed', completed_at = ?, error_code = 'PROCESS_INTERRUPTED'
                    WHERE status IN ('starting', 'generating')
                    """,
                    (now,),
                )
                runs_repaired = cursor.rowcount
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return runs_repaired, messages_repaired

    async def latest_summary(self, conversation_id: str) -> dict[str, Any] | None:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            row = await (
                await connection.execute(
                    """
                    SELECT * FROM summaries
                    WHERE conversation_id = ? AND summary_type = 'rolling'
                    ORDER BY created_at DESC, id DESC LIMIT 1
                    """,
                    (conversation_id,),
                )
            ).fetchone()
        return dict(row) if row else None

    async def summary_covered_message_ids(self, summary_id: str) -> set[str]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    WITH RECURSIVE lineage(id, parent_summary_id) AS (
                        SELECT id, parent_summary_id FROM summaries WHERE id = ?
                        UNION ALL
                        SELECT s.id, s.parent_summary_id
                        FROM summaries s JOIN lineage l ON s.id = l.parent_summary_id
                    )
                    SELECT DISTINCT ss.message_id
                    FROM summary_sources ss JOIN lineage l ON l.id = ss.summary_id
                    """,
                    (summary_id,),
                )
            ).fetchall()
        return {str(row[0]) for row in rows}

    async def create_summary(
        self,
        *,
        summary_id: str,
        conversation_id: str,
        parent_summary_id: str | None,
        content_json: str,
        prompt_id: str,
        prompt_version: str,
        prompt_sha256: str,
        model_run_id: str,
        source_message_ids: list[str],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO summaries(
                        id, conversation_id, summary_type, schema_version,
                        parent_summary_id, content_json, prompt_id, prompt_version,
                        prompt_sha256, model_run_id, created_at
                    ) VALUES (?, ?, 'rolling', '1.0', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary_id,
                        conversation_id,
                        parent_summary_id,
                        content_json,
                        prompt_id,
                        prompt_version,
                        prompt_sha256,
                        model_run_id,
                        now,
                    ),
                )
                for message_id in source_message_ids:
                    await connection.execute(
                        "INSERT INTO summary_sources(summary_id, message_id) VALUES (?, ?)",
                        (summary_id, message_id),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def summary_lineage(self, summary_id: str) -> list[dict[str, Any]]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    """
                    WITH RECURSIVE lineage(id, parent_summary_id, depth) AS (
                        SELECT id, parent_summary_id, 0 FROM summaries WHERE id = ?
                        UNION ALL
                        SELECT s.id, s.parent_summary_id, l.depth + 1
                        FROM summaries s JOIN lineage l ON s.id = l.parent_summary_id
                    )
                    SELECT s.*, l.depth FROM summaries s JOIN lineage l ON s.id = l.id
                    ORDER BY l.depth DESC
                    """,
                    (summary_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def begin_turn(
        self,
        *,
        conversation_id: str,
        client_turn_id: str,
        content: str,
        input_type: str,
        ids: dict[str, str],
        run_metadata: dict[str, Any],
        memory_job: dict[str, Any] | None = None,
    ) -> Turn:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._find_turn(connection, conversation_id, client_turn_id)
                if existing is not None:
                    await connection.commit()
                    return existing

                conversation = await (
                    await connection.execute(
                        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
                    )
                ).fetchone()
                if conversation is None:
                    raise ConversationNotFoundError(conversation_id)
                if conversation["deleted_at"] is not None:
                    raise ConversationDeletedError(conversation_id)

                active = await (
                    await connection.execute(
                        """
                        SELECT u.client_turn_id
                        FROM messages AS a
                        JOIN messages AS u
                          ON u.conversation_id = a.conversation_id
                         AND u.sequence_no = a.sequence_no - 1
                        WHERE a.conversation_id = ?
                          AND a.role = 'assistant'
                          AND a.status = 'streaming'
                        LIMIT 1
                        """,
                        (conversation_id,),
                    )
                ).fetchone()
                if active is not None and active["client_turn_id"] != client_turn_id:
                    raise ConversationBusyError(conversation_id)

                session_id = await self._resolve_session(
                    connection, conversation_id, now_dt, ids["session_id"]
                )
                user_sequence = int(conversation["next_sequence_no"])
                assistant_sequence = user_sequence + 1
                await connection.execute(
                    """
                    INSERT INTO messages(
                        id, conversation_id, session_id, sequence_no, role, content,
                        input_type, status, created_at, updated_at, client_turn_id
                    ) VALUES (?, ?, ?, ?, 'user', ?, ?, 'complete', ?, ?, ?)
                    """,
                    (
                        ids["user_message_id"],
                        conversation_id,
                        session_id,
                        user_sequence,
                        content,
                        input_type,
                        now,
                        now,
                        client_turn_id,
                    ),
                )
                await self._insert_model_run(connection, ids["run_id"], run_metadata, now)
                if memory_job is not None:
                    await connection.execute(
                        """
                        INSERT INTO jobs(
                            id, kind, status, priority, dedupe_key, source_message_id,
                            blocked_by_run_id, attempts, max_attempts, available_at,
                            created_at, updated_at
                        ) VALUES (?, 'memory_extract', 'pending', ?, ?, ?, ?, 0, ?, ?, ?, ?)
                        ON CONFLICT(dedupe_key) DO NOTHING
                        """,
                        (
                            memory_job["id"],
                            memory_job["priority"],
                            memory_job["dedupe_key"],
                            ids["user_message_id"],
                            ids["run_id"],
                            memory_job["max_attempts"],
                            now,
                            now,
                            now,
                        ),
                    )
                await connection.execute(
                    """
                    INSERT INTO messages(
                        id, conversation_id, session_id, sequence_no, role, content,
                        input_type, status, created_at, updated_at, model_run_id
                    ) VALUES (?, ?, ?, ?, 'assistant', '', 'generated', 'streaming', ?, ?, ?)
                    """,
                    (
                        ids["assistant_message_id"],
                        conversation_id,
                        session_id,
                        assistant_sequence,
                        now,
                        now,
                        ids["run_id"],
                    ),
                )
                await connection.execute(
                    """
                    UPDATE conversations
                    SET next_sequence_no = next_sequence_no + 2, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, conversation_id),
                )
                await connection.execute(
                    "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
                    (now, session_id),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
            turn = await self._find_turn(connection, conversation_id, client_turn_id)
            assert turn is not None
            return turn.model_copy(update={"created": True})

    async def _find_turn(
        self, connection: aiosqlite.Connection, conversation_id: str, client_turn_id: str
    ) -> Turn | None:
        row = await (
            await connection.execute(
                """
                SELECT
                    u.id AS user_id,
                    a.id AS assistant_id,
                    a.model_run_id AS run_id,
                    r.status AS run_status,
                    r.error_code AS run_error_code
                FROM messages AS u
                JOIN messages AS a
                  ON a.conversation_id = u.conversation_id
                 AND a.sequence_no = u.sequence_no + 1
                JOIN model_runs AS r ON r.id = a.model_run_id
                WHERE u.conversation_id = ? AND u.client_turn_id = ?
                """,
                (conversation_id, client_turn_id),
            )
        ).fetchone()
        if row is None:
            return None
        user = await (
            await connection.execute("SELECT * FROM messages WHERE id = ?", (row["user_id"],))
        ).fetchone()
        assistant = await (
            await connection.execute("SELECT * FROM messages WHERE id = ?", (row["assistant_id"],))
        ).fetchone()
        return Turn(
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            user_message=self._message(user),
            assistant_message=self._message(assistant),
            run_id=row["run_id"],
            run_status=row["run_status"],
            run_error_code=row["run_error_code"],
            created=False,
        )

    async def _resolve_session(
        self,
        connection: aiosqlite.Connection,
        conversation_id: str,
        now: datetime,
        new_session_id: str,
    ) -> str:
        row = await (
            await connection.execute(
                """
                SELECT * FROM sessions
                WHERE conversation_id = ? AND ended_at IS NULL
                ORDER BY last_activity_at DESC LIMIT 1
                """,
                (conversation_id,),
            )
        ).fetchone()
        if row is not None:
            last_activity = datetime.fromisoformat(row["last_activity_at"])
            if now - last_activity <= self.session_timeout:
                return str(row["id"])
            await connection.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (now.isoformat(), row["id"])
            )
        await connection.execute(
            """
            INSERT INTO sessions(id, conversation_id, started_at, last_activity_at)
            VALUES (?, ?, ?, ?)
            """,
            (new_session_id, conversation_id, now.isoformat(), now.isoformat()),
        )
        return new_session_id

    async def _insert_model_run(
        self,
        connection: aiosqlite.Connection,
        run_id: str,
        metadata: dict[str, Any],
        started_at: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO model_runs(
                id, status, run_kind, model_name, model_sha256, backend_name,
                backend_version, backend_build, prompt_id, prompt_version,
                prompt_sha256, generation_config_json, seed, app_version,
                app_git_commit, runtime_info_json, context_size, started_at
            ) VALUES (?, 'starting', 'chat', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                metadata["model_name"],
                metadata["model_sha256"],
                metadata["backend_name"],
                metadata["backend_version"],
                metadata.get("backend_build"),
                metadata["prompt_id"],
                metadata["prompt_version"],
                metadata["prompt_sha256"],
                json.dumps(metadata["generation_config"], sort_keys=True, separators=(",", ":")),
                metadata.get("seed"),
                metadata["app_version"],
                metadata.get("app_git_commit"),
                json.dumps(metadata["runtime_info"], sort_keys=True, separators=(",", ":")),
                metadata["context_size"],
                started_at,
            ),
        )

    @staticmethod
    def _message(row: aiosqlite.Row) -> Message:
        values = dict(row)
        values["excluded_from_ai"] = bool(values["excluded_from_ai"])
        return Message.model_validate(values)
