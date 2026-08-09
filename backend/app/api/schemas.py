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
