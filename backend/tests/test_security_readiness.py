from pathlib import Path

from backend.app.config import Settings
from backend.app.security.readiness import security_readiness


def test_readiness_fails_closed_when_security_is_disabled(tmp_path: Path) -> None:
    result = security_readiness(
        Settings(data_directory=tmp_path, database_path=tmp_path / "data" / "app.sqlite", security_enabled=False),
        secret_store=None,
        database_key=None,
    )
    assert result["state"] == "NOT_READY"
    assert "encrypted_database" in result["hard_failures"]
    assert "local_api_auth" in result["hard_failures"]
