from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path

import pytest

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.retrieval import (
    ComponentHealth,
    ComponentStatus,
    RerankedSource,
    Reranker,
    RerankInput,
    RetrievalCandidate,
    RetrievalDocument,
    RetrievalModelInfo,
    RetrievalProfile,
    RetrievalQuery,
    RetrievalSelector,
    RetrievalService,
    RetrievalSourceType,
    ScoredSource,
    SqlCipherLexicalIndex,
    reciprocal_rank_fusion,
)


class ScoreReranker(Reranker):
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.candidate_counts: list[int] = []

    async def rerank(
        self, query: str, candidates: list[RerankInput], *, top_k: int
    ) -> list[RerankedSource]:
        self.candidate_counts.append(len(candidates))
        ordered = sorted(
            candidates,
            key=lambda item: (-self.scores.get(item.source_id, 0.0), item.source_id),
        )[:top_k]
        return [
            RerankedSource(
                source_type=item.source_type,
                source_id=item.source_id,
                score=self.scores.get(item.source_id, 0.0),
                rank=rank,
            )
            for rank, item in enumerate(ordered, start=1)
        ]

    async def health(self) -> ComponentHealth:
        return ComponentHealth(status=ComponentStatus.HEALTHY)

    def model_info(self) -> RetrievalModelInfo:
        return RetrievalModelInfo(
            name="fake-reranker",
            revision="one",
            model_sha256="a" * 64,
            runtime="test",
            device="cpu",
            dtype="float32",
        )


class FailingReranker(ScoreReranker):
    async def rerank(
        self, query: str, candidates: list[RerankInput], *, top_k: int
    ) -> list[RerankedSource]:
        raise RuntimeError("reranker unavailable")


class StubHybrid:
    def __init__(
        self,
        retrieval_profile: RetrievalProfile,
        result: list[RetrievalCandidate] | Exception,
        *,
        delay: float = 0,
    ) -> None:
        self.profile = retrieval_profile
        self.result = result
        self.delay = delay

    async def retrieve(self, _query: RetrievalQuery) -> list[RetrievalCandidate]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class UnusedLexical:
    async def retrieve(self, *args, **kwargs):
        raise RuntimeError("lexical unavailable")


class UnusedSources:
    async def get(self, *args, **kwargs):
        raise AssertionError("source lookup should not run")


def source(
    source_type: RetrievalSourceType,
    source_id: str,
    content: str,
    *,
    status: str | None = None,
) -> RetrievalDocument:
    return RetrievalDocument(
        source_type=source_type,
        source_id=source_id,
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        updated_at="2026-08-13T00:00:00+00:00",
        status=status,
    )


def candidate(document: RetrievalDocument, fused_rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        document=document,
        fused_rank=fused_rank,
        fused_score=1 / (60 + fused_rank),
    )


def query(text: str = "Qu'est-ce qui m'aide à apprendre ?") -> RetrievalQuery:
    return RetrievalQuery(
        current_user_text=text,
        current_message_id="current-message",
        conversation_id="current-conversation",
        timestamp="2026-08-13T00:00:00+00:00",
    )


def profile(*, threshold: float | None = 0.2, max_items: int = 3) -> RetrievalProfile:
    return RetrievalProfile(
        lexical_top_k=8,
        dense_top_k=8,
        rrf_top_k=8,
        rerank_top_k=4,
        max_items=max_items,
        minimum_reranker_score=threshold,
    )


def test_rrf_is_equal_weight_complementary_and_deterministic() -> None:
    lexical_memory = [
        ScoredSource(
            source_type=RetrievalSourceType.MEMORY,
            source_id="exact-name",
            score=10,
            rank=1,
        )
    ]
    dense_memory = [
        ScoredSource(
            source_type=RetrievalSourceType.MEMORY,
            source_id="semantic",
            score=0.9,
            rank=1,
        ),
        ScoredSource(
            source_type=RetrievalSourceType.MEMORY,
            source_id="exact-name",
            score=0.8,
            rank=2,
        ),
    ]

    first = reciprocal_rank_fusion([lexical_memory, dense_memory, [], []], constant=60, top_k=8)
    second = reciprocal_rank_fusion([lexical_memory, dense_memory, [], []], constant=60, top_k=8)

    assert first == second
    assert [item[1] for item in first] == ["exact-name", "semantic"]
    assert first[0][3:] == (1, 2)
    assert first[1][3:] == (None, 1)


@pytest.mark.asyncio
async def test_lexical_query_finds_rare_name_number_and_accent(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    index = SqlCipherLexicalIndex(database)
    documents = [
        source(
            RetrievalSourceType.RAW_USER,
            "rare",
            "J'ai rencontré Éléonore au dossier ZX-4815 le 12 août.",
        ),
        source(RetrievalSourceType.RAW_USER, "noise", "J'ai acheté du pain."),
    ]
    await index.upsert(documents)

    results = await index.query(
        "Quel était le dossier ZX-4815 d'Éléonore ?",
        RetrievalSourceType.RAW_USER,
        top_k=5,
    )

    assert results[0].source_id == "rare"


@pytest.mark.asyncio
async def test_selector_is_bounded_allows_zero_and_prefers_structured_duplicate() -> None:
    content = "Je préfère les explications qui montrent les mécanismes."
    candidates = [
        candidate(source(RetrievalSourceType.RAW_USER, "raw", content), 1),
        candidate(source(RetrievalSourceType.MEMORY, "memory", content, status="active"), 2),
        candidate(source(RetrievalSourceType.RAW_USER, "irrelevant", "Pain acheté hier"), 3),
        candidate(source(RetrievalSourceType.RAW_USER, "extra", "Autre détail"), 4),
        candidate(source(RetrievalSourceType.RAW_USER, "not-reranked", "Hors borne"), 5),
    ]
    reranker = ScoreReranker({"raw": 0.95, "memory": 0.94, "irrelevant": 0.01, "extra": 0.005})
    selector = RetrievalSelector(reranker, profile())

    selected = await selector.select(query(), candidates)
    zero = await RetrievalSelector(ScoreReranker({"irrelevant": 0.01}), profile()).select(
        query("Bonjour"), [candidates[2]]
    )

    assert reranker.candidate_counts == [4]
    assert [(item.document.source_id, item.final_rank) for item in selected] == [("memory", 1)]
    assert zero == []


@pytest.mark.asyncio
async def test_superseded_is_historical_only() -> None:
    active = candidate(
        source(RetrievalSourceType.MEMORY, "lausanne", "J'habite à Lausanne", status="active"),
        1,
    )
    superseded = candidate(
        source(RetrievalSourceType.MEMORY, "sion", "J'habitais à Sion", status="superseded"),
        2,
    )
    reranker = ScoreReranker({"sion": 0.99, "lausanne": 0.9})
    selector = RetrievalSelector(reranker, profile())

    current = await selector.select(query("Où est-ce que j'habite ?"), [active, superseded])
    historical = await selector.select(
        query("Où est-ce que j'habitais avant Lausanne ?"), [active, superseded]
    )

    assert [item.document.source_id for item in current] == ["lausanne"]
    assert [item.document.source_id for item in historical] == ["sion", "lausanne"]


@pytest.mark.asyncio
async def test_service_timeout_and_full_failure_return_valid_no_retrieval() -> None:
    retrieval_profile = profile()
    selector = RetrievalSelector(ScoreReranker({}), retrieval_profile)
    timed_out = RetrievalService(
        StubHybrid(retrieval_profile, [], delay=0.05),  # type: ignore[arg-type]
        UnusedLexical(),  # type: ignore[arg-type]
        selector,
        UnusedSources(),  # type: ignore[arg-type]
        timeout_ms=5,
    )
    unavailable = RetrievalService(
        StubHybrid(retrieval_profile, RuntimeError("dense unavailable")),  # type: ignore[arg-type]
        UnusedLexical(),  # type: ignore[arg-type]
        selector,
        UnusedSources(),  # type: ignore[arg-type]
        timeout_ms=100,
    )

    timeout_result = await timed_out.retrieve(query())
    unavailable_result = await unavailable.retrieve(query())

    assert timeout_result.items == []
    assert timeout_result.error_codes == ["RETRIEVAL_TIMEOUT"]
    assert unavailable_result.items == []
    assert unavailable_result.error_codes == ["RETRIEVAL_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_reranker_failure_keeps_only_hybrid_agreement() -> None:
    agreed = candidate(source(RetrievalSourceType.MEMORY, "agreed", "Contexte utile"), 1)
    agreed = agreed.model_copy(update={"lexical_rank": 1, "dense_rank": 2})
    dense_only = candidate(source(RetrievalSourceType.RAW_USER, "dense", "Détail"), 2)
    dense_only = dense_only.model_copy(update={"dense_rank": 1})
    retrieval_profile = profile()
    selector = RetrievalSelector(FailingReranker({}), retrieval_profile)
    service = RetrievalService(
        StubHybrid(retrieval_profile, [agreed, dense_only]),  # type: ignore[arg-type]
        UnusedLexical(),  # type: ignore[arg-type]
        selector,
        UnusedSources(),  # type: ignore[arg-type]
        timeout_ms=100,
    )

    result = await service.retrieve(query())

    assert result.mode == "hybrid_without_reranker"
    assert result.degraded is True
    assert result.error_codes == ["RERANKER_UNAVAILABLE"]
    assert [item.document.source_id for item in result.items] == ["agreed"]


@pytest.mark.asyncio
async def test_explicit_current_user_update_excludes_contradicted_history() -> None:
    old = candidate(
        source(
            RetrievalSourceType.MEMORY,
            "old-preference",
            "Je prefere des reponses courtes avec des exemples concrets.",
            status="active",
        ),
        1,
    )
    retrieval_profile = profile()
    selector = RetrievalSelector(ScoreReranker({"old-preference": 0.99}), retrieval_profile)

    selected = await selector.select(
        query("Je ne veux plus de reponses courtes avec des exemples concrets."), [old]
    )

    assert selected == []


@pytest.mark.asyncio
async def test_non_personal_query_short_circuits_to_no_retrieval() -> None:
    noise = candidate(
        source(RetrievalSourceType.MEMORY, "noise", "Le ciel est bleu.", status="active"),
        1,
    )
    reranker = ScoreReranker({"noise": 0.99})

    selected = await RetrievalSelector(reranker, profile()).select(
        query("Pourquoi le ciel est-il bleu ?"), [noise]
    )

    assert selected == []
    assert reranker.candidate_counts == []


@pytest.mark.asyncio
async def test_named_history_lookup_and_bounded_multi_detail_agreement() -> None:
    alex = candidate(
        source(RetrievalSourceType.MEMORY, "alex", "Alex Martin est mon collegue", status="active"),
        1,
    ).model_copy(update={"lexical_rank": 1, "dense_rank": 1})
    detail = candidate(
        source(RetrievalSourceType.RAW_USER, "detail", "Code exact ZX-4912"),
        2,
    ).model_copy(update={"lexical_rank": 1, "dense_rank": 2})
    configured = profile(threshold=0.2).model_copy(update={"agreement_score_floor": 0.01})

    named = await RetrievalSelector(ScoreReranker({"alex": 0.9}), configured).select(
        query("Que sais-tu sur Alex Martin ?"), [alex]
    )
    multi = await RetrievalSelector(ScoreReranker({"detail": 0.02}), configured).select(
        query("Rappelle mon evenement et mon code exact."), [detail]
    )

    assert [item.document.source_id for item in named] == ["alex"]
    assert [item.document.source_id for item in multi] == ["detail"]
