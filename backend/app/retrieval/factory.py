from __future__ import annotations

from hashlib import sha256

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.db import Database
from backend.app.retrieval.audit import RetrievalAuditRepository
from backend.app.retrieval.context import RetrievalContextAssembler
from backend.app.retrieval.indexer import RetrievalIndexer
from backend.app.retrieval.models import RetrievalProfile
from backend.app.retrieval.pipeline import (
    DenseRetriever,
    HybridRetriever,
    LexicalRetriever,
    RetrievalSelector,
)
from backend.app.retrieval.repository import RetrievalSourceRepository
from backend.app.retrieval.runtime import TransformerEmbedder, TransformerReranker
from backend.app.retrieval.service import RetrievalService
from backend.app.retrieval.store import SqlCipherLexicalIndex, SqlCipherVectorStore


class RetrievalRuntime:
    def __init__(self, settings: Settings, database: Database) -> None:
        query_prompt = load_prompt(
            REPOSITORY_ROOT,
            settings.retrieval_query_prompt_id,
            settings.retrieval_query_prompt_version,
        )
        rerank_prompt = load_prompt(
            REPOSITORY_ROOT,
            settings.retrieval_rerank_prompt_id,
            settings.retrieval_rerank_prompt_version,
        )
        self.embedder = TransformerEmbedder(
            settings.embedding_model_path,
            name=settings.embedding_model_name,
            revision=settings.embedding_model_revision,
            expected_sha256=settings.embedding_model_expected_sha256,
            query_instruction=query_prompt.content,
            dimensions=settings.retrieval_embedding_dimensions,
        )
        self.reranker = TransformerReranker(
            settings.reranker_model_path,
            name=settings.reranker_model_name,
            revision=settings.reranker_model_revision,
            expected_sha256=settings.reranker_model_expected_sha256,
            instruction=rerank_prompt.content,
        )
        sources = RetrievalSourceRepository(database)
        lexical = SqlCipherLexicalIndex(database)
        vectors = SqlCipherVectorStore(database)
        self.indexer = RetrievalIndexer(
            sources,
            lexical,
            vectors,
            self.embedder,
            instruction_version=f"{query_prompt.id}:v{query_prompt.version}",
            dimensions=settings.retrieval_embedding_dimensions,
            dtype=settings.retrieval_embedding_dtype,
        )
        profile = RetrievalProfile(
            lexical_top_k=settings.retrieval_lexical_top_k,
            dense_top_k=settings.retrieval_dense_top_k,
            rrf_top_k=settings.retrieval_rrf_top_k,
            rerank_top_k=settings.retrieval_rerank_top_k,
            max_items=settings.retrieval_max_items,
            minimum_reranker_score=settings.retrieval_minimum_reranker_score,
        )
        dense = DenseRetriever(
            self.embedder,
            vectors,
            instruction_version=f"{query_prompt.id}:v{query_prompt.version}",
            configuration_sha256=self.indexer.configuration_sha256,
            dimensions=settings.retrieval_embedding_dimensions,
            dtype=settings.retrieval_embedding_dtype,
        )
        lexical_retriever = LexicalRetriever(lexical)
        hybrid = HybridRetriever(lexical_retriever, dense, sources, profile)
        self.profile = profile
        self.service = RetrievalService(
            hybrid,
            lexical_retriever,
            RetrievalSelector(self.reranker, profile),
            sources,
            timeout_ms=settings.retrieval_interactive_timeout_ms,
        )
        self.context_assembler = RetrievalContextAssembler(
            token_budget=settings.retrieval_context_budget_tokens
        )
        self.audit = RetrievalAuditRepository(database)
        self.configuration_sha256 = sha256(profile.model_dump_json().encode("utf-8")).hexdigest()

    async def close(self) -> None:
        await self.reranker.close()
        await self.embedder.close()
