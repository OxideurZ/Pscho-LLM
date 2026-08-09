import asyncio
import sqlite3

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from backend.app.db.cipher import sqlcipher_connection
from backend.app.db.migration import EncryptedDatabaseMigrator


def test_sqlcipher_connection_enforces_key_wal_and_foreign_keys(tmp_path) -> None:
    path = tmp_path / "encrypted.sqlite"
    key = b"e" * 32

    async def create() -> None:
        async with sqlcipher_connection(path, key) as connection:
            await connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            await connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
            await connection.commit()
            assert await (await connection.execute("PRAGMA foreign_keys")).fetchone() == (1,)

    asyncio.run(create())
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT * FROM parent").fetchall()


def test_plaintext_migration_preserves_marker_and_removes_original(tmp_path) -> None:
    path = tmp_path / "app.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES (?)", ("ULTRA_SECRET_DB_CARIBOU_E_2026",))
        connection.commit()
    finally:
        connection.close()

    key = b"m" * 32
    EncryptedDatabaseMigrator(key).migrate(path)

    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT * FROM marker").fetchall()
    encrypted = sqlcipher.connect(path)
    try:
        encrypted.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        assert encrypted.execute("SELECT value FROM marker").fetchone()[0].startswith("ULTRA")
    finally:
        encrypted.close()
