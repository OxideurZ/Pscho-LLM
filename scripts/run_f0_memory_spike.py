#!/usr/bin/env python3
"""Run one isolated Milestone F0 cache case against the real llama-server."""

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.llm import LLMMessage, LLMRole
from backend.app.llm.llama_cpp import LlamaCppBackend
from backend.app.memory import MemoryExtractionResult
from benchmark.scripts.run_incremental import direct_llama_metrics

CHAT_MESSAGES = [
    {
        "role": "user",
        "content": (
            "Je prépare un trail local et je préfère comprendre le mécanisme de mon entraînement. "
            "Donne-moi une première piste concise."
        ),
    }
]
NEXT_USER_MESSAGE = {
    "role": "user",
    "content": "En gardant ce fil, quel point devrais-je observer pendant ma prochaine sortie ?",
}
MEMORY_MESSAGE_ID = "f0-user-001"
MEMORY_TEXT = "Je prépare un trail local et je préfère comprendre le mécanisme de mon entraînement."


def request_chat(
    client: httpx.Client,
    settings: Settings,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": settings.model_name,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": settings.default_temperature,
            "top_p": settings.default_top_p,
            "seed": settings.default_seed,
            "max_tokens": 96,
            "id_slot": 0,
            "cache_prompt": True,
        },
    )
    response.raise_for_status()
    answer, metrics, terminal = direct_llama_metrics(response.text)
    if terminal != "done":
        raise RuntimeError(f"chat request ended with {terminal}")
    return answer, metrics


async def extract_memory(settings: Settings) -> tuple[MemoryExtractionResult, int]:
    prompt = load_prompt(
        REPOSITORY_ROOT,
        settings.memory_extraction_prompt_id,
        settings.memory_extraction_prompt_version,
    )
    payload = {
        "target_message_id": MEMORY_MESSAGE_ID,
        "user_messages": [
            {
                "message_id": MEMORY_MESSAGE_ID,
                "content": MEMORY_TEXT,
                "is_target": True,
            }
        ],
    }
    backend = LlamaCppBackend(settings)
    started = perf_counter()
    result = await backend.generate_structured(
        [
            LLMMessage(role=LLMRole.SYSTEM, content=prompt.content),
            LLMMessage(role=LLMRole.USER, content=json.dumps(payload, ensure_ascii=False)),
        ],
        MemoryExtractionResult,
    )
    elapsed_ms = round((perf_counter() - started) * 1000)
    return result, elapsed_ms


def validate_smoke(result: MemoryExtractionResult) -> None:
    if not result.candidates:
        raise RuntimeError("structured extraction returned no candidate")
    for candidate in result.candidates:
        if not any(source.message_id == MEMORY_MESSAGE_ID for source in candidate.source_spans):
            raise RuntimeError("candidate does not include the target message")
        for source in candidate.source_spans:
            if source.message_id != MEMORY_MESSAGE_ID:
                raise RuntimeError("F0 smoke referenced an unknown message")
            if MEMORY_TEXT[source.start_char : source.end_char] != source.quote:
                matches = [
                    index
                    for index in range(len(MEMORY_TEXT))
                    if MEMORY_TEXT.startswith(source.quote, index)
                ]
                raise RuntimeError(
                    "F0 smoke returned a mismatched source span "
                    f"(reported={source.start_char}:{source.end_char}, "
                    f"quote_length={len(source.quote)}, exact_matches={matches})"
                )


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("chat-baseline", "memory-between-turns"), required=True)
    parser.add_argument("--llama-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "benchmark" / "results" / "milestone-f0.jsonl",
    )
    args = parser.parse_args()

    settings = Settings(llama_server_url=args.llama_url)
    conversation_prompt = load_prompt(REPOSITORY_ROOT, settings.prompt_id, settings.prompt_version)
    extraction_prompt = load_prompt(
        REPOSITORY_ROOT,
        settings.memory_extraction_prompt_id,
        settings.memory_extraction_prompt_version,
    )
    with httpx.Client(base_url=args.llama_url, timeout=600) as client:
        health = client.get("/health")
        health.raise_for_status()
        first_answer, first_metrics = request_chat(
            client, settings, conversation_prompt.content, list(CHAT_MESSAGES)
        )
        extraction: MemoryExtractionResult | None = None
        extraction_ms: int | None = None
        if args.case == "memory-between-turns":
            extraction, extraction_ms = asyncio.run(extract_memory(settings))
            validate_smoke(extraction)
        second_messages = [
            *CHAT_MESSAGES,
            {"role": "assistant", "content": first_answer},
            NEXT_USER_MESSAGE,
        ]
        _, second_metrics = request_chat(
            client, settings, conversation_prompt.content, second_messages
        )

    result = {
        "date": datetime.now(UTC).isoformat(),
        "case": args.case,
        "app_commit": git_commit(),
        "model": settings.model_name,
        "model_sha256": settings.model_expected_sha256,
        "llama_cpp_version": settings.llama_cpp_version,
        "llama_cpp_build": settings.llama_cpp_build,
        "conversation_prompt": {
            "id": conversation_prompt.id,
            "version": conversation_prompt.version,
            "sha256": conversation_prompt.sha256,
        },
        "memory_prompt": {
            "id": extraction_prompt.id,
            "version": extraction_prompt.version,
            "sha256": extraction_prompt.sha256,
        },
        "first_chat": first_metrics,
        "second_chat": second_metrics,
        "memory_extraction_ms": extraction_ms,
        "memory_candidate_count": len(extraction.candidates) if extraction else 0,
        "memory_candidates": (
            [
                {
                    "kind": candidate.kind,
                    "epistemic_status": candidate.epistemic_status,
                    "source_span_count": len(candidate.source_spans),
                }
                for candidate in extraction.candidates
            ]
            if extraction
            else []
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
