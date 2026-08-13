from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from backend.app.retrieval.interfaces import Embedder, Reranker
from backend.app.retrieval.models import (
    RerankInput,
    RetrievalCandidate,
    RetrievalProfile,
    RetrievalQuery,
    RetrievalSourceType,
    ScoredSource,
    VectorQuery,
)
from backend.app.retrieval.repository import RetrievalSourceRepository
from backend.app.retrieval.store import SqlCipherLexicalIndex, SqlCipherVectorStore


class LexicalRetriever:
    def __init__(self, index: SqlCipherLexicalIndex) -> None:
        self.index = index

    async def retrieve(
        self, query: RetrievalQuery, source_type: RetrievalSourceType, *, top_k: int
    ) -> list[ScoredSource]:
        return await self.index.query(query.current_user_text, source_type, top_k=top_k)


class DenseRetriever:
    def __init__(
        self,
        embedder: Embedder,
        vector_store: SqlCipherVectorStore,
        *,
        instruction_version: str,
        configuration_sha256: str,
        dimensions: int,
        dtype: str,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.instruction_version = instruction_version
        self.configuration_sha256 = configuration_sha256
        self.dimensions = dimensions
        self.dtype = dtype

    async def retrieve(
        self,
        query: RetrievalQuery,
        source_type: RetrievalSourceType,
        *,
        top_k: int,
        vector: list[float] | None = None,
    ) -> list[ScoredSource]:
        query_vector = vector or await self.embedder.embed_query(query.current_user_text)
        info = self.embedder.model_info()
        results = await self.vector_store.query(
            VectorQuery(
                embedding_model=info.name,
                embedding_revision=info.revision,
                instruction_version=self.instruction_version,
                configuration_sha256=self.configuration_sha256,
                dimensions=self.dimensions,
                dtype=self.dtype,
                vector=query_vector,
                top_k=top_k,
                source_type=source_type,
            )
        )
        return [result.model_copy(update={"rank": rank}) for rank, result in enumerate(results, 1)]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[ScoredSource]], *, constant: int, top_k: int
) -> list[tuple[RetrievalSourceType, str, float, int | None, int | None]]:
    scores: dict[tuple[RetrievalSourceType, str], float] = defaultdict(float)
    lexical_ranks: dict[tuple[RetrievalSourceType, str], int] = {}
    dense_ranks: dict[tuple[RetrievalSourceType, str], int] = {}
    for list_index, ranked in enumerate(ranked_lists):
        is_lexical = list_index % 2 == 0
        for result in ranked:
            key = (result.source_type, result.source_id)
            scores[key] += 1 / (constant + result.rank)
            ranks = lexical_ranks if is_lexical else dense_ranks
            ranks[key] = min(ranks.get(key, result.rank), result.rank)
    ordered = sorted(scores, key=lambda key: (-scores[key], key[0].value, key[1]))[:top_k]
    return [
        (
            source_type,
            source_id,
            scores[(source_type, source_id)],
            lexical_ranks.get((source_type, source_id)),
            dense_ranks.get((source_type, source_id)),
        )
        for source_type, source_id in ordered
    ]


class HybridRetriever:
    def __init__(
        self,
        lexical: LexicalRetriever,
        dense: DenseRetriever,
        sources: RetrievalSourceRepository,
        profile: RetrievalProfile,
    ) -> None:
        self.lexical = lexical
        self.dense = dense
        self.sources = sources
        self.profile = profile

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        lists: list[list[ScoredSource]] = []
        query_vector = await self.dense.embedder.embed_query(query.current_user_text)
        for source_type in RetrievalSourceType:
            lists.append(
                await self.lexical.retrieve(query, source_type, top_k=self.profile.lexical_top_k)
            )
            lists.append(
                await self.dense.retrieve(
                    query,
                    source_type,
                    top_k=self.profile.dense_top_k,
                    vector=query_vector,
                )
            )
        fused = reciprocal_rank_fusion(
            lists, constant=self.profile.rrf_constant, top_k=self.profile.rrf_top_k
        )
        candidates: list[RetrievalCandidate] = []
        for fused_rank, (source_type, source_id, score, lexical_rank, dense_rank) in enumerate(
            fused, start=1
        ):
            if source_id in query.exclusions:
                continue
            document = await self.sources.get(source_type, source_id)
            if document is None:
                continue
            candidates.append(
                RetrievalCandidate(
                    document=document,
                    lexical_rank=lexical_rank,
                    dense_rank=dense_rank,
                    fused_rank=fused_rank,
                    fused_score=score,
                )
            )
        return candidates


class RetrievalSelector:
    def __init__(self, reranker: Reranker, profile: RetrievalProfile) -> None:
        self.reranker = reranker
        self.profile = profile

    async def select(
        self, query: RetrievalQuery, candidates: Sequence[RetrievalCandidate]
    ) -> list[RetrievalCandidate]:
        if not _has_personal_history_intent(query.current_user_text):
            return []
        contradicted = [
            candidate
            for candidate in candidates
            if _current_user_contradicts(query.current_user_text, candidate)
        ]
        if contradicted:
            return []
        shortlist = list(candidates[: self.profile.rerank_top_k])
        if not shortlist:
            return []
        ranked = await self.reranker.rerank(
            query.current_user_text,
            [
                RerankInput(
                    source_type=candidate.document.source_type,
                    source_id=candidate.document.source_id,
                    content=candidate.document.content,
                )
                for candidate in shortlist
            ],
            top_k=self.profile.rerank_top_k,
        )
        by_key = {
            (candidate.document.source_type, candidate.document.source_id): candidate
            for candidate in shortlist
        }
        reranked = [
            by_key[(item.source_type, item.source_id)].model_copy(
                update={"reranker_score": item.score}
            )
            for item in ranked
            if (item.source_type, item.source_id) in by_key
        ]
        threshold = self.profile.minimum_reranker_score
        if threshold is not None:
            reranked = [
                candidate
                for candidate in reranked
                if candidate.reranker_score is not None
                and (
                    candidate.reranker_score >= threshold
                    or self._safe_multi_detail_override(query, candidate)
                )
            ]
        if not _has_historical_intent(query.current_user_text):
            reranked = [
                candidate for candidate in reranked if candidate.document.status != "superseded"
            ]
        selected: list[RetrievalCandidate] = []
        seen_hashes: dict[str, RetrievalCandidate] = {}
        for candidate in reranked:
            duplicate = seen_hashes.get(candidate.document.content_sha256)
            if duplicate is not None:
                if (
                    candidate.document.source_type is RetrievalSourceType.MEMORY
                    and duplicate.document.source_type is RetrievalSourceType.RAW_USER
                ):
                    selected.remove(duplicate)
                    selected.append(candidate)
                    seen_hashes[candidate.document.content_sha256] = candidate
                continue
            selected.append(candidate)
            seen_hashes[candidate.document.content_sha256] = candidate
            if len(selected) == self.profile.max_items:
                break
        return [
            candidate.model_copy(update={"final_rank": rank})
            for rank, candidate in enumerate(selected, start=1)
        ]

    def _safe_multi_detail_override(
        self, query: RetrievalQuery, candidate: RetrievalCandidate
    ) -> bool:
        floor = self.profile.agreement_score_floor
        normalized = " ".join(query.current_user_text.casefold().split())
        return bool(
            floor is not None
            and (" et " in normalized or "ainsi que" in normalized)
            and candidate.lexical_rank is not None
            and candidate.dense_rank is not None
            and (candidate.reranker_score or 0) >= floor
        )


def _has_historical_intent(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return bool(
        re.search(
            r"\b(avant|autrefois|ancien(?:ne)?|précédemment|auparavant|à l'époque|"
            r"passé|habit(?:ais|ait|ions|iez|aient)|viv(?:ais|ait|ions|iez|aient))\b",
            normalized,
        )
    )


def _current_user_contradicts(text: str, candidate: RetrievalCandidate) -> bool:
    current = " ".join(text.casefold().split())
    if not re.search(r"\b(ne .{0,40} plus|plus maintenant|desormais|j'ai change)\b", current):
        return False
    historical = " ".join(candidate.document.content.casefold().split())
    ignored = {
        "avec",
        "cette",
        "dans",
        "dois",
        "doit",
        "est",
        "pour",
        "plus",
        "quel",
        "quelle",
        "suis",
    }
    current_terms = {term for term in re.findall(r"[^\W_]{4,}", current) if term not in ignored}
    historical_terms = set(re.findall(r"[^\W_]{4,}", historical))
    return len(current_terms & historical_terms) >= 2


def _has_personal_history_intent(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return bool(
        re.search(
            r"(?:\b(?:je|moi|mon|ma|mes|me|mien|mienne|ai-je|suis-je|"
            r"avais-je|etais-je|habite|habitais)\b|\bj'|\bm'|\bque sais-tu sur\b)",
            normalized,
        )
    )
