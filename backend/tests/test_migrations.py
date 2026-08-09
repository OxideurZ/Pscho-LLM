import shutil
from pathlib import Path

import aiosqlite
import pytest

from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database, MigrationError, SchemaVersionError


def copy_migrations(destination: Path) -> Path:
    shutil.copytree(REPOSITORY_ROOT / "migrations", destination)
    return destination


@pytest.mark.asyncio
async def test_fresh_database_applies_ordered_checksumed_migrations(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "app.sqlite", REPOSITORY_ROOT / "migrations")

    await database.migrate()
    database.mark_reconciliation_completed()
    status = await database.health()

    assert status.reachable is True
    assert status.schema_current is True
    assert status.schema_version == 7
    assert status.expected_schema_version == 7
    assert status.foreign_keys is True
    assert status.journal_mode == "wal"
    assert status.busy_timeout_ms == 30_000
    assert status.migrations == "current"
    assert status.startup_reconciliation is True
    async with database.connect() as connection:
        rows = await (
            await connection.execute(
                "SELECT version, name, length(checksum) FROM schema_migrations ORDER BY version"
            )
        ).fetchall()
    assert rows == [
        (1, "model_runs", 64),
        (2, "conversations", 64),
        (3, "sessions", 64),
        (4, "messages", 64),
        (5, "model_run_kind", 64),
        (6, "summaries", 64),
        (7, "summary_sources", 64),
    ]


@pytest.mark.asyncio
async def test_existing_milestone_a_database_migrates_without_losing_runs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "milestone-a.sqlite"
    async with aiosqlite.connect(path) as connection:
        await connection.executescript(
            (REPOSITORY_ROOT / "migrations" / "001_model_runs.sql").read_text(encoding="utf-8")
        )
        await connection.execute(
            """
            INSERT INTO model_runs (
                id, status, model_name, model_sha256, backend_name, backend_version,
                prompt_id, prompt_version, prompt_sha256, generation_config_json,
                app_version, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_a",
                "complete",
                "model",
                "a" * 64,
                "llama.cpp",
                "b9637",
                "conversation_system",
                "0.1.2",
                "b" * 64,
                "{}",
                "0.1.0-dev",
                "2026-08-09T00:00:00+00:00",
            ),
        )
        await connection.commit()

    database = Database(path, REPOSITORY_ROOT / "migrations")
    await database.migrate()

    async with database.connect() as connection:
        row = await (
            await connection.execute(
                "SELECT id, status, run_kind FROM model_runs WHERE id = 'run_a'"
            )
        ).fetchone()
        foreign_key_violations = await (
            await connection.execute("PRAGMA foreign_key_check")
        ).fetchall()
    assert row == ("run_a", "complete", "chat")
    assert foreign_key_violations == []


@pytest.mark.asyncio
async def test_changed_applied_migration_checksum_refuses_startup(tmp_path: Path) -> None:
    migrations = copy_migrations(tmp_path / "migrations")
    database = Database(tmp_path / "app.sqlite", migrations)
    await database.migrate()
    first = migrations / "001_model_runs.sql"
    first.write_text(first.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")

    with pytest.raises(SchemaVersionError, match="checksum or name changed"):
        await Database(tmp_path / "app.sqlite", migrations).migrate()


@pytest.mark.asyncio
async def test_invalid_or_failed_migration_never_reports_current(tmp_path: Path) -> None:
    invalid_order = copy_migrations(tmp_path / "invalid-order")
    (invalid_order / "009_gap.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(SchemaVersionError, match="contiguous"):
        await Database(tmp_path / "order.sqlite", invalid_order).migrate()

    failing = copy_migrations(tmp_path / "failing")
    (failing / "007_summary_sources.sql").write_text("CREATE TABLE broken(;\n", encoding="utf-8")
    database = Database(tmp_path / "failed.sqlite", failing)
    with pytest.raises(MigrationError, match="007_summary_sources failed"):
        await database.migrate()
    status = await database.health()
    assert status.schema_current is False
    assert status.migrations == "failed"


def test_default_runtime_database_is_outside_repository() -> None:
    settings = Settings(_env_file=None)

    assert not settings.database_path.resolve().is_relative_to(REPOSITORY_ROOT.resolve())
    assert settings.database_path.name == "app.sqlite"
