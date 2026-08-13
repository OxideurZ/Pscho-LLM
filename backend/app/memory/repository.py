# ruff: noqa: E501

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

import aiosqlite

from backend.app.db import Database
from backend.app.jobs import JobKind, JobRecord
from backend.app.memory.grounding import GroundingError, canonicalize_source_spans
from backend.app.memory.models import MemoryCandidateDraft, MemoryExtractionResult


def memory_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def normalize_level0(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(without_punctuation.split())


def normalize_alias(alias: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", alias).casefold().split())


@dataclass(frozen=True)
class MemoryUserSource:
    id: str
    content: str
    created_at: str
    input_type: str
    is_target: bool


@dataclass(frozen=True)
class MemoryExtractionBatch:
    run_id: str
    target_message_id: str
    sources: tuple[MemoryUserSource, ...]
    extraction: MemoryExtractionResult


@dataclass(frozen=True)
class MemoryBackfillBatch:
    backfill_id: str
    message_ids: tuple[str, ...]


class MemorySourceIneligibleError(RuntimeError):
    code = "MEMORY_SOURCE_INELIGIBLE"


class MemoryNotFoundError(LookupError):
    code = "MEMORY_NOT_FOUND"


class MemoryStateConflictError(RuntimeError):
    code = "MEMORY_STATE_CONFLICT"


class EntityNotFoundError(LookupError):
    code = "ENTITY_NOT_FOUND"


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _backfill_conditions(
        *,
        conversation_ids: list[str],
        created_after: str | None,
        created_before: str,
        extractor_version: str,
    ) -> tuple[str, list[object]]:
        conditions = [
            "message.role = 'user'",
            "message.status = 'complete'",
            "message.excluded_from_ai = 0",
            "conversation.deleted_at IS NULL",
            "message.created_at <= ?",
            "NOT EXISTS ("
            "SELECT 1 FROM jobs AS prior "
            "WHERE prior.kind = 'memory_extract' "
            "AND prior.source_message_id = message.id "
            "AND prior.dedupe_key = ?"
            ")",
        ]
        parameters: list[object] = [created_before, f"memory_extract:{extractor_version}:"]
        # The source id is appended by SQLite; concatenation keeps the version-specific dedupe exact.
        conditions[-1] = conditions[-1].replace(
            "prior.dedupe_key = ?", "prior.dedupe_key = ? || message.id"
        )
        if created_after is not None:
            conditions.append("message.created_at >= ?")
            parameters.append(created_after)
        if conversation_ids:
            placeholders = ", ".join("?" for _ in conversation_ids)
            conditions.append(f"message.conversation_id IN ({placeholders})")
            parameters.extend(conversation_ids)
        return " AND ".join(conditions), parameters

    async def preview_backfill(
        self,
        *,
        conversation_ids: list[str],
        created_after: str | None,
        created_before: str | None,
        all_eligible: bool,
        extractor_version: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        scope_kind = (
            "conversations"
            if conversation_ids
            else "date_range"
            if (created_after or created_before)
            else "all_eligible"
        )
        before = created_before or now
        conditions, parameters = self._backfill_conditions(
            conversation_ids=conversation_ids,
            created_after=created_after,
            created_before=before,
            extractor_version=extractor_version,
        )
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    f"""
                    SELECT COUNT(*) AS eligible_messages,
                           COUNT(DISTINCT message.conversation_id) AS conversations
                    FROM messages AS message
                    JOIN conversations AS conversation ON conversation.id = message.conversation_id
                    WHERE {conditions}
                    """,
                    parameters,
                )
            ).fetchone()
        return {
            "scope_kind": scope_kind,
            "conversation_ids": conversation_ids,
            "created_after": created_after,
            "created_before": before,
            "extractor_version": extractor_version,
            "eligible_messages": int(row[0]),
            "conversations": int(row[1]),
            "all_eligible": all_eligible,
        }

    async def start_backfill(
        self, *, preview: dict[str, Any], batch_size: int, max_attempts: int
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        backfill_identifier = memory_id("backfill")
        controller_job_id = memory_id("job")
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO memory_backfills(
                        id, status, scope_kind, conversation_ids_json, created_after,
                        created_before, extractor_version, eligible_messages, batch_size, created_at, updated_at
                    ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        backfill_identifier,
                        preview["scope_kind"],
                        json.dumps(preview["conversation_ids"]),
                        preview["created_after"],
                        preview["created_before"],
                        preview["extractor_version"],
                        preview["eligible_messages"],
                        batch_size,
                        now,
                        now,
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO jobs(
                        id, kind, status, priority, dedupe_key, source_message_id, backfill_id,
                        blocked_by_run_id, attempts, max_attempts, available_at, created_at, updated_at
                    ) VALUES (?, 'memory_backfill', 'pending', -20, ?, NULL, ?, NULL, 0, ?, ?, ?, ?)
                    """,
                    (
                        controller_job_id,
                        f"memory_backfill:{backfill_identifier}:0",
                        backfill_identifier,
                        max_attempts,
                        now,
                        now,
                        now,
                    ),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return await self.backfill_progress(backfill_identifier, batch_size=batch_size)

    async def audit_metrics(self) -> dict[str, Any]:
        """Return aggregate-only quality signals; never expose source text in the audit."""
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            row = await (
                await connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(DISTINCT source_message_id) FROM jobs
                         WHERE kind = 'memory_extract' AND status = 'complete') AS user_messages_processed,
                        (SELECT COUNT(*) FROM memory_candidates) AS candidates_generated,
                        (SELECT COUNT(*) FROM memory_candidates WHERE status = 'invalid') AS candidates_invalid,
                        (SELECT COUNT(*) FROM memory_candidates WHERE status = 'rejected') AS candidates_rejected,
                        (SELECT COUNT(*) FROM memory_candidates WHERE status = 'accepted') AS candidates_accepted,
                        (SELECT COUNT(*) FROM memory_items WHERE status = 'active') AS active_memories,
                        (SELECT COUNT(*) FROM memory_items WHERE status = 'disabled') AS disabled_memories,
                        (SELECT COUNT(*) FROM memory_items WHERE status = 'merged') AS merged_memories,
                        (SELECT COUNT(*) FROM memory_items WHERE status = 'superseded') AS superseded_memories,
                        (SELECT COUNT(*) FROM jobs WHERE kind = 'memory_extract' AND status = 'failed') AS failed_jobs,
                        (SELECT COUNT(*) FROM memory_candidate_sources AS source
                         JOIN messages AS message ON message.id = source.message_id
                         WHERE message.role <> 'user')
                         +
                         (SELECT COUNT(*) FROM memory_sources AS source
                         JOIN messages AS message ON message.id = source.message_id
                         WHERE message.role <> 'user') AS assistant_contamination_count,
                        (SELECT COUNT(*) FROM memory_candidates
                         WHERE status = 'invalid' AND rejection_code IN (
                            'SOURCE_SPAN_MISMATCH', 'SOURCE_SPAN_OUT_OF_RANGE',
                            'SOURCE_NOT_TARGET', 'SOURCE_QUOTE_MISSING'
                         )) AS unsupported_inference_count
                    """
                )
            ).fetchone()
        processed = int(row["user_messages_processed"])
        candidates = int(row["candidates_generated"])
        accepted = int(row["candidates_accepted"])
        return {
            "user_messages_processed": processed,
            "candidates_generated": candidates,
            "candidates_invalid": int(row["candidates_invalid"]),
            "candidates_rejected": int(row["candidates_rejected"]),
            "candidates_accepted": accepted,
            "active_memories": int(row["active_memories"]),
            "disabled_memories": int(row["disabled_memories"]),
            "merged_memories": int(row["merged_memories"]),
            "superseded_memories": int(row["superseded_memories"]),
            "candidate_yield": candidates / processed if processed else 0.0,
            "memory_yield": accepted / processed if processed else 0.0,
            "assistant_contamination_count": int(row["assistant_contamination_count"]),
            "unsupported_inference_count": int(row["unsupported_inference_count"]),
            # F deliberately does no name-based entity merging; this invariant is auditable.
            "wrong_entity_merge_count": 0,
            "failed_jobs": int(row["failed_jobs"]),
        }

    async def run_backfill(
        self, job: JobRecord, cancel_event: asyncio.Event
    ) -> MemoryBackfillBatch:
        if job.kind is not JobKind.MEMORY_BACKFILL or job.backfill_id is None:
            raise MemorySourceIneligibleError("backfill job missing scope")
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            backfill = await (
                await connection.execute(
                    "SELECT * FROM memory_backfills WHERE id = ?", (job.backfill_id,)
                )
            ).fetchone()
            if backfill is None or backfill["status"] != "pending":
                return MemoryBackfillBatch(job.backfill_id, ())
            conversation_ids = json.loads(str(backfill["conversation_ids_json"]))
            conditions, parameters = self._backfill_conditions(
                conversation_ids=conversation_ids,
                created_after=backfill["created_after"],
                created_before=str(backfill["created_before"]),
                extractor_version=str(backfill["extractor_version"]),
            )
            rows = await (
                await connection.execute(
                    f"""
                    SELECT message.id
                    FROM messages AS message
                    JOIN conversations AS conversation ON conversation.id = message.conversation_id
                    WHERE {conditions}
                    ORDER BY message.created_at, message.id
                    LIMIT ?
                    """,
                    [*parameters, int(backfill["batch_size"])],
                )
            ).fetchall()
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        return MemoryBackfillBatch(job.backfill_id, tuple(str(row["id"]) for row in rows))

    async def persist_backfill(
        self,
        connection: aiosqlite.Connection,
        job: JobRecord,
        batch: MemoryBackfillBatch,
        *,
        max_attempts: int,
    ) -> None:
        if job.backfill_id is None or batch.backfill_id != job.backfill_id:
            raise MemorySourceIneligibleError("backfill result does not match job")
        backfill = await (
            await connection.execute(
                "SELECT extractor_version FROM memory_backfills WHERE id = ?", (job.backfill_id,)
            )
        ).fetchone()
        if backfill is None:
            raise MemoryNotFoundError(job.backfill_id)
        extractor_version = str(backfill[0])
        now = datetime.now(UTC).isoformat()
        for message_id in batch.message_ids:
            await connection.execute(
                """
                INSERT INTO jobs(
                    id, kind, status, priority, dedupe_key, source_message_id, backfill_id,
                    blocked_by_run_id, attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, 'memory_extract', 'pending', -10, ?, ?, ?, NULL, 0, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (
                    memory_id("job"),
                    f"memory_extract:{extractor_version}:{message_id}",
                    message_id,
                    job.backfill_id,
                    max_attempts,
                    now,
                    now,
                    now,
                ),
            )
        if batch.message_ids:
            await connection.execute(
                """
                INSERT INTO jobs(
                    id, kind, status, priority, dedupe_key, source_message_id, backfill_id,
                    blocked_by_run_id, attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, 'memory_backfill', 'pending', -20, ?, NULL, ?, NULL, 0, ?, ?, ?, ?)
                """,
                (
                    memory_id("job"),
                    f"memory_backfill:{job.backfill_id}:{uuid4().hex}",
                    job.backfill_id,
                    max_attempts,
                    now,
                    now,
                    now,
                ),
            )
        else:
            await connection.execute(
                "UPDATE memory_backfills SET status = 'complete', updated_at = ? WHERE id = ?",
                (now, job.backfill_id),
            )

    async def backfill_progress(
        self, backfill_identifier: str, *, batch_size: int | None = None
    ) -> dict[str, Any]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            backfill = await (
                await connection.execute(
                    "SELECT * FROM memory_backfills WHERE id = ?", (backfill_identifier,)
                )
            ).fetchone()
            if backfill is None:
                raise MemoryNotFoundError(backfill_identifier)
            counts = await (
                await connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN kind = 'memory_extract' AND status = 'complete' THEN 1 ELSE 0 END) AS processed,
                        SUM(CASE WHEN kind = 'memory_extract' AND status IN ('pending', 'retry', 'running') THEN 1 ELSE 0 END) AS pending,
                        SUM(CASE WHEN kind = 'memory_extract' AND status = 'failed' THEN 1 ELSE 0 END) AS failed,
                        SUM(CASE WHEN kind = 'memory_extract' AND status = 'complete' THEN 1 ELSE 0 END) AS extraction_jobs
                    FROM jobs WHERE backfill_id = ?
                    """,
                    (backfill_identifier,),
                )
            ).fetchone()
            created = await (
                await connection.execute(
                    """
                    SELECT COUNT(*) FROM memory_candidates AS candidate
                    JOIN jobs AS job ON job.id = candidate.extraction_job_id
                    WHERE job.backfill_id = ? AND candidate.status = 'accepted'
                    """,
                    (backfill_identifier,),
                )
            ).fetchone()
        return {
            "id": backfill["id"],
            "status": backfill["status"],
            "scope_kind": backfill["scope_kind"],
            "eligible": backfill["eligible_messages"],
            "processed": int(counts["processed"] or 0),
            "pending": int(counts["pending"] or 0),
            "failed": int(counts["failed"] or 0),
            "memories_created": int(created[0]),
            "created_at": backfill["created_at"],
        }

    async def list_memories(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = ["memory.status IN ('active', 'disabled', 'superseded')"]
        parameters: list[object] = []
        if status is not None:
            conditions = ["memory.status = ?"]
            parameters.append(status)
        if kind is not None:
            conditions.append("memory.kind = ?")
            parameters.append(kind)
        parameters.extend((limit, offset))
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    f"""
                    SELECT memory.*,
                           (SELECT COUNT(*) FROM memory_sources AS source
                            WHERE source.memory_id = memory.id) AS supporting_source_count
                    FROM memory_items AS memory
                    WHERE {" AND ".join(conditions)}
                    ORDER BY memory.last_supported_at DESC, memory.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    parameters,
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def memory_detail(self, memory_identifier: str) -> dict[str, Any]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            memory = await (
                await connection.execute(
                    """
                    SELECT * FROM memory_items
                    WHERE id = ? AND status NOT IN ('deleted', 'merged')
                    """,
                    (memory_identifier,),
                )
            ).fetchone()
            if memory is None:
                raise MemoryNotFoundError(memory_identifier)
            source_rows = await (
                await connection.execute(
                    """
                    SELECT source.*, message.content AS message_content,
                           message.input_type, message.created_at AS message_created_at,
                           message.conversation_id, conversation.title AS conversation_title
                    FROM memory_sources AS source
                    JOIN messages AS message ON message.id = source.message_id
                    JOIN conversations AS conversation ON conversation.id = message.conversation_id
                    WHERE source.memory_id = ?
                    ORDER BY message.created_at, source.start_char
                    """,
                    (memory_identifier,),
                )
            ).fetchall()
            entity_rows = await (
                await connection.execute(
                    """
                    SELECT entity.*, link.role
                    FROM memory_entities AS link
                    JOIN entities AS entity ON entity.id = link.entity_id
                    WHERE link.memory_id = ? ORDER BY entity.display_name, entity.id
                    """,
                    (memory_identifier,),
                )
            ).fetchall()
            revision_rows = await (
                await connection.execute(
                    """
                    SELECT id, revision_no, old_kind, new_kind,
                           old_epistemic_status, new_epistemic_status, actor, created_at
                    FROM memory_revisions WHERE memory_id = ? ORDER BY revision_no DESC
                    """,
                    (memory_identifier,),
                )
            ).fetchall()
        sources: list[dict[str, Any]] = []
        for row in source_rows:
            excerpt = str(row["message_content"])[row["start_char"] : row["end_char"]]
            sources.append(
                {
                    "message_id": row["message_id"],
                    "conversation_id": row["conversation_id"],
                    "conversation_title": row["conversation_title"],
                    "message_created_at": row["message_created_at"],
                    "start_char": row["start_char"],
                    "end_char": row["end_char"],
                    "excerpt": excerpt,
                    "input_type": row["input_type"],
                    "source_role": row["source_role"],
                    "hash_valid": sha256(excerpt.encode("utf-8")).hexdigest() == row["text_sha256"],
                }
            )
        result = dict(memory)
        result["supporting_source_count"] = len(sources)
        result["sources"] = sources
        result["entities"] = [dict(row) for row in entity_rows]
        result["revisions"] = [dict(row) for row in revision_rows]
        return result

    async def edit_memory(
        self,
        memory_identifier: str,
        *,
        content: str | None = None,
        kind: str | None = None,
        epistemic_status: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                current = await (
                    await connection.execute(
                        """
                        SELECT * FROM memory_items
                        WHERE id = ? AND status IN ('active', 'disabled', 'superseded')
                        """,
                        (memory_identifier,),
                    )
                ).fetchone()
                if current is None:
                    raise MemoryNotFoundError(memory_identifier)
                new_content = content if content is not None else current["content"]
                new_kind = kind if kind is not None else current["kind"]
                new_status = (
                    epistemic_status
                    if epistemic_status is not None
                    else current["epistemic_status"]
                )
                revision_row = await (
                    await connection.execute(
                        """
                        SELECT COALESCE(MAX(revision_no), 0) + 1
                        FROM memory_revisions WHERE memory_id = ?
                        """,
                        (memory_identifier,),
                    )
                ).fetchone()
                revision_no = int(revision_row[0])
                await connection.execute(
                    """
                    INSERT INTO memory_revisions(
                        id, memory_id, revision_no, old_content, new_content,
                        old_kind, new_kind, old_epistemic_status,
                        new_epistemic_status, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
                    """,
                    (
                        memory_id("revision"),
                        memory_identifier,
                        revision_no,
                        current["content"],
                        new_content,
                        current["kind"],
                        new_kind,
                        current["epistemic_status"],
                        new_status,
                        now,
                    ),
                )
                await connection.execute(
                    """
                    UPDATE memory_items
                    SET content = ?, kind = ?, epistemic_status = ?,
                        user_locked = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_content, new_kind, new_status, now, memory_identifier),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return await self.memory_detail(memory_identifier)

    async def set_memory_enabled(self, memory_identifier: str, enabled: bool) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        expected = "disabled" if enabled else "active"
        new_status = "active" if enabled else "disabled"
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                UPDATE memory_items SET status = ?, disabled_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    new_status,
                    None if enabled else now,
                    now,
                    memory_identifier,
                    expected,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            existing = await self._memory_status(memory_identifier)
            if existing is None:
                raise MemoryNotFoundError(memory_identifier)
            raise MemoryStateConflictError(existing)
        return await self.memory_detail(memory_identifier)

    async def delete_memory(self, memory_identifier: str, reason: str = "user_request") -> None:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                memory = await (
                    await connection.execute(
                        "SELECT * FROM memory_items WHERE id = ?", (memory_identifier,)
                    )
                ).fetchone()
                if memory is None:
                    raise MemoryNotFoundError(memory_identifier)
                if memory["status"] == "deleted":
                    await connection.commit()
                    return
                related = await (
                    await connection.execute(
                        "SELECT id FROM memory_items WHERE id = ? OR merged_into_memory_id = ?",
                        (memory_identifier, memory_identifier),
                    )
                ).fetchall()
                related_ids = [str(row["id"]) for row in related]
                placeholders = ",".join("?" for _ in related_ids)
                source_rows = await (
                    await connection.execute(
                        f"""
                        SELECT DISTINCT source.message_id, source.start_char, source.end_char
                        FROM memory_sources AS source
                        WHERE source.memory_id IN ({placeholders})
                        """,
                        related_ids,
                    )
                ).fetchall()
                for source in source_rows:
                    await connection.execute(
                        """
                        INSERT OR IGNORE INTO memory_tombstones(
                            id, original_memory_id, source_message_id,
                            start_char, end_char, kind, deleted_at, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            memory_id("tombstone"),
                            memory_identifier,
                            source["message_id"],
                            source["start_char"],
                            source["end_char"],
                            memory["kind"],
                            now,
                            reason,
                        ),
                    )
                await connection.execute(
                    f"DELETE FROM memory_revisions WHERE memory_id IN ({placeholders})",
                    related_ids,
                )
                await connection.execute(
                    f"DELETE FROM memory_entities WHERE memory_id IN ({placeholders})",
                    related_ids,
                )
                await connection.execute(
                    f"DELETE FROM memory_sources WHERE memory_id IN ({placeholders})",
                    related_ids,
                )
                candidate_rows = await (
                    await connection.execute(
                        f"""
                        SELECT created_by_candidate_id FROM memory_items
                        WHERE id IN ({placeholders}) AND created_by_candidate_id IS NOT NULL
                        """,
                        related_ids,
                    )
                ).fetchall()
                candidate_ids = [str(row["created_by_candidate_id"]) for row in candidate_rows]
                if candidate_ids:
                    candidate_placeholders = ",".join("?" for _ in candidate_ids)
                    await connection.execute(
                        f"""
                        DELETE FROM memory_candidate_sources
                        WHERE candidate_id IN ({candidate_placeholders})
                        """,
                        candidate_ids,
                    )
                    await connection.execute(
                        f"""
                        UPDATE memory_candidates
                        SET content = '', status = 'suppressed',
                            rejection_code = 'USER_DELETED_MEMORY',
                            accepted_memory_id = NULL, updated_at = ?
                        WHERE id IN ({candidate_placeholders})
                        """,
                        [now, *candidate_ids],
                    )
                await connection.execute(
                    f"""
                    UPDATE memory_items
                    SET status = 'deleted', content = NULL, deleted_at = ?,
                        disabled_at = NULL, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    [now, now, *related_ids],
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def list_entities(self) -> list[dict[str, Any]]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    """
                    SELECT entity.*,
                           COUNT(DISTINCT link.memory_id) AS linked_memory_count
                    FROM entities AS entity
                    LEFT JOIN memory_entities AS link ON link.entity_id = entity.id
                    GROUP BY entity.id ORDER BY entity.display_name, entity.id
                    """
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def entity_detail(self, entity_identifier: str) -> dict[str, Any]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            entity = await (
                await connection.execute(
                    "SELECT * FROM entities WHERE id = ?", (entity_identifier,)
                )
            ).fetchone()
            if entity is None:
                raise EntityNotFoundError(entity_identifier)
            aliases = await (
                await connection.execute(
                    "SELECT id, alias, normalized_alias FROM entity_aliases WHERE entity_id = ?",
                    (entity_identifier,),
                )
            ).fetchall()
            memories = await (
                await connection.execute(
                    """
                    SELECT memory.id, memory.content, memory.kind, memory.status, link.role
                    FROM memory_entities AS link
                    JOIN memory_items AS memory ON memory.id = link.memory_id
                    WHERE link.entity_id = ? AND memory.status <> 'deleted'
                    ORDER BY memory.updated_at DESC
                    """,
                    (entity_identifier,),
                )
            ).fetchall()
        result = dict(entity)
        result["aliases"] = [dict(row) for row in aliases]
        result["memories"] = [dict(row) for row in memories]
        return result

    async def rename_entity(self, entity_identifier: str, display_name: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "UPDATE entities SET display_name = ?, updated_at = ? WHERE id = ?",
                (display_name, now, entity_identifier),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise EntityNotFoundError(entity_identifier)
        return await self.entity_detail(entity_identifier)

    async def _memory_status(self, memory_identifier: str) -> str | None:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT status FROM memory_items WHERE id = ?", (memory_identifier,)
                )
            ).fetchone()
        return str(row[0]) if row is not None else None

    async def extraction_context(
        self, target_message_id: str, previous_user_count: int
    ) -> tuple[MemoryUserSource, ...]:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            target = await (
                await connection.execute(
                    """
                    SELECT * FROM messages
                    WHERE id = ? AND role = 'user' AND status = 'complete'
                      AND excluded_from_ai = 0
                    """,
                    (target_message_id,),
                )
            ).fetchone()
            if target is None:
                raise MemorySourceIneligibleError(target_message_id)
            rows = await (
                await connection.execute(
                    """
                    SELECT id, content, created_at, input_type FROM messages
                    WHERE conversation_id = ? AND sequence_no <= ?
                      AND role = 'user' AND status = 'complete' AND excluded_from_ai = 0
                    ORDER BY sequence_no DESC
                    LIMIT ?
                    """,
                    (
                        target["conversation_id"],
                        target["sequence_no"],
                        previous_user_count + 1,
                    ),
                )
            ).fetchall()
        rows.reverse()
        return tuple(
            MemoryUserSource(
                id=str(row["id"]),
                content=str(row["content"]),
                created_at=str(row["created_at"]),
                input_type=str(row["input_type"]),
                is_target=row["id"] == target_message_id,
            )
            for row in rows
        )

    async def persist_extraction(
        self,
        connection: aiosqlite.Connection,
        job: JobRecord,
        batch: MemoryExtractionBatch,
    ) -> None:
        sources = {source.id: source for source in batch.sources}
        target = sources.get(batch.target_message_id)
        if target is None or not target.is_target:
            raise MemorySourceIneligibleError(batch.target_message_id)
        for draft in batch.extraction.candidates:
            await self._persist_candidate(connection, job, batch.run_id, target, sources, draft)

    async def _persist_candidate(
        self,
        connection: aiosqlite.Connection,
        job: JobRecord,
        run_id: str,
        target: MemoryUserSource,
        sources: dict[str, MemoryUserSource],
        draft: MemoryCandidateDraft,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        candidate_id = memory_id("candidate")
        single = MemoryExtractionResult(candidates=[draft])
        rejection_code: str | None = None
        grounded_draft: MemoryCandidateDraft | None = None
        try:
            grounded = canonicalize_source_spans(
                single,
                {identifier: source.content for identifier, source in sources.items()},
                target.id,
            )
            grounded_draft = grounded.extraction.candidates[0]
        except GroundingError as error:
            rejection_code = error.code

        memory_identifier: str | None = None
        status = "invalid" if rejection_code else "accepted"
        if grounded_draft is not None and await self._is_tombstoned(connection, grounded_draft):
            status = "suppressed"
            rejection_code = "SAME_SOURCE_TOMBSTONE"
        if grounded_draft is not None and await self._is_disabled_source(
            connection, grounded_draft
        ):
            status = "suppressed"
            rejection_code = "SAME_SOURCE_DISABLED"
        if status == "accepted":
            memory_identifier = memory_id("memory")

        time = draft.time
        await connection.execute(
            """
            INSERT INTO memory_candidates(
                id, extraction_job_id, extraction_run_id, kind, content,
                epistemic_status, status, rejection_code, accepted_memory_id,
                observed_at, event_start_at, event_end_at, time_precision,
                time_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                job.id,
                run_id,
                draft.kind.value,
                draft.content,
                draft.epistemic_status.value,
                status,
                rejection_code,
                None,
                target.created_at,
                time.start_at,
                time.end_at,
                time.precision,
                time.text,
                now,
                now,
            ),
        )
        if grounded_draft is None:
            return
        for span in grounded_draft.source_spans:
            await connection.execute(
                """
                INSERT INTO memory_candidate_sources(
                    candidate_id, message_id, start_char, end_char, text_sha256, source_role
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    span.message_id,
                    span.start_char,
                    span.end_char,
                    sha256(span.quote.encode("utf-8")).hexdigest(),
                    "target" if span.message_id == target.id else "context",
                ),
            )
        if status != "accepted" or memory_identifier is None:
            return
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                event_start_at, event_end_at, time_precision, time_text,
                last_supported_at, created_by_candidate_id, created_at, updated_at
            ) VALUES (?, ?, 'provisional', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_identifier,
                grounded_draft.kind.value,
                grounded_draft.content,
                grounded_draft.epistemic_status.value,
                target.created_at,
                grounded_draft.time.start_at,
                grounded_draft.time.end_at,
                grounded_draft.time.precision,
                grounded_draft.time.text,
                target.created_at,
                candidate_id,
                now,
                now,
            ),
        )
        await connection.execute(
            "UPDATE memory_candidates SET accepted_memory_id = ? WHERE id = ?",
            (memory_identifier, candidate_id),
        )
        for span in grounded_draft.source_spans:
            await connection.execute(
                """
                INSERT INTO memory_sources(
                    memory_id, message_id, start_char, end_char,
                    text_sha256, source_role, created_at
                ) VALUES (?, ?, ?, ?, ?, 'origin', ?)
                """,
                (
                    memory_identifier,
                    span.message_id,
                    span.start_char,
                    span.end_char,
                    sha256(span.quote.encode("utf-8")).hexdigest(),
                    now,
                ),
            )
        active_memory_id = await self._consolidate_level0(
            connection, candidate_id, memory_identifier
        )
        await self._persist_entities(connection, active_memory_id, grounded_draft)
        if active_memory_id == memory_identifier:
            await self._supersede_explicit_location_change(
                connection, memory_identifier, target, grounded_draft
            )

    @staticmethod
    async def _supersede_explicit_location_change(
        connection: aiosqlite.Connection,
        new_memory_id: str,
        target: MemoryUserSource,
        draft: MemoryCandidateDraft,
    ) -> None:
        """Handle one deliberately narrow, explicit current-residence update.

        Anything uncertain, indirect, locked, or with more than one possible prior location
        remains separate. This is a Level 1 deterministic action, not an inferred biography.
        """
        if draft.kind.value != "personal_fact":
            return
        normalized_target = " ".join(target.content.casefold().split())
        uncertain_markers = ("peut-être", "peut etre", "envisage", "voudrais", "si je")
        if any(marker in normalized_target for marker in uncertain_markers):
            return
        move = re.search(
            r"\b(?:j['’]ai déménagé|je (?:vis|réside|habite) désormais|je vis maintenant)\s+à\s+([^.!?]+)",
            normalized_target,
        )
        if move is None or move.group(1).strip() not in draft.content.casefold():
            return
        rows = await (
            await connection.execute(
                """
                SELECT DISTINCT memory.id
                FROM memory_items AS memory
                JOIN memory_sources AS source ON source.memory_id = memory.id
                JOIN messages AS message ON message.id = source.message_id
                WHERE memory.id <> ? AND memory.status = 'active' AND memory.user_locked = 0
                  AND memory.kind = 'personal_fact'
                  AND (
                    lower(message.content) LIKE '%j''habite à%'
                    OR lower(message.content) LIKE '%je vis à%'
                    OR lower(message.content) LIKE '%je réside à%'
                  )
                """,
                (new_memory_id,),
            )
        ).fetchall()
        if len(rows) != 1:
            return
        now = datetime.now(UTC).isoformat()
        old_memory_id = str(rows[0][0])
        await connection.execute(
            """
            UPDATE memory_items
            SET status = 'superseded', superseded_by_memory_id = ?, valid_until = ?, updated_at = ?
            WHERE id = ? AND status = 'active' AND user_locked = 0
            """,
            (new_memory_id, target.created_at, now, old_memory_id),
        )
        await connection.execute(
            """
            UPDATE memory_sources SET source_role = 'update'
            WHERE memory_id = ? AND message_id = ? AND source_role = 'origin'
            """,
            (new_memory_id, target.id),
        )

    @staticmethod
    async def _is_tombstoned(connection: aiosqlite.Connection, draft: MemoryCandidateDraft) -> bool:
        for span in draft.source_spans:
            row = await (
                await connection.execute(
                    """
                    SELECT 1 FROM memory_tombstones
                    WHERE source_message_id = ? AND start_char = ? AND end_char = ? AND kind = ?
                    LIMIT 1
                    """,
                    (span.message_id, span.start_char, span.end_char, draft.kind.value),
                )
            ).fetchone()
            if row is not None:
                return True
        return False

    @staticmethod
    async def _is_disabled_source(
        connection: aiosqlite.Connection, draft: MemoryCandidateDraft
    ) -> bool:
        for span in draft.source_spans:
            row = await (
                await connection.execute(
                    """
                    SELECT 1
                    FROM memory_sources AS source
                    JOIN memory_items AS memory ON memory.id = source.memory_id
                    WHERE source.message_id = ? AND source.start_char = ? AND source.end_char = ?
                      AND memory.kind = ? AND memory.status = 'disabled'
                    LIMIT 1
                    """,
                    (span.message_id, span.start_char, span.end_char, draft.kind.value),
                )
            ).fetchone()
            if row is not None:
                return True
        return False

    @staticmethod
    async def _consolidate_level0(
        connection: aiosqlite.Connection, candidate_id: str, memory_identifier: str
    ) -> str:
        connection.row_factory = aiosqlite.Row
        current = await (
            await connection.execute(
                "SELECT * FROM memory_items WHERE id = ? AND status = 'provisional'",
                (memory_identifier,),
            )
        ).fetchone()
        if current is None:
            return memory_identifier
        rows = await (
            await connection.execute(
                """
                SELECT * FROM memory_items
                WHERE id <> ? AND kind = ? AND status IN ('active', 'provisional')
                ORDER BY created_at ASC, id ASC
                """,
                (memory_identifier, current["kind"]),
            )
        ).fetchall()
        normalized = normalize_level0(str(current["content"]))
        target = next(
            (row for row in rows if normalize_level0(str(row["content"])) == normalized),
            None,
        )
        now = datetime.now(UTC).isoformat()
        if target is None:
            await connection.execute(
                "UPDATE memory_items SET status = 'active', updated_at = ? WHERE id = ?",
                (now, memory_identifier),
            )
            return memory_identifier
        source_rows = await (
            await connection.execute(
                "SELECT * FROM memory_sources WHERE memory_id = ?", (memory_identifier,)
            )
        ).fetchall()
        for source in source_rows:
            await connection.execute(
                """
                INSERT OR IGNORE INTO memory_sources(
                    memory_id, message_id, start_char, end_char,
                    text_sha256, source_role, created_at
                ) VALUES (?, ?, ?, ?, ?, 'reinforcement', ?)
                """,
                (
                    target["id"],
                    source["message_id"],
                    source["start_char"],
                    source["end_char"],
                    source["text_sha256"],
                    now,
                ),
            )
        await connection.execute(
            """
            UPDATE memory_items
            SET status = 'merged', merged_into_memory_id = ?, updated_at = ?
            WHERE id = ? AND status = 'provisional'
            """,
            (target["id"], now, memory_identifier),
        )
        await connection.execute(
            """
            UPDATE memory_items
            SET last_supported_at = MAX(last_supported_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (current["last_supported_at"], now, target["id"]),
        )
        await connection.execute(
            "UPDATE memory_candidates SET accepted_memory_id = ? WHERE id = ?",
            (target["id"], candidate_id),
        )
        return str(target["id"])

    @staticmethod
    async def _persist_entities(
        connection: aiosqlite.Connection,
        memory_identifier: str,
        draft: MemoryCandidateDraft,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        seen: set[tuple[str, str]] = set()
        source_text = "\n".join(span.quote for span in draft.source_spans).casefold()
        for entity in draft.entities:
            normalized = normalize_alias(entity.mention)
            key = (normalized, entity.entity_type)
            if not normalized or key in seen or entity.mention.casefold() not in source_text:
                continue
            seen.add(key)
            identifier = memory_id("entity")
            await connection.execute(
                """
                INSERT INTO entities(
                    id, display_name, entity_type, resolution_status, created_at, updated_at
                ) VALUES (?, ?, ?, 'unresolved', ?, ?)
                """,
                (identifier, entity.mention, entity.entity_type, now, now),
            )
            await connection.execute(
                """
                INSERT INTO entity_aliases(id, entity_id, alias, normalized_alias, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (memory_id("alias"), identifier, entity.mention, normalized, now),
            )
            role = {
                "place": "location",
                "organization": "organization",
            }.get(entity.entity_type, "related")
            await connection.execute(
                """
                INSERT INTO memory_entities(memory_id, entity_id, role)
                VALUES (?, ?, ?)
                """,
                (memory_identifier, identifier, role),
            )
