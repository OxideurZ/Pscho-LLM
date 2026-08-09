import asyncio
import sqlite3

import pytest

from backend.app.db.cipher import sqlcipher_connection


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
