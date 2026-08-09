from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlcipher3 import dbapi2 as sqlcipher


class EncryptedMigrationError(RuntimeError):
    code = "SECURITY_MIGRATION_FAILED"


class EncryptedDatabaseMigrator:
    """Copy a validated plaintext DB into SQLCipher, then swap it atomically."""

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("Encryption key cannot be empty")
        self.key = key

    def migrate(self, source: Path, destination: Path | None = None) -> Path:
        source = source.resolve()
        target = (destination or source).resolve()
        if not source.is_file():
            raise EncryptedMigrationError("Plaintext source database does not exist")
        if destination is not None and target.exists() and target != source:
            raise EncryptedMigrationError("Encrypted destination already exists")
        counts, dump = self._read_plaintext(source)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.encrypted.tmp")
        try:
            self._write_encrypted(temporary, dump, counts)
            if target == source:
                backup = source.with_name(
                    f"{source.name}.pre-e-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
                )
                source.replace(backup)
                try:
                    temporary.replace(target)
                except OSError:
                    backup.replace(target)
                    raise
                try:
                    backup.unlink()
                except OSError:
                    # The encrypted DB is valid; readiness must surface this leftover plaintext
                    # artifact to the security audit instead of claiming a clean migration.
                    pass
            else:
                temporary.replace(target)
            return target
        except EncryptedMigrationError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error, sqlcipher.Error) as error:
            temporary.unlink(missing_ok=True)
            raise EncryptedMigrationError("Plaintext to encrypted migration failed") from error

    @staticmethod
    def _read_plaintext(path: Path) -> tuple[dict[str, int], str]:
        connection = None
        try:
            connection = sqlite3.connect(path)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if not integrity or integrity[0] != "ok" or foreign_keys:
                raise EncryptedMigrationError("Plaintext source failed integrity checks")
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            counts = {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in tables
            }
            return counts, "\n".join(connection.iterdump())
        except EncryptedMigrationError:
            raise
        except sqlite3.Error as error:
            raise EncryptedMigrationError("Plaintext source cannot be opened") from error
        finally:
            if connection is not None:
                connection.close()

    def _write_encrypted(self, path: Path, dump: str, expected_counts: dict[str, int]) -> None:
        connection = sqlcipher.connect(str(path))
        try:
            connection.execute(f"PRAGMA key = \"x'{self.key.hex()}'\"")
            connection.execute("PRAGMA cipher_compatibility = 4")
            connection.executescript(dump)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if not integrity or integrity[0] != "ok" or foreign_keys:
                raise EncryptedMigrationError("Encrypted destination failed integrity checks")
            for table, expected in expected_counts.items():
                actual = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                if actual != expected:
                    raise EncryptedMigrationError(
                        f"Encrypted destination count mismatch for table {table}"
                    )
        finally:
            connection.close()
