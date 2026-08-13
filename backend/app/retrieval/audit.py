from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from uuid import uuid4

import aiosqlite

from backend.app.db import Database
from backend.app.retrieval.models import RetrievalCandidate, RetrievalOutcome


class RetrievalAuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(
        self,
        *,
        model_run_id: str,
        conversation_id: str,
        query: str,
        profile_version: str,
        outcome: RetrievalOutcome,
        candidates: list[RetrievalCandidate],
        injected: set[tuple[str, str]],
        started_at: float,
    ) -> str:
        identifier = f"retrieval_{uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        duration_ms = max(0.0, (monotonic() - started_at) * 1000)
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO retrieval_runs(
                        id, model_run_id, conversation_id, query_sha256,
                        profile_version, mode, degraded, error_codes_json,
                        duration_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        model_run_id,
                        conversation_id,
                        sha256(query.encode("utf-8")).hexdigest(),
                        profile_version,
                        outcome.mode,
                        int(outcome.degraded),
                        json.dumps(outcome.error_codes, separators=(",", ":")),
                        duration_ms,
                        now,
                    ),
                )
                for candidate in candidates:
                    document = candidate.document
                    key = (document.source_type.value, document.source_id)
                    await connection.execute(
                        """
                        INSERT INTO retrieval_run_items(
                            retrieval_run_id, source_type, source_id, content_sha256,
                            lexical_rank, dense_rank, fused_rank, reranker_score,
                            final_rank, injected, rejection_code
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identifier,
                            *key,
                            document.content_sha256,
                            candidate.lexical_rank,
                            candidate.dense_rank,
                            candidate.fused_rank,
                            candidate.reranker_score,
                            candidate.final_rank,
                            int(key in injected),
                            candidate.rejection_code,
                        ),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return identifier

    async def for_model_run(self, model_run_id: str) -> dict[str, object] | None:
        async with self.database.connect() as connection:
            connection.row_factory = aiosqlite.Row
            run = await (
                await connection.execute(
                    "SELECT * FROM retrieval_runs WHERE model_run_id = ?", (model_run_id,)
                )
            ).fetchone()
            if run is None:
                return None
            items = await (
                await connection.execute(
                    """
                    SELECT source_type, source_id, lexical_rank, dense_rank, fused_rank,
                           reranker_score, final_rank, injected, rejection_code
                    FROM retrieval_run_items WHERE retrieval_run_id = ?
                    ORDER BY COALESCE(final_rank, 2147483647), source_type, source_id
                    """,
                    (run["id"],),
                )
            ).fetchall()
        result = dict(run)
        result["error_codes"] = json.loads(str(result.pop("error_codes_json")))
        result["items"] = [dict(item) for item in items]
        return result
