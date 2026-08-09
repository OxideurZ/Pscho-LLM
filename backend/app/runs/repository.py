import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.runs.models import ActiveRun, RunStatus


class RunRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def create(
        self,
        run: ActiveRun,
        *,
        model_name: str,
        model_sha256: str,
        backend_name: str,
        backend_version: str,
        backend_build: str | None,
        prompt_id: str,
        prompt_version: str,
        prompt_sha256: str,
        generation_config: dict[str, Any],
        seed: int | None,
        app_version: str,
        app_git_commit: str | None,
        runtime_info: dict[str, Any],
        context_size: int,
    ) -> None:
        values = (
            run.id,
            run.status.value,
            model_name,
            model_sha256,
            backend_name,
            backend_version,
            backend_build,
            prompt_id,
            prompt_version,
            prompt_sha256,
            json.dumps(generation_config, sort_keys=True, separators=(",", ":")),
            seed,
            app_version,
            app_git_commit,
            json.dumps(runtime_info, sort_keys=True, separators=(",", ":")),
            context_size,
            run.started_at.isoformat(),
        )
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                INSERT INTO model_runs (
                    id, status, model_name, model_sha256, backend_name, backend_version,
                    backend_build, prompt_id, prompt_version, prompt_sha256,
                    generation_config_json, seed, app_version, app_git_commit,
                    runtime_info_json, context_size, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            await connection.commit()

    async def update_status(
        self, run_id: str, status: RunStatus, error_code: str | None = None
    ) -> None:
        completed_at = datetime.now(UTC).isoformat() if status.terminal else None
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "UPDATE model_runs SET status = ?, completed_at = ?, error_code = ? WHERE id = ?",
                (status.value, completed_at, error_code, run_id),
            )
            await connection.commit()

    async def update_metrics(self, run_id: str, metrics: dict[str, int | float | None]) -> None:
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                UPDATE model_runs SET
                    input_tokens = ?, output_tokens = ?, ttft_ms = ?, prompt_eval_ms = ?,
                    generation_ms = ?, total_ms = ?, tokens_per_second = ?
                WHERE id = ?
                """,
                (
                    metrics.get("input_tokens"),
                    metrics.get("output_tokens"),
                    metrics.get("ttft_ms"),
                    metrics.get("prompt_eval_ms"),
                    metrics.get("generation_ms"),
                    metrics.get("total_ms"),
                    metrics.get("tokens_per_second"),
                    run_id,
                ),
            )
            await connection.commit()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute("SELECT * FROM model_runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
