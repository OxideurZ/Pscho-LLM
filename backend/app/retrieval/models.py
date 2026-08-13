from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RetrievalSourceType(StrEnum):
    MEMORY = "memory"
    RAW_USER = "raw_user"


class ComponentStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ComponentStatus
    detail_code: str | None = None


class RetrievalModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    revision: str
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: str
    device: str
    dtype: str
    dimensions: int | None = None


class RetrievalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType
    source_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: str
    status: str | None = None
    kind: str | None = None
    epistemic_status: str | None = None
    observed_at: str | None = None
    event_start_at: str | None = None
    event_end_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    time_precision: str | None = None
    time_text: str | None = None
    conversation_id: str | None = None
    conversation_title: str | None = None
    created_at: str | None = None
    input_type: str | None = None


class VectorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType
    source_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model: str
    embedding_revision: str
    instruction_version: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: int = Field(ge=32, le=4096)
    dtype: str = "float32"
    vector: list[float]


class VectorQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding_model: str
    embedding_revision: str
    instruction_version: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: int = Field(ge=32, le=4096)
    dtype: str = "float32"
    vector: list[float]
    top_k: int = Field(ge=1, le=100)
    source_type: RetrievalSourceType | None = None


class ScoredSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType
    source_id: str
    score: float
    rank: int = Field(ge=1)


class RerankInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType
    source_id: str
    content: str


class RerankedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType
    source_id: str
    score: float
    rank: int = Field(ge=1)


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_user_text: str = Field(min_length=1)
    current_message_id: str
    conversation_id: str
    recent_context: list[str] = Field(default_factory=list, max_length=8)
    timestamp: str
    exclusions: set[str] = Field(default_factory=set)


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: RetrievalDocument
    lexical_rank: int | None = None
    dense_rank: int | None = None
    fused_rank: int | None = None
    fused_score: float | None = None
    reranker_score: float | None = None
    final_rank: int | None = None
    rejection_code: str | None = None


class RetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "retrieval_profile:v1.0"
    lexical_top_k: int = Field(ge=1, le=100)
    dense_top_k: int = Field(ge=1, le=100)
    rrf_top_k: int = Field(ge=1, le=100)
    rerank_top_k: int = Field(ge=1, le=32)
    max_items: int = Field(ge=1, le=20)
    rrf_constant: int = Field(default=60, ge=1)
    minimum_reranker_score: float | None = Field(default=None, ge=0, le=1)


class RetrievalOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    items: list[RetrievalCandidate]
    degraded: bool = False
    error_codes: list[str] = Field(default_factory=list)
