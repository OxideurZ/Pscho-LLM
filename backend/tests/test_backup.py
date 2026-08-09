import sqlite3
from pathlib import Path

import pytest

from backend.app.backup import BackupIntegrityError, BackupService, RestoreError
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.conversations import ConversationRepository
from backend.app.db import Database


@pytest.mark.asyncio
async def test_active_wal_database_backup_and_restore_are_consistent(tmp_path: Path) -> None:
    original = tmp_path / "runtime" / "data" / "app.sqlite"
    database = Database(original, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    before = await repository.create("Avant snapshot")
    service = BackupService(original, tmp_path / "runtime" / "backups")

    snapshot = await service.create_snapshot()
    after = await repository.create("Après snapshot")
    restored = await service.restore_snapshot(snapshot, tmp_path / "restored" / "app.sqlite")

    restored_database = Database(restored, REPOSITORY_ROOT / "migrations")
    await restored_database.migrate()
    restored_repository = ConversationRepository(restored_database)
    conversations = await restored_repository.list(limit=100, offset=0)
    assert [conversation.id for conversation in conversations] == [before.id]
    assert after.id not in {conversation.id for conversation in conversations}
    await service.verify_snapshot(snapshot)
    async with restored_database.connect() as connection:
        assert (await (await connection.execute("PRAGMA integrity_check")).fetchone()) == ("ok",)
        assert await (await connection.execute("PRAGMA foreign_key_check")).fetchall() == []


@pytest.mark.asyncio
async def test_restore_refuses_overwrite_and_corrupt_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "app.sqlite"
    database = Database(source, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    service = BackupService(source, tmp_path / "backups")
    snapshot = await service.create_snapshot()

    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"existing")
    with pytest.raises(RestoreError, match="already exists"):
        await service.restore_snapshot(snapshot, destination)

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(BackupIntegrityError):
        await service.verify_snapshot(corrupt)


@pytest.mark.asyncio
async def test_encrypted_backup_is_unreadable_without_database_key(tmp_path: Path) -> None:
    key = b"b" * 32
    original = tmp_path / "encrypted" / "app.sqlite"
    database = Database(original, REPOSITORY_ROOT / "migrations", encryption_key=key)
    await database.migrate()
    repository = ConversationRepository(database)
    await repository.create("Backup chiffré")
    service = BackupService(original, tmp_path / "backups", encryption_key=key)

    snapshot = await service.create_snapshot()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(snapshot).execute("SELECT * FROM conversations").fetchall()

    wrong_key_service = BackupService(original, tmp_path / "backups", encryption_key=b"w" * 32)
    with pytest.raises(BackupIntegrityError):
        await wrong_key_service.verify_snapshot(snapshot)

    restored = await service.restore_snapshot(snapshot, tmp_path / "restored" / "app.sqlite")
    restored_database = Database(restored, REPOSITORY_ROOT / "migrations", encryption_key=key)
    await restored_database.migrate()
    assert len(await ConversationRepository(restored_database).list(limit=10, offset=0)) == 1
