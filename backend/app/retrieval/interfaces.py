from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from backend.app.retrieval.models import (
    ComponentHealth,
    RerankedSource,
    RerankInput,
    RetrievalModelInfo,
    ScoredSource,
    VectorQuery,
    VectorRecord,
)


class Embedder(ABC):
    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> ComponentHealth:
        raise NotImplementedError

    @abstractmethod
    def model_info(self) -> RetrievalModelInfo:
        raise NotImplementedError


class Reranker(ABC):
    @abstractmethod
    async def rerank(
        self, query: str, candidates: Sequence[RerankInput], *, top_k: int
    ) -> list[RerankedSource]:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> ComponentHealth:
        raise NotImplementedError

    @abstractmethod
    def model_info(self) -> RetrievalModelInfo:
        raise NotImplementedError


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, source_type: str, source_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def query(self, query: VectorQuery) -> list[ScoredSource]:
        raise NotImplementedError

    @abstractmethod
    async def rebuild(self, records: Sequence[VectorRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> ComponentHealth:
        raise NotImplementedError
