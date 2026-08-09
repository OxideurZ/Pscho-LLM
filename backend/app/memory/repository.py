from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import aiosqlite

from backend.app.db import Database
from backend.app.jobs import JobRecord
from backend.app.memory.grounding import GroundingError, canonicalize_source_spans
from backend.app.memory.models import MemoryCandidateDraft, MemoryExtractionResult


def memory_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


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


class MemorySourceIneligibleError(RuntimeError):
    code = "MEMORY_SOURCE_INELIGIBLE"


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

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
