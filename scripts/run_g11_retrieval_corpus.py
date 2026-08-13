#!/usr/bin/env python3
"""Run the frozen Milestone G retrieval corpus once with the pinned local models."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import yaml

from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.db import Database
from backend.app.retrieval import (
    RetrievalCandidate,
    RetrievalContextAssembler,
    RetrievalQuery,
    RetrievalRuntime,
    RetrievalSourceType,
    ScoredSource,
    reciprocal_rank_fusion,
)

CORPUS = REPOSITORY_ROOT / "tests" / "retrieval" / "retrieval_cases.yaml"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


async def seed(database: Database, documents: list[dict[str, Any]]) -> None:
    now = datetime.now(UTC).isoformat()
    raw = [item for item in documents if item["source"] == "raw_user"]
    async with database.connect() as connection:
        await connection.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("eval-conversation", "Corpus longitudinal G", now, now),
        )
        sequence = 1
        for item in documents:
            if item["source"] == "memory":
                await connection.execute(
                    """
                    INSERT INTO memory_items(
                        id, kind, status, content, epistemic_status, observed_at,
                        last_supported_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["kind"],
                        item["status"],
                        item["content"],
                        item["epistemic"],
                        now,
                        now,
                        now,
                        now,
                    ),
                )
        for item in raw:
            await connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, sequence_no, role, content, input_type,
                    status, created_at, updated_at
                ) VALUES (?, 'eval-conversation', ?, 'user', ?, ?, 'complete', ?, ?)
                """,
                (item["id"], sequence, item["content"], item.get("input_type", "text"), now, now),
            )
            sequence += 1
        await connection.commit()


def ranked_ids(items: list[ScoredSource] | list[RetrievalCandidate]) -> list[str]:
    return [
        item.source_id if isinstance(item, ScoredSource) else item.document.source_id
        for item in items
    ]


def stage_metrics(cases: list[dict[str, Any]], results: dict[str, list[str]]) -> dict[str, Any]:
    recall: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcg: list[float] = []
    injected_relevant = 0
    injected_total = 0
    forbidden = 0
    zero_correct = 0
    zero_total = 0
    hard_failures: list[str] = []
    for case in cases:
        ids = results[case["id"]]
        wanted = set(case.get("must_retrieve", []))
        blocked = set(case.get("must_not_retrieve", []))
        if wanted:
            hits = [index + 1 for index, source_id in enumerate(ids) if source_id in wanted]
            recall.append(len(set(ids) & wanted) / len(wanted))
            reciprocal_ranks.append(1 / min(hits) if hits else 0.0)
            dcg = sum(1 / math.log2(rank + 1) for rank in hits)
            ideal = sum(1 / math.log2(rank + 1) for rank in range(1, len(wanted) + 1))
            ndcg.append(dcg / ideal)
        relevant = wanted
        injected_relevant += len(set(ids) & relevant)
        injected_total += len(ids)
        violations = set(ids) & blocked
        forbidden += len(violations)
        if case.get("expected_zero"):
            zero_total += 1
            zero_correct += int(not ids)
        if case.get("hard") and (
            wanted - set(ids) or violations or (case.get("expected_zero") and ids)
        ):
            hard_failures.append(case["id"])
    return {
        "recall_at_k": statistics.fmean(recall) if recall else 1.0,
        "mrr": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 1.0,
        "ndcg_at_k": statistics.fmean(ndcg) if ndcg else 1.0,
        "precision_at_injected": injected_relevant / injected_total if injected_total else 1.0,
        "forbidden_injection_count": forbidden,
        "no_retrieval_correct": zero_correct,
        "no_retrieval_total": zero_total,
        "hard_failures": hard_failures,
    }


async def evaluate_case(
    runtime: RetrievalRuntime,
    assembler: RetrievalContextAssembler,
    case: dict[str, Any],
) -> tuple[dict[str, list[str]], list[RetrievalCandidate], dict[str, float]]:
    query = RetrievalQuery(
        current_user_text=case["query"],
        current_message_id=f"query:{case['id']}",
        conversation_id="eval-current",
        recent_context=case.get("recent_context", []),
        timestamp=datetime.now(UTC).isoformat(),
    )
    timings: dict[str, float] = {}
    started = monotonic()
    query_vector = await runtime.embedder.embed_query(query.current_user_text)
    timings["embedding_ms"] = (monotonic() - started) * 1000

    lexical_lists: list[list[ScoredSource]] = []
    dense_lists: list[list[ScoredSource]] = []
    started = monotonic()
    for source_type in RetrievalSourceType:
        lexical_lists.append(
            await runtime.service.lexical.retrieve(
                query, source_type, top_k=runtime.profile.lexical_top_k
            )
        )
    timings["fts_ms"] = (monotonic() - started) * 1000
    started = monotonic()
    for source_type in RetrievalSourceType:
        dense_lists.append(
            await runtime.service.hybrid.dense.retrieve(
                query,
                source_type,
                top_k=runtime.profile.dense_top_k,
                vector=query_vector,
            )
        )
    timings["vector_ms"] = (monotonic() - started) * 1000

    ordered_lists = [lexical_lists[0], dense_lists[0], lexical_lists[1], dense_lists[1]]
    fused = reciprocal_rank_fusion(
        ordered_lists,
        constant=runtime.profile.rrf_constant,
        top_k=runtime.profile.rrf_top_k,
    )
    candidates: list[RetrievalCandidate] = []
    for rank, (source_type, source_id, score, lexical_rank, dense_rank) in enumerate(fused, 1):
        document = await runtime.service.sources.get(source_type, source_id)
        if document is not None:
            candidates.append(
                RetrievalCandidate(
                    document=document,
                    lexical_rank=lexical_rank,
                    dense_rank=dense_rank,
                    fused_rank=rank,
                    fused_score=score,
                )
            )
    started = monotonic()
    reranked = await runtime.service.selector.select(query, candidates)
    timings["reranker_ms"] = (monotonic() - started) * 1000
    timings["total_ms"] = sum(timings.values())
    lexical = reciprocal_rank_fusion(
        [lexical_lists[0], [], lexical_lists[1], []],
        constant=runtime.profile.rrf_constant,
        top_k=runtime.profile.rrf_top_k,
    )
    dense = reciprocal_rank_fusion(
        [[], dense_lists[0], [], dense_lists[1]],
        constant=runtime.profile.rrf_constant,
        top_k=runtime.profile.rrf_top_k,
    )
    return (
        {
            "lexical": [item[1] for item in lexical],
            "dense": [item[1] for item in dense],
            "rrf": [item.document.source_id for item in candidates],
            "reranked": ranked_ids(reranked),
        },
        reranked,
        timings,
    )


def choose_threshold(
    cases: list[dict[str, Any]], raw: dict[str, list[RetrievalCandidate]]
) -> tuple[float, float]:
    positive_scores = [
        float(item.reranker_score or 0)
        for case in cases
        for item in raw[case["id"]]
        if item.document.source_id in set(case.get("must_retrieve", []))
    ]
    negative_scores = [
        float(item.reranker_score or 0)
        for case in cases
        for item in raw[case["id"]]
        if item.document.source_id not in set(case.get("must_retrieve", []))
    ]
    if not positive_scores:
        raise RuntimeError("Development corpus has no positive reranker score")
    negative_ceiling = max(negative_scores, default=0.0)
    positive_floor = min(positive_scores)
    if negative_ceiling >= positive_floor:
        raise RuntimeError("Development reranker scores have no safe separating margin")
    threshold = round((negative_ceiling + positive_floor) / 2, 6)
    agreement_floor = 0.0
    return threshold, agreement_floor


async def main_async(output: Path) -> int:
    corpus = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="psych-g11-") as directory:
        database = Database(
            Path(directory) / "evaluation.sqlite",
            REPOSITORY_ROOT / "migrations",
            encryption_key=os.urandom(32),
        )
        await database.migrate()
        await seed(database, corpus["documents"])
        runtime = RetrievalRuntime(
            Settings(
                security_enabled=False,
                retrieval_minimum_reranker_score=None,
                retrieval_agreement_score_floor=None,
            ),
            database,
        )
        await runtime.indexer.rebuild(asyncio.Event())
        assembler = runtime.context_assembler
        stage_results: dict[str, dict[str, list[str]]] = {
            name: {} for name in ("lexical", "dense", "rrf", "reranked", "final")
        }
        raw_reranked: dict[str, list[RetrievalCandidate]] = {}
        timings: dict[str, list[float]] = {
            name: [] for name in ("fts_ms", "embedding_ms", "vector_ms", "reranker_ms", "total_ms")
        }
        try:
            for case in corpus["cases"]:
                stages, reranked, case_timings = await evaluate_case(runtime, assembler, case)
                for name in ("lexical", "dense", "rrf"):
                    stage_results[name][case["id"]] = stages[name]
                raw_reranked[case["id"]] = reranked
                stage_results["reranked"][case["id"]] = stages["reranked"]
                for name, value in case_timings.items():
                    timings[name].append(value)
            development = [case for case in corpus["cases"] if case["split"] == "development"]
            threshold, agreement_floor = choose_threshold(development, raw_reranked)
            for case in corpus["cases"]:
                filtered = [
                    item
                    for index, item in enumerate(raw_reranked[case["id"]])
                    if (item.reranker_score or 0) >= threshold
                    or (
                        index < 2
                        and agreement_floor is not None
                        and (
                            " et " in case["query"].casefold()
                            or "ainsi que" in case["query"].casefold()
                        )
                        and item.lexical_rank is not None
                        and item.dense_rank is not None
                        and (item.reranker_score or 0) >= agreement_floor
                    )
                ]
                assembled = assembler.assemble(
                    filtered,
                    recent_contents=set(case.get("recent_context", [])),
                )
                stage_results["final"][case["id"]] = ranked_ids(assembled.items)
        finally:
            await runtime.close()

    development = [case for case in corpus["cases"] if case["split"] == "development"]
    heldout = [case for case in corpus["cases"] if case["split"] == "heldout"]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_schema": corpus["schema_version"],
        "development_cases": len(development),
        "heldout_cases": len(heldout),
        "frozen_threshold": threshold,
        "frozen_agreement_score_floor": agreement_floor,
        "metrics": {
            split: {stage: stage_metrics(cases, stage_results[stage]) for stage in stage_results}
            for split, cases in (("development", development), ("heldout", heldout))
        },
        "latency_ms": {
            name: {"p50": statistics.median(values), "p95": percentile(values, 0.95)}
            for name, values in timings.items()
        },
        "results": [
            {
                "id": case["id"],
                "split": case["split"],
                "reranker_scores": [
                    {"source_id": item.document.source_id, "score": item.reranker_score}
                    for item in raw_reranked[case["id"]]
                ],
                "stages": {stage: values[case["id"]] for stage, values in stage_results.items()},
            }
            for case in corpus["cases"]
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    heldout_final = report["metrics"]["heldout"]["final"]
    print(
        json.dumps(
            {
                "output": str(output),
                "frozen_threshold": threshold,
                "frozen_agreement_score_floor": agreement_floor,
                "heldout_final": heldout_final,
            },
            ensure_ascii=False,
        )
    )
    return int(bool(heldout_final["hard_failures"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "reports" / "milestone-g-retrieval-corpus.json",
    )
    return asyncio.run(main_async(parser.parse_args().output))


if __name__ == "__main__":
    raise SystemExit(main())
