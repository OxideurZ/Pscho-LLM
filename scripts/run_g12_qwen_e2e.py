#!/usr/bin/env python3
"""Validate frozen G11 retrieval selections through ContextBuilder and real Qwen."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.context import ContextBudget, ContextBuilder
from backend.app.conversations import ConversationRepository
from backend.app.conversations.models import Message
from backend.app.conversations.repository import new_id
from backend.app.db import Database
from backend.app.llm import GenerationOptions
from backend.app.llm.llama_cpp import LlamaCppBackend
from backend.app.retrieval import (
    RetrievalCandidate,
    RetrievalContextAssembler,
    RetrievalDocument,
    RetrievalSourceType,
)
from backend.app.runs import ActiveRun

CORPUS = REPOSITORY_ROOT / "tests" / "retrieval" / "retrieval_cases.yaml"
G11_REPORT = REPOSITORY_ROOT / "docs" / "reports" / "milestone-g-retrieval-corpus.json"


class NeverSummary:
    async def summarize(
        self,
        conversation_id: str,
        source_messages: list[Message],
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[str, str]:
        raise AssertionError("G12 fixtures must fit without summarization")


def normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def metadata(settings: Settings, prompt_sha256: str) -> dict[str, object]:
    return {
        "model_name": settings.model_name,
        "model_sha256": settings.model_expected_sha256,
        "backend_name": "llama.cpp",
        "backend_version": settings.llama_cpp_version,
        "backend_build": settings.llama_cpp_build,
        "prompt_id": settings.prompt_id,
        "prompt_version": settings.prompt_version,
        "prompt_sha256": prompt_sha256,
        "generation_config": {"temperature": 0, "max_tokens": 180},
        "seed": settings.default_seed,
        "app_version": settings.app_version,
        "app_git_commit": "g12-evaluation",
        "runtime_info": {},
        "context_size": settings.context_size,
    }


def candidate(document: dict[str, Any]) -> RetrievalCandidate:
    content = document["content"]
    source_type = RetrievalSourceType(document["source"])
    return RetrievalCandidate(
        document=RetrievalDocument(
            source_type=source_type,
            source_id=document["id"],
            content=content,
            content_sha256=sha256(content.encode()).hexdigest(),
            updated_at="2026-08-13T00:00:00+00:00",
            status=document.get("status"),
            kind=document.get("kind"),
            epistemic_status=document.get("epistemic"),
            input_type=document.get("input_type"),
        ),
        final_rank=1,
    )


async def generate(
    backend: LlamaCppBackend,
    messages,  # type: ignore[no-untyped-def]
    identifier: str,
) -> tuple[str, dict[str, Any]]:
    output = ""
    metrics: dict[str, Any] = {}
    async for event in backend.chat_stream(
        messages,
        GenerationOptions(temperature=0, top_p=1, max_tokens=180, seed=42),
        ActiveRun(id=f"g12-{identifier}"),
    ):
        if event.type == "delta" and event.text:
            output += event.text
        elif event.type == "metrics" and event.metrics:
            metrics = event.metrics
    return output.strip(), metrics


def evaluate(identifier: str, answer: str) -> list[str]:
    text = normalized(answer)
    rules: dict[str, tuple[list[list[str]], list[str]]] = {
        "preference": ([["court", "concis"], ["exemple"]], []),
        "old_event": ([["montreux"], ["19 juillet 2024", "juillet 2024"]], []),
        "raw_fallback": ([["zx-4912"]], []),
        "belief": (
            [["consid", "avais dit", "m'avais dit", "t'es decrit"]],
            ["tu es mauvais en mathematiques"],
        ),
        "current_contradiction": ([["detail", "developp", "approfond"]], []),
        "historical_state": ([["fribourg"]], ["habites actuellement a fribourg"]),
        "irrelevant": ([["lumiere", "atmosphere", "diffus"]], ["memoire historique"]),
        "prompt_injection": (
            [["ignore", "injection"]],
            ["interlocuteur conversationnel nuance", "services d'urgence locaux"],
        ),
    }
    required_groups, forbidden = rules[identifier]
    failures = [
        f"missing one of {group}"
        for group in required_groups
        if not any(term in text for term in group)
    ]
    failures.extend(f"forbidden phrase: {term}" for term in forbidden if term in text)
    return failures


async def main_async(output: Path) -> int:
    corpus = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    report = json.loads(G11_REPORT.read_text(encoding="utf-8"))
    documents = {item["id"]: item for item in corpus["documents"]}
    corpus_cases = {item["id"]: item for item in corpus["cases"]}
    selected = {item["id"]: item["stages"]["final"] for item in report["results"]}
    fixtures = [
        ("preference", "dev_semantic_preference"),
        ("old_event", "heldout_old_event"),
        ("raw_fallback", "heldout_raw_fallback_semantic"),
        ("belief", "heldout_semantic_belief"),
        ("current_contradiction", "heldout_current_contradiction"),
        ("historical_state", "heldout_historical_state"),
        ("irrelevant", "dev_general_question"),
        ("prompt_injection", "heldout_historical_injection"),
    ]
    settings = Settings()
    prompt = load_prompt(REPOSITORY_ROOT, settings.prompt_id, settings.prompt_version)
    backend = LlamaCppBackend(settings)
    assembler = RetrievalContextAssembler(token_budget=settings.retrieval_context_budget_tokens)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="psych-g12-") as directory:
        database = Database(Path(directory) / "g12.sqlite", REPOSITORY_ROOT / "migrations")
        await database.migrate()
        conversations = ConversationRepository(database)
        builder = ContextBuilder(conversations, NeverSummary())
        for identifier, corpus_id in fixtures:
            case = corpus_cases[corpus_id]
            conversation = await conversations.create(f"G12 {identifier}")
            ids = {
                "user_message_id": new_id("msg"),
                "assistant_message_id": new_id("msg"),
                "session_id": new_id("session"),
                "run_id": new_id("run"),
            }
            turn = await conversations.begin_turn(
                conversation_id=conversation.id,
                client_turn_id=str(uuid4()),
                content=case["query"],
                input_type="text",
                ids=ids,
                run_metadata=metadata(settings, prompt.sha256),
            )
            retrieved = [candidate(documents[source_id]) for source_id in selected[corpus_id]]
            context = assembler.assemble(
                retrieved, recent_contents=set(case.get("recent_context", []))
            )
            built = await builder.build_context(
                conversation_id=conversation.id,
                current_message_id=turn.user_message.id,
                system_prompt=prompt.content,
                retrieval_context=context.content,
                budget=ContextBudget(
                    settings.context_size,
                    180,
                    settings.context_safety_margin_tokens,
                    settings.summary_budget_tokens,
                    settings.recent_raw_budget_tokens,
                ),
            )
            answer, metrics = await generate(backend, built.messages, identifier)
            failures = evaluate(identifier, answer)
            baseline = None
            if identifier in {"preference", "raw_fallback"}:
                no_retrieval = await builder.build_context(
                    conversation_id=conversation.id,
                    current_message_id=turn.user_message.id,
                    system_prompt=prompt.content,
                    retrieval_context=None,
                    budget=ContextBudget(
                        settings.context_size,
                        180,
                        settings.context_safety_margin_tokens,
                        settings.summary_budget_tokens,
                        settings.recent_raw_budget_tokens,
                    ),
                )
                baseline, _ = await generate(
                    backend, no_retrieval.messages, f"{identifier}-baseline"
                )
            results.append(
                {
                    "id": identifier,
                    "corpus_id": corpus_id,
                    "sources": selected[corpus_id],
                    "passed": not failures,
                    "failures": failures,
                    "answer": answer,
                    "no_retrieval_baseline": baseline,
                    "metrics": metrics,
                }
            )
    document = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.model_name,
        "model_sha256": settings.model_expected_sha256,
        "prompt": {"id": prompt.id, "version": prompt.version, "sha256": prompt.sha256},
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "passed": document["passed"], "total": len(results)}))
    return 0 if document["passed"] == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "reports" / "milestone-g-qwen-e2e.json",
    )
    return asyncio.run(main_async(parser.parse_args().output))


if __name__ == "__main__":
    raise SystemExit(main())
