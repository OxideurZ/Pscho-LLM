import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from backend.app.db.cipher import sqlcipher_connection

MIGRATION_PATTERN = re.compile(r"^(?P<version>\d{3,4})_(?P<name>[a-z0-9_]+)\.sql$")


class DatabaseConfigurationError(RuntimeError):
    pass


class MigrationError(RuntimeError):
    code = "MIGRATION_FAILED"


class SchemaVersionError(MigrationError):
    code = "SCHEMA_VERSION_INVALID"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class DatabaseStatus:
    reachable: bool
    schema_current: bool
    schema_version: int | None
    expected_schema_version: int | None
    foreign_keys: bool
    journal_mode: str | None
    busy_timeout_ms: int | None
    migrations: str
    startup_reconciliation: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@asynccontextmanager
async def sqlite_connection(
    path: Path, busy_timeout_ms: int = 30_000, encryption_key: bytes | None = None
) -> AsyncIterator[aiosqlite.Connection]:
    if encryption_key is not None:
        async with sqlcipher_connection(path, encryption_key, busy_timeout_ms) as connection:
            yield connection  # type: ignore[misc]
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path, timeout=busy_timeout_ms / 1000)
    try:
        await connection.execute("PRAGMA foreign_keys = ON")
        journal_row = await (await connection.execute("PRAGMA journal_mode = WAL")).fetchone()
        await connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms:d}")
        foreign_keys_row = await (await connection.execute("PRAGMA foreign_keys")).fetchone()
        busy_timeout_row = await (await connection.execute("PRAGMA busy_timeout")).fetchone()
        journal_mode = str(journal_row[0]).lower() if journal_row else None
        foreign_keys = bool(foreign_keys_row and foreign_keys_row[0] == 1)
        actual_timeout = int(busy_timeout_row[0]) if busy_timeout_row else None
        if not foreign_keys or journal_mode != "wal" or actual_timeout != busy_timeout_ms:
            raise DatabaseConfigurationError(
                "SQLite pragmas are not active: "
                f"foreign_keys={foreign_keys} journal_mode={journal_mode} "
                f"busy_timeout={actual_timeout}"
            )
        yield connection
    finally:
        await connection.close()


class Database:
    def __init__(
        self,
        path: Path,
        migrations_dir: Path,
        busy_timeout_ms: int = 30_000,
        encryption_key: bytes | None = None,
    ) -> None:
        self.path = path
        self.migrations_dir = migrations_dir
        self.busy_timeout_ms = busy_timeout_ms
        self.encryption_key = encryption_key
        self.migration_status = "not_started"
        self.schema_version: int | None = None
        self.expected_schema_version: int | None = None
        self.reconciliation_completed = False

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with sqlite_connection(
            self.path, self.busy_timeout_ms, self.encryption_key
        ) as connection:
            yield connection

    def _discover_migrations(self) -> list[Migration]:
        migrations: list[Migration] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = MIGRATION_PATTERN.fullmatch(path.name)
            if match is None:
                raise SchemaVersionError(f"Invalid migration filename: {path.name}")
            migrations.append(
                Migration(
                    version=int(match.group("version")),
                    name=match.group("name"),
                    path=path,
                    checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        versions = [migration.version for migration in migrations]
        if not versions or versions != list(range(1, len(versions) + 1)):
            raise SchemaVersionError(f"Migration versions must be contiguous from 1: {versions}")
        return migrations

    async def migrate(self) -> None:
        self.migration_status = "running"
        try:
            migrations = self._discover_migrations()
            self.expected_schema_version = migrations[-1].version
            async with self.connect() as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                await connection.commit()
                connection.row_factory = aiosqlite.Row
                rows = await (
                    await connection.execute(
                        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                    )
                ).fetchall()
                applied = {int(row["version"]): row for row in rows}
                known_versions = {migration.version for migration in migrations}
                unknown = sorted(set(applied) - known_versions)
                if unknown:
                    raise SchemaVersionError(f"Unknown applied migrations: {unknown}")
                applied_versions = sorted(applied)
                if applied_versions and applied_versions != list(
                    range(1, applied_versions[-1] + 1)
                ):
                    raise SchemaVersionError(
                        f"Applied migrations are not a strict prefix: {applied_versions}"
                    )

                for migration in migrations:
                    previous = applied.get(migration.version)
                    if previous is not None:
                        if (
                            previous["name"] != migration.name
                            or previous["checksum"] != migration.checksum
                        ):
                            raise SchemaVersionError(
                                f"Migration {migration.version:03d} checksum or name changed"
                            )
                        continue
                    script = migration.path.read_text(encoding="utf-8")
                    applied_at = datetime.now(UTC).isoformat().replace("'", "''")
                    name = migration.name.replace("'", "''")
                    checksum = migration.checksum.replace("'", "''")
                    atomic_script = (
                        "BEGIN IMMEDIATE;\n"
                        f"{script.rstrip()}\n"
                        "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                        f"VALUES ({migration.version}, '{name}', '{checksum}', '{applied_at}');\n"
                        "COMMIT;"
                    )
                    try:
                        await connection.executescript(atomic_script)
                    except aiosqlite.Error as error:
                        with suppress(aiosqlite.Error):
                            await connection.rollback()
                        raise MigrationError(
                            f"Migration {migration.version:03d}_{migration.name} failed"
                        ) from error
                self.schema_version = self.expected_schema_version
                self.migration_status = "current"
        except Exception:
            self.migration_status = "failed"
            raise

    def mark_reconciliation_completed(self) -> None:
        self.reconciliation_completed = True

    async def health(self) -> DatabaseStatus:
        try:
            async with self.connect() as connection:
                version_row = await (
                    await connection.execute("SELECT MAX(version) FROM schema_migrations")
                ).fetchone()
                foreign_keys_row = await (
                    await connection.execute("PRAGMA foreign_keys")
                ).fetchone()
                journal_row = await (await connection.execute("PRAGMA journal_mode")).fetchone()
                timeout_row = await (await connection.execute("PRAGMA busy_timeout")).fetchone()
            schema_version = int(version_row[0]) if version_row and version_row[0] else 0
            schema_current = schema_version == self.expected_schema_version
            return DatabaseStatus(
                reachable=True,
                schema_current=schema_current,
                schema_version=schema_version,
                expected_schema_version=self.expected_schema_version,
                foreign_keys=bool(foreign_keys_row and foreign_keys_row[0] == 1),
                journal_mode=str(journal_row[0]).lower() if journal_row else None,
                busy_timeout_ms=int(timeout_row[0]) if timeout_row else None,
                migrations=self.migration_status,
                startup_reconciliation=self.reconciliation_completed,
            )
        except (aiosqlite.Error, DatabaseConfigurationError):
            return DatabaseStatus(
                reachable=False,
                schema_current=False,
                schema_version=None,
                expected_schema_version=self.expected_schema_version,
                foreign_keys=False,
                journal_mode=None,
                busy_timeout_ms=None,
                migrations=self.migration_status,
                startup_reconciliation=self.reconciliation_completed,
            )
