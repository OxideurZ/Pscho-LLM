import asyncio
import json
from pathlib import Path
from uuid import uuid4

import aiosqlite

from backend.app.config.metadata import git_commit, runtime_info
from backend.app.config.prompt import load_prompt
from backend.app.config.settings import Settings
from backend.app.jobs import JobKind, JobRecord
from backend.app.llm.base import LLMBackend
from backend.app.llm.models import LLMMessage, LLMRole
from backend.app.memory.models import MemoryExtractionResult
from backend.app.memory.repository import (
    MemoryBackfillBatch,
    MemoryExtractionBatch,
    MemoryRepository,
)
from backend.app.runs import ActiveRun, RunRepository, RunStatus


class UnsupportedMemoryJobError(RuntimeError):
    pass


class MemoryExtractor:
    def __init__(
        self,
        settings: Settings,
        backend: LLMBackend,
        memory_repository: MemoryRepository,
        run_repository: RunRepository,
        repository_root: Path,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.memory_repository = memory_repository
        self.run_repository = run_repository
        self.repository_root = repository_root

    async def run(
        self, job: JobRecord, cancel_event: asyncio.Event
    ) -> MemoryExtractionBatch | MemoryBackfillBatch:
        if job.kind is JobKind.MEMORY_BACKFILL:
            return await self.memory_repository.run_backfill(job, cancel_event)
        if job.kind is not JobKind.MEMORY_EXTRACT or job.source_message_id is None:
            raise UnsupportedMemoryJobError(job.kind)
        sources = await self.memory_repository.extraction_context(
            job.source_message_id, self.settings.memory_previous_user_context
        )
        prompt = load_prompt(
            self.repository_root,
            self.settings.memory_extraction_prompt_id,
            self.settings.memory_extraction_prompt_version,
        )
        payload = {
            "target_message_id": job.source_message_id,
            "user_messages": [
                {
                    "message_id": source.id,
                    "content": source.content,
                    "is_target": source.is_target,
                }
                for source in sources
            ],
        }
        messages = [
            LLMMessage(role=LLMRole.SYSTEM, content=prompt.content),
            LLMMessage(role=LLMRole.USER, content=json.dumps(payload, ensure_ascii=False)),
        ]
        run = ActiveRun(id=f"run_{uuid4().hex}")
        await self.run_repository.create(
            run,
            model_name=self.settings.model_name,
            model_sha256=self.settings.model_expected_sha256,
            backend_name="llama.cpp",
            backend_version=self.settings.llama_cpp_version,
            backend_build=self.settings.llama_cpp_build,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            generation_config={
                "temperature": 0,
                "seed": self.settings.default_seed,
                "max_tokens": self.settings.summary_budget_tokens,
                "schema": "MemoryExtractionResult:1.0",
            },
            seed=self.settings.default_seed,
            app_version=self.settings.app_version,
            app_git_commit=git_commit(self.repository_root),
            runtime_info=runtime_info(self.settings),
            context_size=self.settings.context_size,
            run_kind="memory_extract",
        )
        try:
            run.status = RunStatus.GENERATING
            await self.run_repository.update_status(run.id, RunStatus.GENERATING)
            extraction = await self.backend.generate_structured(
                messages, MemoryExtractionResult, cancel_event
            )
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            await self.run_repository.update_status(
                run.id, RunStatus.CANCELLED, "BACKGROUND_PREEMPTED"
            )
            raise
        except Exception as error:
            run.status = RunStatus.FAILED
            await self.run_repository.update_status(run.id, RunStatus.FAILED, type(error).__name__)
            raise
        run.status = RunStatus.COMPLETE
        await self.run_repository.update_status(run.id, RunStatus.COMPLETE)
        return MemoryExtractionBatch(
            run_id=run.id,
            target_message_id=job.source_message_id,
            sources=sources,
            extraction=extraction,
        )

    async def persist(
        self,
        connection: aiosqlite.Connection,
        job: JobRecord,
        result: object,
    ) -> None:
        if isinstance(result, MemoryExtractionBatch):
            await self.memory_repository.persist_extraction(connection, job, result)
            return
        if isinstance(result, MemoryBackfillBatch):
            await self.memory_repository.persist_backfill(
                connection, job, result, max_attempts=self.settings.memory_job_max_attempts
            )
            return
        raise TypeError("memory extractor received an invalid result")
