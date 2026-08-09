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
