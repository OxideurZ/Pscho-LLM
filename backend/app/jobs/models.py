from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobKind(StrEnum):
    MEMORY_EXTRACT = "memory_extract"
    MEMORY_CONSOLIDATE = "memory_consolidate"
    MEMORY_BACKFILL = "memory_backfill"
    RETRIEVAL_INDEX_MESSAGE = "retrieval_index_message"
    RETRIEVAL_INDEX_MEMORY = "retrieval_index_memory"
    RETRIEVAL_REINDEX = "retrieval_reindex"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY = "retry"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: JobKind
    status: JobStatus
    priority: int
    dedupe_key: str
    source_message_id: str | None
    source_type: str | None = None
    source_id: str | None = None
    backfill_id: str | None = None
    blocked_by_run_id: str | None
    attempts: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    available_at: str
    started_at: str | None
    completed_at: str | None
    error_code: str | None
    execution_token: str | None
    cancel_requested_at: str | None
    created_at: str
    updated_at: str
