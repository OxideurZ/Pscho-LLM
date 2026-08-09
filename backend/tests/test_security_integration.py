import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.fakes import FakeBackend

pytestmark = pytest.mark.skipif(
    os.environ.get("PSYCH_ELEVATED_TESTS") != "1",
    reason="requires Windows DPAPI test harness",
)


def test_secure_startup_migrates_existing_plaintext_database(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "app.sqlite"
    database_path.parent.mkdir()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('ULTRA_SECRET_DB_CARIBOU_E_2026')")
        connection.commit()
    finally:
        connection.close()
    settings = Settings(
        data_directory=tmp_path,
        database_path=database_path,
        security_enabled=True,
        model_expected_sha256="a" * 64,
    )
    with TestClient(create_app(settings, FakeBackend())) as client:
        response = client.get("/v1/health", headers={"host": "127.0.0.1:8000"})
        assert response.status_code == 200
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(database_path) as connection:
            connection.execute("SELECT * FROM marker").fetchall()
