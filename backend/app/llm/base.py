import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel

from backend.app.llm.models import (
    GenerationOptions,
    HealthStatus,
    LLMMessage,
    LLMStreamEvent,
    ModelInfo,
)
from backend.app.runs.models import ActiveRun


class LLMBackend(Protocol):
    async def chat_stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        run: ActiveRun,
    ) -> AsyncIterator[LLMStreamEvent]: ...

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel],
        cancel_event: asyncio.Event | None = None,
    ) -> BaseModel: ...

    async def health(self) -> HealthStatus: ...

    async def model_info(self) -> ModelInfo: ...
