"""Print aggregate-only Milestone F memory quality metrics for a local database."""

import asyncio
import json

from backend.app.config import get_settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.memory import MemoryRepository


async def main() -> None:
    settings = get_settings()
    database = Database(
        settings.database_path,
        REPOSITORY_ROOT / "migrations",
        settings.sqlite_busy_timeout_ms,
    )
    await database.migrate()
    print(json.dumps(await MemoryRepository(database).audit_metrics(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
