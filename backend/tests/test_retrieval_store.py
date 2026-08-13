import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.jobs import JobKind, JobRepository
from backend.app.retrieval import (
    ComponentStatus,
    RetrievalDocument,
    RetrievalSourceType,
    SqlCipherLexicalIndex,
    SqlCipherVectorStore,
    VectorQuery,
    VectorRecord,
)

PROFILE_SHA = "c" * 64


async def migrated_database(path: Path) -> Database:
    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    return database


def vector_record(
    source_id: str,
    vector: list[float],
    *,
    source_type: RetrievalSourceType = RetrievalSourceType.MEMORY,
    content: str = "document",
) -> VectorRecord:
    return VectorRecord(
        source_type=source_type,
        source_id=source_id,
        content_sha256=sha256(content.encode()).hexdigest(),
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_revision="revision",
        instruction_version="retrieval_query_instruction:v0.1.0",
        configuration_sha256=PROFILE_SHA,
        dimensions=32,
        vector=vector,
    )


def vector_query(vector: list[float], *, configuration_sha256: str = PROFILE_SHA) -> VectorQuery:
    return VectorQuery(
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_revision="revision",
        instruction_version="retrieval_query_instruction:v0.1.0",
        configuration_sha256=configuration_sha256,
        dimensions=32,
        vector=vector,
        top_k=5,
    )


@pytest.mark.asyncio
async def test_migration_13_creates_fts_vector_profile_and_retrieval_jobs(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    jobs = JobRepository(database)
    job = await jobs.enqueue(
        kind=JobKind.RETRIEVAL_INDEX_MEMORY,
        dedupe_key="retrieval:memory:one:hash",
        source_type="memory",
        source_id="memory-one",
    )

    async with database.connect() as connection:
        version = await (
            await connection.execute("SELECT MAX(version) FROM schema_migrations")
        ).fetchone()
        tables = {
            row[0]
            for row in await (
                await connection.execute(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'retrieval_%'"
                )
            ).fetchall()
        }
        violations = await (await connection.execute("PRAGMA foreign_key_check")).fetchall()

    assert version == (15,)
    assert {"retrieval_fts", "retrieval_embeddings", "retrieval_profiles"} <= tables
    assert job.kind is JobKind.RETRIEVAL_INDEX_MEMORY
    assert job.source_type == "memory"
    assert job.source_id == "memory-one"
    assert violations == []


@pytest.mark.asyncio
async def test_migration_13_preserves_existing_jobs_and_referencing_candidates(
    tmp_path: Path,
) -> None:
    partial_migrations = tmp_path / "migrations-12"
    partial_migrations.mkdir()
    for migration in sorted((REPOSITORY_ROOT / "migrations").glob("*.sql"))[:12]:
        shutil.copy2(migration, partial_migrations / migration.name)
    database = Database(tmp_path / "upgrade.sqlite", partial_migrations)
    await database.migrate()
    now = "2026-08-13T00:00:00+00:00"
    async with database.connect() as connection:
        await connection.execute(
            """
            INSERT INTO jobs(
                id, kind, status, priority, dedupe_key, attempts, max_attempts,
                available_at, created_at, updated_at
            ) VALUES ('job-old', 'memory_extract', 'complete', 0, 'old', 1, 3, ?, ?, ?)
            """,
            (now, now, now),
        )
        await connection.execute(
            """
            INSERT INTO memory_candidates(
                id, extraction_job_id, kind, content, epistemic_status, status,
                observed_at, created_at, updated_at
            ) VALUES (
                'candidate-old', 'job-old', 'preference', 'Contenu existant',
                'stated', 'accepted', ?, ?, ?
            )
            """,
            (now, now, now),
        )
        await connection.commit()

    database.migrations_dir = REPOSITORY_ROOT / "migrations"
    await database.migrate()

    async with database.connect() as connection:
        job = await (
            await connection.execute("SELECT kind FROM jobs WHERE id = 'job-old'")
        ).fetchone()
        candidate = await (
            await connection.execute(
                "SELECT extraction_job_id FROM memory_candidates WHERE id = 'candidate-old'"
            )
        ).fetchone()
        violations = await (await connection.execute("PRAGMA foreign_key_check")).fetchall()
    assert job == ("memory_extract",)
    assert candidate == ("job-old",)
    assert violations == []


@pytest.mark.asyncio
async def test_sqlcipher_vector_store_upsert_filters_profile_and_ranks_deterministically(
    tmp_path: Path,
) -> None:
    store = SqlCipherVectorStore(await migrated_database(tmp_path / "app.sqlite"))
    first = [1.0, *([0.0] * 31)]
    second = [0.8, 0.6, *([0.0] * 30)]
    opposite = [-1.0, *([0.0] * 31)]

    await store.upsert(
        [
            vector_record("memory-a", first),
            vector_record("message-b", second, source_type=RetrievalSourceType.RAW_USER),
            vector_record("memory-c", opposite),
        ]
    )
    ranked = await store.query(vector_query(first))
    stale_profile = await store.query(vector_query(first, configuration_sha256="d" * 64))

    assert [(item.source_id, item.rank) for item in ranked] == [
        ("memory-a", 1),
        ("message-b", 2),
        ("memory-c", 3),
    ]
    assert ranked[0].score == pytest.approx(1.0)
    assert stale_profile == []
    assert (await store.health()).status is ComponentStatus.HEALTHY


@pytest.mark.asyncio
async def test_vector_store_rebuild_and_delete_treat_index_as_disposable(tmp_path: Path) -> None:
    store = SqlCipherVectorStore(await migrated_database(tmp_path / "app.sqlite"))
    vector = [1.0, *([0.0] * 31)]
    await store.upsert([vector_record("old", vector)])

    await store.rebuild([vector_record("rebuilt", vector)])
    assert [item.source_id for item in await store.query(vector_query(vector))] == ["rebuilt"]

    await store.delete("memory", "rebuilt")
    assert await store.query(vector_query(vector)) == []


@pytest.mark.asyncio
async def test_lexical_index_upsert_replace_rebuild_and_delete(tmp_path: Path) -> None:
    database = await migrated_database(tmp_path / "app.sqlite")
    index = SqlCipherLexicalIndex(database)
    original = RetrievalDocument(
        source_type=RetrievalSourceType.RAW_USER,
        source_id="message-one",
        content="La terrasse était derrière l'église à Lausanne.",
        content_sha256=sha256(b"original").hexdigest(),
        updated_at="2026-08-13T00:00:00+00:00",
    )
    replacement = original.model_copy(
        update={
            "content": "Le code rare est ZX-4815.",
            "content_sha256": sha256(b"replacement").hexdigest(),
        }
    )
    await index.upsert([original])
    await index.upsert([replacement])

    async with database.connect() as connection:
        old_hits = await (
            await connection.execute(
                "SELECT source_id FROM retrieval_fts WHERE retrieval_fts MATCH ?", ("Lausanne",)
            )
        ).fetchall()
        new_hits = await (
            await connection.execute(
                "SELECT source_id FROM retrieval_fts WHERE retrieval_fts MATCH ?", ("ZX",)
            )
        ).fetchall()
    assert old_hits == []
    assert new_hits == [("message-one",)]

    await index.rebuild([original])
    await index.delete("raw_user", "message-one")
    async with database.connect() as connection:
        count = await (await connection.execute("SELECT COUNT(*) FROM retrieval_fts")).fetchone()
    assert count == (0,)
    assert (await index.health()).status is ComponentStatus.HEALTHY


def test_vector_store_rejects_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        from backend.app.retrieval.store import _encode_vector

        _encode_vector([1.0], 32)
