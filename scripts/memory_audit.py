"""Print aggregate-only Milestone F memory quality metrics for a local database."""

import asyncio
import json

from backend.app.config import get_settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.memory import MemoryRepository
from backend.app.security import WindowsDpapiSecretStore


async def main() -> None:
    settings = get_settings()
    database_key = None
    if settings.security_enabled:
        store = WindowsDpapiSecretStore(settings.data_directory / "security")
        database_key = store.create(settings.database_key_name)
    database = Database(
        settings.database_path,
        REPOSITORY_ROOT / "migrations",
        settings.sqlite_busy_timeout_ms,
        database_key,
    )
    await database.migrate()
    print(json.dumps(await MemoryRepository(database).audit_metrics(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
