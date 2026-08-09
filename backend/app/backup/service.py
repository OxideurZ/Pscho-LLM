import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlcipher3 import dbapi2 as sqlcipher


class BackupError(RuntimeError):
    code = "BACKUP_FAILED"


class BackupIntegrityError(BackupError):
    code = "BACKUP_INTEGRITY_FAILED"


class RestoreError(BackupError):
    code = "RESTORE_FAILED"


class BackupService:
    def __init__(
        self, database_path: Path, backup_directory: Path, encryption_key: bytes | None = None
    ) -> None:
        self.database_path = database_path
        self.backup_directory = backup_directory
        self.encryption_key = encryption_key

    async def create_snapshot(self, destination: Path | None = None) -> Path:
        if not self.database_path.exists():
            raise BackupError("Source database does not exist")
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination or self.backup_directory / (
            f"psych-local-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}.sqlite"
        )
        if resolved_destination.exists():
            raise BackupError("Backup destination already exists")
        try:
            await asyncio.to_thread(
                self._backup_database,
                self.database_path,
                resolved_destination,
                self.encryption_key,
            )
            await self.verify_snapshot(resolved_destination)
            return resolved_destination
        except BackupIntegrityError:
            raise
        except (OSError, sqlite3.Error, sqlcipher.Error) as error:
            raise BackupError("SQLite backup failed") from error

    async def verify_snapshot(self, snapshot: Path) -> None:
        try:
            integrity, foreign_keys = await asyncio.to_thread(
                self._verify, snapshot, self.encryption_key
            )
        except (OSError, sqlite3.Error, sqlcipher.Error) as error:
            raise BackupIntegrityError("Snapshot could not be opened") from error
        if integrity != "ok" or foreign_keys:
            raise BackupIntegrityError(
                f"Snapshot integrity failed: integrity={integrity} fk_count={len(foreign_keys)}"
            )

    async def restore_snapshot(self, snapshot: Path, destination: Path) -> Path:
        await self.verify_snapshot(snapshot)
        if await asyncio.to_thread(destination.exists):
            raise RestoreError("Restore destination already exists")
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(
                self._backup_database, snapshot, destination, self.encryption_key
            )
            await self.verify_snapshot(destination)
            return destination
        except BackupIntegrityError:
            raise
        except (OSError, sqlite3.Error, sqlcipher.Error) as error:
            raise RestoreError("SQLite restore failed") from error

    @staticmethod
    def _backup_database(
        source_path: Path, destination_path: Path, encryption_key: bytes | None = None
    ) -> None:
        if encryption_key is not None:
            source = sqlcipher.connect(str(source_path), timeout=30)
            destination = sqlcipher.connect(str(destination_path), timeout=30)
            try:
                key_literal = encryption_key.hex()
                source.execute(f"PRAGMA key = \"x'{key_literal}'\"")
                source.execute("PRAGMA cipher_compatibility = 4")
                destination.execute(f"PRAGMA key = \"x'{key_literal}'\"")
                destination.execute("PRAGMA cipher_compatibility = 4")
                source.backup(destination)
                destination.commit()
            finally:
                source.close()
                destination.close()
            return
        with sqlite3.connect(source_path, timeout=30) as source:
            with sqlite3.connect(destination_path, timeout=30) as destination:
                source.backup(destination)

    @staticmethod
    def _verify(
        path: Path, encryption_key: bytes | None = None
    ) -> tuple[str, list[tuple[object, ...]]]:
        if encryption_key is not None:
            connection = sqlcipher.connect(str(path), timeout=30)
            try:
                key_literal = encryption_key.hex()
                connection.execute(f"PRAGMA key = \"x'{key_literal}'\"")
                connection.execute("PRAGMA cipher_compatibility = 4")
                connection.execute("PRAGMA foreign_keys = ON")
                integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
                foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                return str(integrity_row[0]) if integrity_row else "missing", foreign_keys
            finally:
                connection.close()
        with sqlite3.connect(path, timeout=30) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        return str(integrity_row[0]) if integrity_row else "missing", foreign_keys
