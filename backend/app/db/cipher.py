from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from sqlcipher3 import dbapi2 as sqlcipher


class CipherDatabaseError(RuntimeError):
    code = "DATABASE_LOCKED"


class CipherCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    async def fetchone(self) -> Any:
        return await asyncio.to_thread(self._cursor.fetchone)

    async def fetchall(self) -> list[Any]:
        return await asyncio.to_thread(self._cursor.fetchall)


class CipherConnection:
    """Async facade preserving the small aiosqlite API used by repositories."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @property
    def row_factory(self) -> Any:
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._connection.row_factory = sqlcipher.Row if value is aiosqlite.Row else value

    async def execute(self, sql: str, parameters: Iterable[Any] = ()) -> CipherCursor:
        try:
            cursor = await asyncio.to_thread(self._connection.execute, sql, tuple(parameters))
        except sqlcipher.IntegrityError as error:
            raise aiosqlite.IntegrityError(str(error)) from error
        except sqlcipher.Error as error:
            raise aiosqlite.Error(str(error)) from error
        return CipherCursor(cursor)

    async def executemany(self, sql: str, parameters: Iterable[Iterable[Any]]) -> CipherCursor:
        rows = [tuple(row) for row in parameters]
        try:
            cursor = await asyncio.to_thread(self._connection.executemany, sql, rows)
        except sqlcipher.IntegrityError as error:
            raise aiosqlite.IntegrityError(str(error)) from error
        except sqlcipher.Error as error:
            raise aiosqlite.Error(str(error)) from error
        return CipherCursor(cursor)

    async def executescript(self, script: str) -> None:
        try:
            await asyncio.to_thread(self._connection.executescript, script)
        except sqlcipher.IntegrityError as error:
            raise aiosqlite.IntegrityError(str(error)) from error
        except sqlcipher.Error as error:
            raise aiosqlite.Error(str(error)) from error

    async def commit(self) -> None:
        await asyncio.to_thread(self._connection.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._connection.rollback)

    async def close(self) -> None:
        await asyncio.to_thread(self._connection.close)


@asynccontextmanager
async def sqlcipher_connection(path: Path, key: bytes, busy_timeout_ms: int = 30_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await asyncio.to_thread(
        sqlcipher.connect, str(path), timeout=busy_timeout_ms / 1000, check_same_thread=False
    )
    wrapped = CipherConnection(connection)
    try:
        key_literal = key.hex()
        await wrapped.execute(f"PRAGMA key = \"x'{key_literal}'\"")
        await wrapped.execute("PRAGMA cipher_compatibility = 4")
        await wrapped.execute("PRAGMA foreign_keys = ON")
        journal_row = await (await wrapped.execute("PRAGMA journal_mode = WAL")).fetchone()
        await wrapped.execute(f"PRAGMA busy_timeout = {busy_timeout_ms:d}")
        foreign_keys_row = await (await wrapped.execute("PRAGMA foreign_keys")).fetchone()
        busy_timeout_row = await (await wrapped.execute("PRAGMA busy_timeout")).fetchone()
        journal_mode = str(journal_row[0]).lower() if journal_row else None
        foreign_keys = bool(foreign_keys_row and foreign_keys_row[0] == 1)
        actual_timeout = int(busy_timeout_row[0]) if busy_timeout_row else None
        if not foreign_keys or journal_mode != "wal" or actual_timeout != busy_timeout_ms:
            raise CipherDatabaseError(
                "SQLCipher pragmas are not active: "
                f"foreign_keys={foreign_keys} journal_mode={journal_mode} "
                f"busy_timeout={actual_timeout}"
            )
        yield wrapped
    finally:
        await wrapped.close()
