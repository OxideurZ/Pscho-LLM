from __future__ import annotations

import asyncio

from backend.app.retrieval.models import (
    RetrievalCandidate,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalSourceType,
)
from backend.app.retrieval.pipeline import (
    HybridRetriever,
    LexicalRetriever,
    RetrievalSelector,
    reciprocal_rank_fusion,
)
from backend.app.retrieval.repository import RetrievalSourceRepository


class RetrievalService:
    """Bounded interactive retrieval with conservative degradation to no context."""

    def __init__(
        self,
        hybrid: HybridRetriever,
        lexical: LexicalRetriever,
        selector: RetrievalSelector,
        sources: RetrievalSourceRepository,
        *,
        timeout_ms: int,
    ) -> None:
        self.hybrid = hybrid
        self.lexical = lexical
        self.selector = selector
        self.sources = sources
        self.timeout_ms = timeout_ms

    async def retrieve(self, query: RetrievalQuery) -> RetrievalOutcome:
        try:
            async with asyncio.timeout(self.timeout_ms / 1000):
                return await self._retrieve(query)
        except TimeoutError:
            return RetrievalOutcome(
                mode="none", items=[], degraded=True, error_codes=["RETRIEVAL_TIMEOUT"]
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return RetrievalOutcome(
                mode="none", items=[], degraded=True, error_codes=["RETRIEVAL_UNAVAILABLE"]
            )

    async def _retrieve(self, query: RetrievalQuery) -> RetrievalOutcome:
        errors: list[str] = []
        try:
            candidates = await self.hybrid.retrieve(query)
            mode = "hybrid_reranker"
        except asyncio.CancelledError:
            raise
        except Exception:
            errors.append("HYBRID_UNAVAILABLE")
            candidates = await self._lexical_candidates(query)
            mode = "lexical_reranker"
        if not candidates:
            return RetrievalOutcome(
                mode="none", items=[], degraded=bool(errors), error_codes=errors
            )
        try:
            selected = await self.selector.select(query, candidates)
        except asyncio.CancelledError:
            raise
        except Exception:
            errors.append("RERANKER_UNAVAILABLE")
            selected = self._conservative_without_reranker(candidates)
            mode = mode.replace("_reranker", "_without_reranker")
        return RetrievalOutcome(
            mode=mode,
            items=selected,
            degraded=bool(errors),
            error_codes=errors,
        )

    async def _lexical_candidates(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        lists = [
            await self.lexical.retrieve(query, source_type, top_k=self.hybrid.profile.lexical_top_k)
            for source_type in RetrievalSourceType
        ]
        fused = reciprocal_rank_fusion(
            [lists[0], [], lists[1], []],
            constant=self.hybrid.profile.rrf_constant,
            top_k=self.hybrid.profile.rrf_top_k,
        )
        candidates: list[RetrievalCandidate] = []
        for rank, (source_type, source_id, score, lexical_rank, _dense_rank) in enumerate(
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
                    fused_rank=rank,
                    fused_score=score,
                )
            )
        return candidates

    def _conservative_without_reranker(
        self, candidates: list[RetrievalCandidate]
    ) -> list[RetrievalCandidate]:
        # Agreement between independent lexical and dense lists is safe enough for fallback.
        # Lexical-only fallback stays empty without the reranker: no context beats noisy context.
        agreed = [
            candidate
            for candidate in candidates
            if candidate.lexical_rank is not None and candidate.dense_rank is not None
        ][: self.hybrid.profile.max_items]
        return [
            candidate.model_copy(update={"final_rank": rank})
            for rank, candidate in enumerate(agreed, start=1)
        ]
