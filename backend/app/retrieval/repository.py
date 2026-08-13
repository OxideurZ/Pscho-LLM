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
                SELECT source.id, source.content, source.updated_at
                FROM memory_items AS source
                WHERE source.status IN ('active', 'superseded')
                  AND source.content IS NOT NULL
                  {suffix}
                ORDER BY source.id
            """
        return f"""
            SELECT source.id, source.content, source.updated_at
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
        )
