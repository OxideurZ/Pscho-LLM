#!/usr/bin/env python3
"""Apply all SQLite migrations in filename order."""

import asyncio

from backend.app.config import get_settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database


async def migrate() -> None:
    settings = get_settings()
    await Database(settings.database_path, REPOSITORY_ROOT / "migrations").migrate()
    print(f"SQLite ready: {settings.database_path}")


if __name__ == "__main__":
    asyncio.run(migrate())
