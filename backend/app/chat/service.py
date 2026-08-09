import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from backend.app.chat.sse import serialize_sse
from backend.app.config.metadata import git_commit, runtime_info
from backend.app.config.prompt import load_prompt
from backend.app.config.settings import Settings
from backend.app.llm.base import LLMBackend
from backend.app.llm.errors import LLMError
from backend.app.llm.models import GenerationOptions, LLMMessage, LLMRole, LLMStreamEvent
from backend.app.runs import ActiveRun, RunRegistry, RunRepository, RunStatus

logger = logging.getLogger(__name__)
_END = object()


class ChatService:
    def __init__(
        self,
        settings: Settings,
        backend: LLMBackend,
        registry: RunRegistry,
        repository: RunRepository,
        repository_root: Path,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.registry = registry
        self.repository = repository
        self.repository_root = repository_root

    async def stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        run = await self.registry.create()
        prompt = load_prompt(
            self.repository_root, self.settings.prompt_id, self.settings.prompt_version
        )
        await self.repository.create(
            run,
            model_name=self.settings.model_name,
            model_sha256=self.settings.model_expected_sha256,
            backend_name="llama.cpp",
            backend_version=self.settings.llama_cpp_version,
            backend_build=self.settings.llama_cpp_build,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            generation_config=options.model_dump(mode="json"),
            seed=options.seed,
            app_version=self.settings.app_version,
            app_git_commit=git_commit(self.repository_root),
            runtime_info=runtime_info(self.settings),
            context_size=self.settings.context_size,
        )
        logger.info("run_created run_id=%s status=starting", run.id)
        yield serialize_sse("run_started", {"run_id": run.id})

        queue: asyncio.Queue[LLMStreamEvent | LLMError | object] = asyncio.Queue()
        upstream_messages = [LLMMessage(role=LLMRole.SYSTEM, content=prompt.content), *messages]

        async def produce() -> None:
            try:
                async for event in self.backend.chat_stream(upstream_messages, options, run):
                    queue.put_nowait(event)
            except asyncio.CancelledError:
                pass
            except LLMError as error:
                queue.put_nowait(error)
            except Exception:
                logger.error("unexpected_generation_error run_id=%s", run.id)
                queue.put_nowait(LLMError("Unexpected generation failure"))

        run.task = asyncio.create_task(produce(), name=f"llm-{run.id}")
        run.task.add_done_callback(lambda _task: queue.put_nowait(_END))
        terminal_sent = False
        metrics: dict[str, int | float | None] | None = None

        try:
            while True:
                if await is_disconnected():
                    await self.registry.cancel(run.id)
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue

                if item is _END:
                    if run.cancel_event.is_set():
                        await self._finish(run, RunStatus.CANCELLED)
                        terminal_sent = True
                        yield serialize_sse("cancelled", {"run_id": run.id})
                    elif not run.status.terminal:
                        await self._finish(run, RunStatus.COMPLETE)
                        terminal_sent = True
                        yield serialize_sse("done", {})
                    break
                if isinstance(item, LLMError):
                    await self._finish(run, RunStatus.FAILED, item.code)
                    terminal_sent = True
                    yield serialize_sse("error", {"code": item.code, "retryable": item.retryable})
                    break
                if item.type == "generation_started":
                    await self.registry.transition(run, RunStatus.GENERATING)
                    await self.repository.update_status(run.id, RunStatus.GENERATING)
                    logger.info("run_generating run_id=%s", run.id)
                elif item.type == "delta" and item.text:
                    yield serialize_sse("delta", {"text": item.text})
                elif item.type == "metrics" and item.metrics is not None:
                    metrics = item.metrics
                    await self.repository.update_metrics(run.id, metrics)
                    yield serialize_sse("metrics", metrics)
        finally:
            if run.task is not None and not run.task.done():
                run.cancel_event.set()
                run.task.cancel()
            if run.task is not None:
                with suppress(asyncio.CancelledError):
                    await run.task
            if not terminal_sent and not run.status.terminal:
                await self._finish(run, RunStatus.CANCELLED)
            await self.registry.cleanup(run.id)
            logger.info(
                "run_finished run_id=%s status=%s output_tokens=%s",
                run.id,
                run.status.value,
                metrics.get("output_tokens") if metrics else None,
            )

    async def _finish(
        self, run: ActiveRun, status: RunStatus, error_code: str | None = None
    ) -> None:
        if not run.status.terminal:
            await self.registry.transition(run, status)
            await self.repository.update_status(run.id, status, error_code)
