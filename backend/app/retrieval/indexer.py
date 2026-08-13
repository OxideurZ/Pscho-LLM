from __future__ import annotations

import asyncio
from hashlib import sha256

from backend.app.jobs import JobKind, JobRecord
from backend.app.retrieval.interfaces import Embedder
from backend.app.retrieval.models import RetrievalDocument, RetrievalSourceType, VectorRecord
from backend.app.retrieval.repository import RetrievalSourceRepository
from backend.app.retrieval.store import SqlCipherLexicalIndex, SqlCipherVectorStore


class RetrievalIndexError(RuntimeError):
    pass


class RetrievalIndexer:
    def __init__(
        self,
        sources: RetrievalSourceRepository,
        lexical_index: SqlCipherLexicalIndex,
        vector_store: SqlCipherVectorStore,
        embedder: Embedder,
        *,
        instruction_version: str,
        dimensions: int,
        dtype: str,
    ) -> None:
        self.sources = sources
        self.lexical_index = lexical_index
        self.vector_store = vector_store
        self.embedder = embedder
        self.instruction_version = instruction_version
        self.dimensions = dimensions
        self.dtype = dtype
        info = embedder.model_info()
        profile = "\n".join(
            (
                info.name,
                info.revision,
                instruction_version,
                str(dimensions),
                dtype,
            )
        )
        self.configuration_sha256 = sha256(profile.encode("utf-8")).hexdigest()

    async def run(self, job: JobRecord, cancel_event: asyncio.Event) -> None:
        if job.kind is JobKind.RETRIEVAL_REINDEX:
            await self.rebuild(cancel_event)
            return
        if job.kind not in {
            JobKind.RETRIEVAL_INDEX_MESSAGE,
            JobKind.RETRIEVAL_INDEX_MEMORY,
        }:
            raise RetrievalIndexError(f"Unsupported retrieval index job: {job.kind}")
        if job.source_type is None or job.source_id is None:
            raise RetrievalIndexError("Retrieval index job has no source reference")
        source_type = RetrievalSourceType(job.source_type)
        document = await self.sources.get(source_type, job.source_id)
        if cancel_event.is_set():
            raise asyncio.CancelledError
        if document is None:
            await self.lexical_index.delete(source_type.value, job.source_id)
            await self.vector_store.delete(source_type.value, job.source_id)
            return
        vectors = await self.embedder.embed_documents([document.content])
        if cancel_event.is_set():
            raise asyncio.CancelledError
        vector_record = self._vector_record(document, vectors)
        await self.lexical_index.upsert([document])
        await self.vector_store.upsert([vector_record])

    async def rebuild(self, cancel_event: asyncio.Event) -> None:
        documents = await self.sources.all_eligible()
        if cancel_event.is_set():
            raise asyncio.CancelledError
        vectors = (
            await self.embedder.embed_documents([document.content for document in documents])
            if documents
            else []
        )
        if cancel_event.is_set():
            raise asyncio.CancelledError
        records = [
            self._vector_record(document, [vector])
            for document, vector in zip(documents, vectors, strict=True)
        ]
        await self.lexical_index.rebuild(documents)
        await self.vector_store.rebuild(records)

    def _vector_record(
        self, document: RetrievalDocument, vectors: list[list[float]]
    ) -> VectorRecord:
        if len(vectors) != 1:
            raise RetrievalIndexError("Embedder returned an unexpected document count")
        vector = vectors[0]
        if len(vector) != self.dimensions:
            raise RetrievalIndexError("Embedder returned an unexpected vector dimension")
        info = self.embedder.model_info()
        return VectorRecord(
            source_type=document.source_type,
            source_id=document.source_id,
            content_sha256=document.content_sha256,
            embedding_model=info.name,
            embedding_revision=info.revision,
            instruction_version=self.instruction_version,
            configuration_sha256=self.configuration_sha256,
            dimensions=self.dimensions,
            dtype=self.dtype,
            vector=vector,
        )
