import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from backend.app.chat.sse import serialize_sse
from backend.app.config.metadata import git_commit, runtime_info
from backend.app.config.prompt import load_prompt
from backend.app.config.settings import Settings
from backend.app.context import ContextBudget, ContextBudgetExceededError, ContextBuilder
from backend.app.conversations.models import MessageStatus, Turn
from backend.app.conversations.repository import ConversationRepository, new_id
from backend.app.llm.base import LLMBackend
from backend.app.llm.errors import LLMError
from backend.app.llm.models import GenerationOptions, LLMStreamEvent
from backend.app.retrieval import (
    RetrievalAuditRepository,
    RetrievalContextAssembler,
    RetrievalProfile,
    RetrievalQuery,
    RetrievalService,
)
from backend.app.runs import ActiveRun, RunRegistry, RunStatus
from backend.app.summaries import SummaryGenerationError

logger = logging.getLogger(__name__)
_END = object()


class ConversationChatService:
    def __init__(
        self,
        settings: Settings,
        backend: LLMBackend,
        registry: RunRegistry,
        repository: ConversationRepository,
        context_builder: ContextBuilder,
        repository_root: Path,
        retrieval_service: RetrievalService | None = None,
        retrieval_assembler: RetrievalContextAssembler | None = None,
        retrieval_audit: RetrievalAuditRepository | None = None,
        retrieval_profile: RetrievalProfile | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.registry = registry
        self.repository = repository
        self.context_builder = context_builder
        self.repository_root = repository_root
        self.retrieval_service = retrieval_service
        self.retrieval_assembler = retrieval_assembler
        self.retrieval_audit = retrieval_audit
        self.retrieval_profile = retrieval_profile

    async def stream_turn(
        self,
        *,
        conversation_id: str,
        client_turn_id: str,
        content: str,
        input_type: str,
        options: GenerationOptions,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        prompt = load_prompt(
            self.repository_root, self.settings.prompt_id, self.settings.prompt_version
        )
        ids = {
            "user_message_id": new_id("msg"),
            "assistant_message_id": new_id("msg"),
            "session_id": new_id("session"),
            "run_id": new_id("run"),
            "memory_job_id": new_id("job"),
        }
        memory_job = None
        if self.settings.memory_enabled:
            memory_job = {
                "id": ids["memory_job_id"],
                "priority": 0,
                "dedupe_key": (
                    "memory_extract:"
                    f"{self.settings.memory_extraction_prompt_version}:"
                    f"{ids['user_message_id']}"
                ),
                "max_attempts": self.settings.memory_job_max_attempts,
            }
        turn = await self.repository.begin_turn(
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            content=content,
            input_type=input_type,
            ids=ids,
            memory_job=memory_job,
            run_metadata={
                "model_name": self.settings.model_name,
                "model_sha256": self.settings.model_expected_sha256,
                "backend_name": "llama.cpp",
                "backend_version": self.settings.llama_cpp_version,
                "backend_build": self.settings.llama_cpp_build,
                "prompt_id": prompt.id,
                "prompt_version": prompt.version,
                "prompt_sha256": prompt.sha256,
                "generation_config": options.model_dump(mode="json"),
                "seed": options.seed,
                "app_version": self.settings.app_version,
                "app_git_commit": git_commit(self.repository_root),
                "runtime_info": runtime_info(self.settings),
                "context_size": self.settings.context_size,
            },
        )
        if not turn.created:
            async for event in self._replay(turn):
                yield event
            return

        run = await self.registry.create(turn.run_id)
        logger.info(
            "conversation_turn_created conversation_id=%s run_id=%s user_sequence=%s",
            conversation_id,
            run.id,
            turn.user_message.sequence_no,
        )
        content_buffer = ""
        terminal_sent = False
        try:
            yield serialize_sse(
                "run_started",
                {
                    "run_id": run.id,
                    "conversation_id": conversation_id,
                    "user_message_id": turn.user_message.id,
                    "assistant_message_id": turn.assistant_message.id,
                    "reused": False,
                },
            )
            retrieval_context: str | None = None
            if (
                self.retrieval_service is not None
                and self.retrieval_assembler is not None
                and self.retrieval_profile is not None
            ):
                retrieval_started = monotonic()
                recent = await self.repository.context_messages(
                    conversation_id, turn.user_message.id
                )
                query = RetrievalQuery(
                    current_user_text=content,
                    current_message_id=turn.user_message.id,
                    conversation_id=conversation_id,
                    recent_context=[message.content for message in recent[-8:]],
                    timestamp=datetime.now(UTC).isoformat(),
                    exclusions={message.id for message in recent},
                )
                try:
                    run.task = asyncio.create_task(
                        self.retrieval_service.retrieve(query),
                        name=f"retrieval-{run.id}",
                    )
                    outcome = await run.task
                except asyncio.CancelledError:
                    if not run.cancel_event.is_set():
                        raise
                    await self._finalize(
                        run,
                        turn,
                        content_buffer,
                        MessageStatus.INTERRUPTED,
                        RunStatus.CANCELLED,
                    )
                    terminal_sent = True
                    yield serialize_sse("cancelled", {"run_id": run.id})
                    return
                finally:
                    run.task = None
                assembled = self.retrieval_assembler.assemble(
                    outcome.items,
                    recent_contents={message.content for message in recent},
                )
                retrieval_context = assembled.content
                if self.retrieval_audit is not None:
                    try:
                        await self.retrieval_audit.record(
                            model_run_id=run.id,
                            conversation_id=conversation_id,
                            query=content,
                            profile_version=self.retrieval_profile.version,
                            outcome=outcome,
                            candidates=outcome.items,
                            injected={
                                (
                                    item.document.source_type.value,
                                    item.document.source_id,
                                )
                                for item in assembled.items
                            },
                            started_at=retrieval_started,
                        )
                    except Exception as error:
                        logger.error(
                            "retrieval_audit_failed run_id=%s error_type=%s",
                            run.id,
                            type(error).__name__,
                        )

            try:
                context = await self.context_builder.build_context(
                    conversation_id=conversation_id,
                    current_message_id=turn.user_message.id,
                    system_prompt=prompt.content,
                    budget=ContextBudget(
                        max_context_tokens=self.settings.context_size,
                        reserved_output_tokens=options.max_tokens,
                        safety_margin_tokens=self.settings.context_safety_margin_tokens,
                        summary_budget_tokens=self.settings.summary_budget_tokens,
                        recent_raw_budget_tokens=self.settings.recent_raw_budget_tokens,
                    ),
                    cancel_event=run.cancel_event,
                    retrieval_context=retrieval_context,
                )
                upstream_messages = context.messages
            except asyncio.CancelledError:
                if not run.cancel_event.is_set():
                    raise
                await self._finalize(
                    run,
                    turn,
                    content_buffer,
                    MessageStatus.INTERRUPTED,
                    RunStatus.CANCELLED,
                )
                terminal_sent = True
                yield serialize_sse("cancelled", {"run_id": run.id})
                return
            except (ContextBudgetExceededError, SummaryGenerationError) as error:
                await self._finalize(
                    run,
                    turn,
                    content_buffer,
                    MessageStatus.FAILED,
                    RunStatus.FAILED,
                    error.code,
                )
                terminal_sent = True
                yield serialize_sse("error", {"code": error.code, "retryable": False})
                return
            except Exception as error:
                logger.error(
                    "context_build_failed conversation_id=%s run_id=%s error_type=%s",
                    conversation_id,
                    run.id,
                    type(error).__name__,
                )
                await self._finalize(
                    run,
                    turn,
                    content_buffer,
                    MessageStatus.FAILED,
                    RunStatus.FAILED,
                    "CONTEXT_BUILD_FAILED",
                )
                terminal_sent = True
                yield serialize_sse("error", {"code": "CONTEXT_BUILD_FAILED", "retryable": False})
                return

            if run.cancel_event.is_set():
                await self._finalize(
                    run,
                    turn,
                    content_buffer,
                    MessageStatus.INTERRUPTED,
                    RunStatus.CANCELLED,
                )
                terminal_sent = True
                yield serialize_sse("cancelled", {"run_id": run.id})
                return

            queue: asyncio.Queue[LLMStreamEvent | LLMError | object] = asyncio.Queue()

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

            run.task = asyncio.create_task(produce(), name=f"conversation-llm-{run.id}")
            run.task.add_done_callback(lambda _task: queue.put_nowait(_END))
            checkpoint_length = 0
            last_checkpoint = monotonic()

            while True:
                if await is_disconnected():
                    await self.registry.cancel(run.id)
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                if item is _END:
                    if run.cancel_event.is_set():
                        await self._finalize(
                            run,
                            turn,
                            content_buffer,
                            MessageStatus.INTERRUPTED,
                            RunStatus.CANCELLED,
                        )
                        terminal_sent = True
                        yield serialize_sse("cancelled", {"run_id": run.id})
                    elif not run.status.terminal:
                        await self._finalize(
                            run, turn, content_buffer, MessageStatus.COMPLETE, RunStatus.COMPLETE
                        )
                        terminal_sent = True
                        yield serialize_sse("done", {})
                    break
                if isinstance(item, LLMError):
                    await self._finalize(
                        run,
                        turn,
                        content_buffer,
                        MessageStatus.FAILED,
                        RunStatus.FAILED,
                        item.code,
                    )
                    terminal_sent = True
                    yield serialize_sse("error", {"code": item.code, "retryable": item.retryable})
                    break
                if item.type == "generation_started":
                    await self.registry.transition(run, RunStatus.GENERATING)
                    await self.repository.mark_generating(run.id)
                elif item.type == "delta" and item.text:
                    content_buffer += item.text
                    now = monotonic()
                    if (
                        now - last_checkpoint >= self.settings.stream_checkpoint_seconds
                        or len(content_buffer) - checkpoint_length
                        >= self.settings.stream_checkpoint_characters
                    ):
                        await self.repository.checkpoint(turn.assistant_message.id, content_buffer)
                        checkpoint_length = len(content_buffer)
                        last_checkpoint = now
                    yield serialize_sse("delta", {"text": item.text})
                elif item.type == "metrics" and item.metrics is not None:
                    await self.repository.update_run_metrics(run.id, item.metrics)
                    yield serialize_sse("metrics", item.metrics)
        finally:
            if run.task is not None and not run.task.done():
                run.cancel_event.set()
                run.task.cancel()
            if run.task is not None:
                with suppress(asyncio.CancelledError):
                    await run.task
            if not terminal_sent and not run.status.terminal:
                await self._finalize(
                    run,
                    turn,
                    content_buffer,
                    MessageStatus.INTERRUPTED,
                    RunStatus.CANCELLED,
                )
            await self.registry.cleanup(run.id)

    async def _finalize(
        self,
        run: ActiveRun,
        turn: Turn,
        content: str,
        message_status: MessageStatus,
        run_status: RunStatus,
        error_code: str | None = None,
    ) -> None:
        if not run.status.terminal:
            await self.registry.transition(run, run_status)
        await self.repository.finalize(
            conversation_id=turn.conversation_id,
            assistant_message_id=turn.assistant_message.id,
            run_id=run.id,
            content=content,
            message_status=message_status.value,
            run_status=run_status.value,
            error_code=error_code,
        )

    async def _replay(self, turn: Turn) -> AsyncIterator[str]:
        yield serialize_sse(
            "run_started",
            {
                "run_id": turn.run_id,
                "conversation_id": turn.conversation_id,
                "user_message_id": turn.user_message.id,
                "assistant_message_id": turn.assistant_message.id,
                "reused": True,
                "idempotency_code": "TURN_ALREADY_EXISTS",
            },
        )
        if turn.assistant_message.content:
            yield serialize_sse("delta", {"text": turn.assistant_message.content})
        status = turn.assistant_message.status
        if status is MessageStatus.COMPLETE:
            yield serialize_sse("done", {})
        elif status is MessageStatus.INTERRUPTED:
            yield serialize_sse("cancelled", {"run_id": turn.run_id})
        elif status is MessageStatus.FAILED:
            yield serialize_sse(
                "error",
                {"code": turn.run_error_code or "GENERATION_FAILED", "retryable": False},
            )
        else:
            yield serialize_sse("turn_state", {"run_id": turn.run_id, "status": status.value})
