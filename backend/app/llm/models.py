from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    role: LLMRole
    content: str = Field(min_length=1, max_length=500_000)


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int = Field(default=800, gt=0, le=32_768)
    seed: int | None = None
    stop: list[str] = Field(default_factory=list)


class LLMStreamEvent(BaseModel):
    type: Literal["generation_started", "delta", "metrics"]
    text: str | None = None
    metrics: dict[str, int | float | None] | None = None


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    backend: str
    model_loaded: bool = False


class ModelInfo(BaseModel):
    backend: str
    model: str
    available: bool
    context_size: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
