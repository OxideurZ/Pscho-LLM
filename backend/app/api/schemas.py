from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.app.llm.models import GenerationOptions, LLMMessage, LLMRole


class ChatRequest(BaseModel):
    messages: list[LLMMessage] = Field(min_length=1, max_length=100)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)

    @model_validator(mode="after")
    def reject_client_system_messages(self) -> "ChatRequest":
        if any(message.role is LLMRole.SYSTEM for message in self.messages):
            raise ValueError("System messages are managed by Psych-local")
        return self


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None


class TurnRequest(BaseModel):
    client_turn_id: UUID
    content: str = Field(min_length=1, max_length=500_000)
    input_type: Literal["text", "voice"] = "text"
    generation: GenerationOptions = Field(default_factory=GenerationOptions)


class VoiceJobCreateRequest(BaseModel):
    voice_input_id: UUID
    conversation_id: str = Field(min_length=1, max_length=100)
    client_turn_id: UUID


class BootstrapRequest(BaseModel):
    bootstrap_token: str | None = Field(default=None, min_length=1, max_length=128)


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=4_000)
    kind: Literal["personal_fact", "event", "goal", "preference", "belief"] | None = None
    epistemic_status: Literal["stated", "interpretation", "uncertain"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one memory field is required")
        return self


class EntityUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=256)
