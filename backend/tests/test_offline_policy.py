from pathlib import Path

from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT


def test_runtime_endpoints_are_loopback_only() -> None:
    settings = Settings()
    assert settings.psych_local_host in {"127.0.0.1", "localhost"}
    assert settings.llama_server_url.startswith("http://127.0.0.1:")


def test_runtime_code_has_no_telemetry_or_lan_bind() -> None:
    runtime_sources = [
        *Path(REPOSITORY_ROOT / "backend" / "app").rglob("*.py"),
        Path(REPOSITORY_ROOT / "scripts" / "launcher.py"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)
    assert "0.0.0.0" not in text
    assert "sentry" not in text.lower()
    assert "analytics" not in text.lower()
    assert "telemetry" not in text.lower()
