from __future__ import annotations

from hashlib import sha256

import aiosqlite

from backend.app.db import Database
from backend.app.retrieval.models import RetrievalDocument, RetrievalSourceType


class RetrievalSourceRepository:
    """Read eligible retrieval documents from the SQLCipher source of truth."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(
        self, source_type: RetrievalSourceType | str, source_id: str
    ) -> RetrievalDocument | None:
        resolved_type = RetrievalSourceType(source_type)
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            row = await (
                await connection.execute(
                    self._eligible_query(resolved_type, "AND source.id = ?"), (source_id,)
                )
            ).fetchone()
        return self._document(resolved_type, row) if row is not None else None

    async def all_eligible(self) -> list[RetrievalDocument]:
        documents: list[RetrievalDocument] = []
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            for source_type in RetrievalSourceType:
                rows = await (
                    await connection.execute(self._eligible_query(source_type))
                ).fetchall()
                documents.extend(self._document(source_type, row) for row in rows)
        return sorted(documents, key=lambda item: (item.source_type.value, item.source_id))

    @staticmethod
    def _eligible_query(source_type: RetrievalSourceType, suffix: str = "") -> str:
        if source_type is RetrievalSourceType.MEMORY:
            return f"""
                SELECT source.id, source.content, source.updated_at,
                       source.status, source.kind, source.epistemic_status,
                       source.observed_at, source.event_start_at, source.event_end_at,
                       source.valid_from, source.valid_until, source.time_precision,
                       source.time_text, NULL AS conversation_id,
                       NULL AS conversation_title, source.created_at, NULL AS input_type
                FROM memory_items AS source
                WHERE source.status IN ('active', 'superseded')
                  AND source.content IS NOT NULL
                  {suffix}
                ORDER BY source.id
            """
        return f"""
            SELECT source.id, source.content, source.updated_at,
                   NULL AS status, NULL AS kind, NULL AS epistemic_status,
                   NULL AS observed_at, NULL AS event_start_at, NULL AS event_end_at,
                   NULL AS valid_from, NULL AS valid_until, NULL AS time_precision,
                   NULL AS time_text, source.conversation_id,
                   conversation.title AS conversation_title, source.created_at, source.input_type
            FROM messages AS source
            JOIN conversations AS conversation ON conversation.id = source.conversation_id
            WHERE source.role = 'user'
              AND source.status = 'complete'
              AND source.excluded_from_ai = 0
              AND conversation.deleted_at IS NULL
              {suffix}
            ORDER BY source.id
        """

    @staticmethod
    def _document(source_type: RetrievalSourceType, row: aiosqlite.Row) -> RetrievalDocument:
        content = str(row["content"])
        return RetrievalDocument(
            source_type=source_type,
            source_id=str(row["id"]),
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
            updated_at=str(row["updated_at"]),
            status=row["status"],
            kind=row["kind"],
            epistemic_status=row["epistemic_status"],
            observed_at=row["observed_at"],
            event_start_at=row["event_start_at"],
            event_end_at=row["event_end_at"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            time_precision=row["time_precision"],
            time_text=row["time_text"],
            conversation_id=row["conversation_id"],
            conversation_title=row["conversation_title"],
            created_at=row["created_at"],
            input_type=row["input_type"],
        )
