from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryKind(StrEnum):
    PERSONAL_FACT = "personal_fact"
    EVENT = "event"
    GOAL = "goal"
    PREFERENCE = "preference"
    BELIEF = "belief"


class EpistemicStatus(StrEnum):
    STATED = "stated"
    INTERPRETATION = "interpretation"
    UNCERTAIN = "uncertain"


class MemorySourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def validate_bounds(self) -> "MemorySourceSpan":
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class MemoryTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=512)
    start_at: str | None = Field(default=None, max_length=64)
    end_at: str | None = Field(default=None, max_length=64)
    precision: Literal["exact", "day", "week", "month", "year", "relative"] | None = None


class MemoryEntityDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str = Field(min_length=1, max_length=256)
    entity_type: Literal["person", "place", "organization", "other"]


class MemoryCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    content: str = Field(min_length=1, max_length=4_000)
    epistemic_status: EpistemicStatus
    source_spans: list[MemorySourceSpan] = Field(min_length=1, max_length=8)
    time: MemoryTime = Field(default_factory=MemoryTime)
    entities: list[MemoryEntityDraft] = Field(default_factory=list, max_length=12)


class MemoryExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    candidates: list[MemoryCandidateDraft] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def discard_unknown_time_fields(cls, value: object) -> object:
        """Tolerate llama.cpp emitting undocumented temporal compatibility keys.

        The persisted contract remains `start_at`/`end_at`; no temporal value is inferred from
        this compatibility-only field.
        """
        if not isinstance(value, dict):
            return value
        candidates = value.get("candidates")
        if not isinstance(candidates, list):
            return value
        normalized = dict(value)
        normalized_candidates: list[object] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("time"), dict):
                normalized_candidates.append(candidate)
                continue
            normalized_candidate = dict(candidate)
            normalized_time = {
                key: item
                for key, item in candidate["time"].items()
                if key in {"text", "start_at", "end_at", "precision"}
            }
            normalized_candidate["time"] = normalized_time
            normalized_candidates.append(normalized_candidate)
        normalized["candidates"] = normalized_candidates
        return normalized


class MemoryConsolidationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["activate", "merge", "supersede", "keep_separate", "reject"]
    target_memory_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_target(self) -> "MemoryConsolidationProposal":
        requires_target = self.action in {"merge", "supersede"}
        if requires_target != (self.target_memory_id is not None):
            raise ValueError("target_memory_id is required only for merge or supersede")
        return self
