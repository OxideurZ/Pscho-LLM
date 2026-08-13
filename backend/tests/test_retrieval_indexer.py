from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import aiosqlite
import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.jobs import JobKind, JobRepository
from backend.app.retrieval import (
    ComponentHealth,
    ComponentStatus,
    Embedder,
    RetrievalIndexer,
    RetrievalModelInfo,
    RetrievalSourceRepository,
    SqlCipherLexicalIndex,
    SqlCipherVectorStore,
    VectorQuery,
)


class FakeEmbedder(Embedder):
    def __init__(self, dimensions: int = 32) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def health(self) -> ComponentHealth:
        return ComponentHealth(status=ComponentStatus.HEALTHY)

    def model_info(self) -> RetrievalModelInfo:
        return RetrievalModelInfo(
            name="fake-embedder",
            revision="revision-1",
            model_sha256="a" * 64,
            runtime="test",
            device="cpu",
            dtype="float32",
            dimensions=self.dimensions,
        )

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        vector[int(sha256(text.encode()).hexdigest()[:4], 16) % self.dimensions] = 1.0
        return vector


async def migrated_database(path: Path) -> Database:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    return database


def make_indexer(database: Database, embedder: FakeEmbedder) -> RetrievalIndexer:
    return RetrievalIndexer(
        RetrievalSourceRepository(database),
        SqlCipherLexicalIndex(database),
        SqlCipherVectorStore(database),
        embedder,
        instruction_version="retrieval_query_instruction:v0.1.0",
        dimensions=embedder.dimensions,
        dtype="float32",
    )


async def insert_conversation(connection: aiosqlite.Connection, conversation_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    await connection.execute(
        """
        INSERT INTO conversations(id, next_sequence_no, created_at, updated_at)
        VALUES (?, 1, ?, ?)
        """,
        (conversation_id, now, now),
    )


async def insert_message(
    connection: aiosqlite.Connection,
    *,
    message_id: str,
    conversation_id: str,
    role: str,
    content: str,
    status: str = "complete",
    excluded: int = 0,
    sequence_no: int = 1,
) -> None:
    now = datetime.now(UTC).isoformat()
    await connection.execute(
        """
        INSERT INTO messages(
            id, conversation_id, sequence_no, role, content, input_type,
            status, created_at, updated_at, excluded_from_ai
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            conversation_id,
            sequence_no,
            role,
            content,
            "text" if role == "user" else "generated",
            status,
            now,
            now,
            excluded,
        ),
    )


@pytest.mark.asyncio
async def test_message_trigger_enqueues_only_complete_included_user_history(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    async with database.connect() as connection:
        await insert_conversation(connection, "conversation")
        await insert_message(
            connection,
            message_id="user-ok",
            conversation_id="conversation",
            role="user",
            content="Détail utile",
        )
        await insert_message(
            connection,
            message_id="assistant-no",
            conversation_id="conversation",
            role="assistant",
            content="Sortie assistant",
            sequence_no=2,
        )
        await connection.commit()
        rows = await (
            await connection.execute(
                """
                SELECT source_type, source_id FROM jobs
                WHERE kind = 'retrieval_index_message' ORDER BY source_id
                """
            )
        ).fetchall()

    assert rows == [("raw_user", "user-ok")]


@pytest.mark.asyncio
async def test_conversation_soft_delete_enqueues_raw_index_invalidation(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    async with database.connect() as connection:
        await insert_conversation(connection, "conversation")
        await insert_message(
            connection,
            message_id="user-history",
            conversation_id="conversation",
            role="user",
            content="Détail historique",
        )
        await connection.execute(
            "UPDATE conversations SET deleted_at = ?, updated_at = ? WHERE id = ?",
            ("2026-08-13T01:00:00+00:00", "2026-08-13T01:00:00+00:00", "conversation"),
        )
        await connection.commit()
        count = await (
            await connection.execute(
                """
                SELECT COUNT(*) FROM jobs
                WHERE kind = 'retrieval_index_message' AND source_id = 'user-history'
                """
            )
        ).fetchone()

    assert count == (2,)
    assert await RetrievalSourceRepository(database).get("raw_user", "user-history") is None


@pytest.mark.asyncio
async def test_memory_trigger_tracks_activation_edit_disable_and_delete(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    now = datetime.now(UTC).isoformat()
    async with database.connect() as connection:
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                last_supported_at, created_at, updated_at
            ) VALUES ('memory-one', 'preference', 'active', 'J’aime le thé',
                      'stated', ?, ?, ?, ?)
            """,
            (now, now, now, now),
        )
        for index, (status, content) in enumerate(
            (("active", "J’aime le thé vert"), ("disabled", "J’aime le thé vert")), start=1
        ):
            await connection.execute(
                "UPDATE memory_items SET status = ?, content = ?, updated_at = ? WHERE id = ?",
                (status, content, f"{now}:{index}", "memory-one"),
            )
        await connection.commit()
        count = await (
            await connection.execute(
                """
                SELECT COUNT(*) FROM jobs
                WHERE kind = 'retrieval_index_memory' AND source_id = 'memory-one'
                """
            )
        ).fetchone()

    assert count == (3,)
    assert await RetrievalSourceRepository(database).get("memory", "memory-one") is None


@pytest.mark.asyncio
async def test_index_job_revalidates_source_and_removes_stale_disabled_memory(
    tmp_path: Path,
) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    embedder = FakeEmbedder()
    indexer = make_indexer(database, embedder)
    jobs = JobRepository(database)
    now = datetime.now(UTC).isoformat()
    async with database.connect() as connection:
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                last_supported_at, created_at, updated_at
            ) VALUES ('memory-one', 'preference', 'active', 'J’aime le thé',
                      'stated', ?, ?, ?, ?)
            """,
            (now, now, now, now),
        )
        await connection.commit()
    job = await jobs.enqueue(
        kind=JobKind.RETRIEVAL_INDEX_MEMORY,
        dedupe_key="manual-index",
        source_type="memory",
        source_id="memory-one",
    )
    await indexer.run(job, asyncio.Event())

    async with database.connect() as connection:
        await connection.execute(
            "UPDATE memory_items SET status = 'disabled', updated_at = ? WHERE id = 'memory-one'",
            (f"{now}:disabled",),
        )
        await connection.commit()
    stale_job = await jobs.enqueue(
        kind=JobKind.RETRIEVAL_INDEX_MEMORY,
        dedupe_key="manual-delete",
        source_type="memory",
        source_id="memory-one",
    )
    await indexer.run(stale_job, asyncio.Event())

    async with database.connect() as connection:
        lexical = await (await connection.execute("SELECT COUNT(*) FROM retrieval_fts")).fetchone()
        vectors = await (
            await connection.execute("SELECT COUNT(*) FROM retrieval_embeddings")
        ).fetchone()
    assert lexical == (0,)
    assert vectors == (0,)


@pytest.mark.asyncio
async def test_rebuild_uses_only_source_truth_eligible_documents(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    embedder = FakeEmbedder()
    indexer = make_indexer(database, embedder)
    jobs = JobRepository(database)
    now = datetime.now(UTC).isoformat()
    async with database.connect() as connection:
        await insert_conversation(connection, "conversation")
        await insert_message(
            connection,
            message_id="raw-user",
            conversation_id="conversation",
            role="user",
            content="La terrasse est derrière l'église",
        )
        await insert_message(
            connection,
            message_id="assistant-excluded",
            conversation_id="conversation",
            role="assistant",
            content="Ne jamais indexer ceci",
            sequence_no=2,
        )
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                last_supported_at, created_at, updated_at
            ) VALUES ('memory-active', 'preference', 'active', 'Je préfère le thé',
                      'stated', ?, ?, ?, ?)
            """,
            (now, now, now, now),
        )
        await connection.commit()
    job = await jobs.enqueue(kind=JobKind.RETRIEVAL_REINDEX, dedupe_key="manual-rebuild")
    await indexer.run(job, asyncio.Event())

    async with database.connect() as connection:
        sources = await (
            await connection.execute(
                "SELECT source_type, source_id FROM retrieval_embeddings ORDER BY source_type"
            )
        ).fetchall()
    assert sources == [("memory", "memory-active"), ("raw_user", "raw-user")]
    assert embedder.calls == [["Je préfère le thé", "La terrasse est derrière l'église"]]


@pytest.mark.asyncio
async def test_indexer_rejects_late_result_after_cancel(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    embedder = FakeEmbedder()
    indexer = make_indexer(database, embedder)
    job = await JobRepository(database).enqueue(
        kind=JobKind.RETRIEVAL_REINDEX, dedupe_key="cancelled-rebuild"
    )
    cancelled = asyncio.Event()
    cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        await indexer.run(job, cancelled)

    query = VectorQuery(
        embedding_model="fake-embedder",
        embedding_revision="revision-1",
        instruction_version="retrieval_query_instruction:v0.1.0",
        configuration_sha256=indexer.configuration_sha256,
        dimensions=32,
        vector=[1.0, *([0.0] * 31)],
        top_k=5,
    )
    assert await SqlCipherVectorStore(database).query(query) == []
