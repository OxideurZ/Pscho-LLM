from __future__ import annotations

import math
import re
import struct
from collections.abc import Sequence
from datetime import UTC, datetime

import aiosqlite

from backend.app.db import Database
from backend.app.retrieval.interfaces import VectorStore
from backend.app.retrieval.models import (
    ComponentHealth,
    ComponentStatus,
    RetrievalDocument,
    RetrievalSourceType,
    ScoredSource,
    VectorQuery,
    VectorRecord,
)


def _encode_vector(vector: Sequence[float], dimensions: int) -> bytes:
    if len(vector) != dimensions:
        raise ValueError("Vector dimensions do not match its profile")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Vector contains a non-finite value")
    return struct.pack(f"<{dimensions}f", *vector)


def _decode_vector(blob: bytes, dimensions: int) -> tuple[float, ...]:
    if len(blob) != dimensions * 4:
        raise ValueError("Stored vector length does not match its profile")
    return struct.unpack(f"<{dimensions}f", blob)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class SqlCipherVectorStore(VectorStore):
    """Reference G vector backend: bounded linear scan inside SQLCipher."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.executemany(
                """
                INSERT INTO retrieval_embeddings(
                    source_type, source_id, content_sha256, embedding_model,
                    embedding_revision, instruction_version, configuration_sha256,
                    dimensions, dtype, vector_blob, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    content_sha256 = excluded.content_sha256,
                    embedding_model = excluded.embedding_model,
                    embedding_revision = excluded.embedding_revision,
                    instruction_version = excluded.instruction_version,
                    configuration_sha256 = excluded.configuration_sha256,
                    dimensions = excluded.dimensions,
                    dtype = excluded.dtype,
                    vector_blob = excluded.vector_blob,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        record.source_type.value,
                        record.source_id,
                        record.content_sha256,
                        record.embedding_model,
                        record.embedding_revision,
                        record.instruction_version,
                        record.configuration_sha256,
                        record.dimensions,
                        record.dtype,
                        _encode_vector(record.vector, record.dimensions),
                        now,
                        now,
                    )
                    for record in records
                ],
            )
            await connection.commit()

    async def delete(self, source_type: str, source_id: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                "DELETE FROM retrieval_embeddings WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            )
            await connection.commit()

    async def query(self, query: VectorQuery) -> list[ScoredSource]:
        query_vector = _decode_vector(
            _encode_vector(query.vector, query.dimensions), query.dimensions
        )
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (
                await connection.execute(
                    """
                    SELECT source_type, source_id, dimensions, vector_blob
                    FROM retrieval_embeddings
                    WHERE embedding_model = ?
                      AND embedding_revision = ?
                      AND instruction_version = ?
                      AND configuration_sha256 = ?
                      AND dimensions = ?
                      AND dtype = ?
                      AND (? IS NULL OR source_type = ?)
                    """,
                    (
                        query.embedding_model,
                        query.embedding_revision,
                        query.instruction_version,
                        query.configuration_sha256,
                        query.dimensions,
                        query.dtype,
                        query.source_type.value if query.source_type else None,
                        query.source_type.value if query.source_type else None,
                    ),
                )
            ).fetchall()
        ranked = sorted(
            (
                (
                    _cosine_similarity(
                        query_vector, _decode_vector(row["vector_blob"], row["dimensions"])
                    ),
                    str(row["source_type"]),
                    str(row["source_id"]),
                )
                for row in rows
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )[: query.top_k]
        return [
            ScoredSource(
                source_type=RetrievalSourceType(source_type),
                source_id=source_id,
                score=score,
                rank=index,
            )
            for index, (score, source_type, source_id) in enumerate(ranked, start=1)
        ]

    async def rebuild(self, records: Sequence[VectorRecord]) -> None:
        now = datetime.now(UTC).isoformat()
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute("DELETE FROM retrieval_embeddings")
                if records:
                    await connection.executemany(
                        """
                        INSERT INTO retrieval_embeddings(
                            source_type, source_id, content_sha256, embedding_model,
                            embedding_revision, instruction_version, configuration_sha256,
                            dimensions, dtype, vector_blob, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                record.source_type.value,
                                record.source_id,
                                record.content_sha256,
                                record.embedding_model,
                                record.embedding_revision,
                                record.instruction_version,
                                record.configuration_sha256,
                                record.dimensions,
                                record.dtype,
                                _encode_vector(record.vector, record.dimensions),
                                now,
                                now,
                            )
                            for record in records
                        ],
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def health(self) -> ComponentHealth:
        try:
            async with self.database.connect() as connection:
                await (
                    await connection.execute("SELECT COUNT(*) FROM retrieval_embeddings")
                ).fetchone()
            return ComponentHealth(status=ComponentStatus.HEALTHY)
        except aiosqlite.Error:
            return ComponentHealth(
                status=ComponentStatus.UNAVAILABLE, detail_code="VECTOR_STORE_UNAVAILABLE"
            )


class SqlCipherLexicalIndex:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert(self, documents: Sequence[RetrievalDocument]) -> None:
        if not documents:
            return
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for document in documents:
                    await connection.execute(
                        "DELETE FROM retrieval_fts WHERE source_type = ? AND source_id = ?",
                        (document.source_type.value, document.source_id),
                    )
                    await connection.execute(
                        "INSERT INTO retrieval_fts("
                        "source_type, source_id, content, content_sha256, updated_at"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (
                            document.source_type.value,
                            document.source_id,
                            document.content,
                            document.content_sha256,
                            document.updated_at,
                        ),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def delete(self, source_type: str, source_id: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                "DELETE FROM retrieval_fts WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            )
            await connection.commit()

    async def query(
        self, text: str, source_type: RetrievalSourceType, *, top_k: int
    ) -> list[ScoredSource]:
        tokens = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text.casefold(), flags=re.UNICODE)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT source_id, bm25(retrieval_fts) AS score
                    FROM retrieval_fts
                    WHERE retrieval_fts MATCH ? AND source_type = ?
                    ORDER BY score ASC, source_id ASC
                    LIMIT ?
                    """,
                    (expression, source_type.value, top_k),
                )
            ).fetchall()
        return [
            ScoredSource(
                source_type=source_type,
                source_id=str(row[0]),
                score=-float(row[1]),
                rank=rank,
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def rebuild(self, documents: Sequence[RetrievalDocument]) -> None:
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute("DELETE FROM retrieval_fts")
                if documents:
                    await connection.executemany(
                        """
                        INSERT INTO retrieval_fts(
                            source_type, source_id, content, content_sha256, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                document.source_type.value,
                                document.source_id,
                                document.content,
                                document.content_sha256,
                                document.updated_at,
                            )
                            for document in documents
                        ],
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def health(self) -> ComponentHealth:
        try:
            async with self.database.connect() as connection:
                await (await connection.execute("SELECT COUNT(*) FROM retrieval_fts")).fetchone()
            return ComponentHealth(status=ComponentStatus.HEALTHY)
        except aiosqlite.Error:
            return ComponentHealth(
                status=ComponentStatus.UNAVAILABLE, detail_code="LEXICAL_INDEX_UNAVAILABLE"
            )
