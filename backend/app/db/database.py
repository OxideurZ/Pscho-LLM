from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: Path, migrations_dir: Path) -> None:
        self.path = path
        self.migrations_dir = migrations_dir

    async def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            for migration in sorted(self.migrations_dir.glob("*.sql")):
                await connection.executescript(migration.read_text(encoding="utf-8"))
            await connection.commit()

    async def health(self) -> bool:
        try:
            async with aiosqlite.connect(self.path) as connection:
                await connection.execute("SELECT 1")
            return True
        except aiosqlite.Error:
            return False
