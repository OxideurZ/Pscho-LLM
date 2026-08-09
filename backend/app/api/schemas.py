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
