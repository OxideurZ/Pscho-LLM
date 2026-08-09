from enum import StrEnum

from pydantic import BaseModel


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class InputType(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    GENERATED = "generated"


class MessageStatus(StrEnum):
    COMPLETE = "complete"
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    DELETED = "deleted"


class Conversation(BaseModel):
    id: str
    title: str | None
    next_sequence_no: int
    created_at: str
    updated_at: str
    archived_at: str | None
    deleted_at: str | None


class Message(BaseModel):
    id: str
    conversation_id: str
    session_id: str | None
    sequence_no: int
    role: MessageRole
    content: str
    input_type: InputType
    status: MessageStatus
    created_at: str
    updated_at: str
    model_run_id: str | None
    client_turn_id: str | None
    excluded_from_ai: bool


class Turn(BaseModel):
    conversation_id: str
    client_turn_id: str
    user_message: Message
    assistant_message: Message
    run_id: str
    run_status: str
    run_error_code: str | None = None
    created: bool
