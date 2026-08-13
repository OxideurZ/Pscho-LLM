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
