import asyncio
from collections.abc import AsyncIterator

from pydantic import BaseModel

from backend.app.llm.errors import LLMBackendUnavailable
from backend.app.llm.models import (
    GenerationOptions,
    HealthStatus,
    LLMMessage,
    LLMStreamEvent,
    ModelInfo,
)
from backend.app.runs.models import ActiveRun


class FakeBackend:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.closed = False
        self.requests: list[list[LLMMessage]] = []

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        run: ActiveRun,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append(messages)
        if self.mode == "unavailable":
            raise LLMBackendUnavailable("offline")
        yield LLMStreamEvent(type="generation_started")
        if self.mode == "slow":
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True
            return
        yield LLMStreamEvent(type="delta", text="Bonjour")
        yield LLMStreamEvent(
            type="metrics",
            metrics={
                "ttft_ms": 12,
                "input_tokens": 8,
                "output_tokens": 1,
                "prompt_eval_ms": 8,
                "generation_ms": 4,
                "total_ms": 12,
                "tokens_per_second": 250.0,
            },
        )

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel],
        cancel_event: asyncio.Event | None = None,
    ) -> BaseModel:
        raise NotImplementedError

    async def health(self) -> HealthStatus:
        available = self.mode != "unavailable"
        return HealthStatus(
            status="ok" if available else "unavailable",
            backend="fake",
            model_loaded=available,
        )

    async def model_info(self) -> ModelInfo:
        available = self.mode != "unavailable"
        return ModelInfo(
            backend="fake", model="test-model", available=available, context_size=32768
        )
