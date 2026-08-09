#!/usr/bin/env python3
"""Run the Milestone F extraction corpus against the pinned local model."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.llm import LLMMessage, LLMRole
from backend.app.llm.llama_cpp import LlamaCppBackend
from backend.app.memory import MemoryExtractionResult, canonicalize_source_spans

CORPUS = REPOSITORY_ROOT / "tests" / "memory" / "philosophy_cases.yaml"


def user_payload(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    users = [message for message in case["messages"] if message["role"] == "user"]
    source_texts = {
        f"{case['id']}-user-{index}": message["content"] for index, message in enumerate(users)
    }
    target_id = next(reversed(source_texts))
    payload = {
        "target_message_id": target_id,
        "user_messages": [
            {"message_id": identifier, "content": content, "is_target": identifier == target_id}
            for identifier, content in source_texts.items()
        ],
    }
    return payload, source_texts


def evaluate(case: dict[str, Any], result: MemoryExtractionResult) -> list[str]:
    failures: list[str] = []
    expected = case.get("expected", [])
    if case["gate"] == "MUST_NOT_CAPTURE" and result.candidates:
        failures.append("expected no candidate")
    for wanted in expected:
        if not any(
            candidate.kind.value == wanted["kind"]
            and candidate.epistemic_status.value == wanted["epistemic_status"]
            for candidate in result.candidates
        ):
            failures.append(f"missing {wanted['kind']}/{wanted['epistemic_status']}")
    for forbidden in case.get("forbidden", []):
        if any(candidate.kind.value == forbidden["kind"] for candidate in result.candidates):
            failures.append(f"forbidden {forbidden['kind']}")
    return failures


async def run_case(backend: LlamaCppBackend, prompt: str, case: dict[str, Any]) -> dict[str, Any]:
    payload, source_texts = user_payload(case)
    result = await backend.generate_structured(
        [
            LLMMessage(role=LLMRole.SYSTEM, content=prompt),
            LLMMessage(role=LLMRole.USER, content=json.dumps(payload, ensure_ascii=False)),
        ],
        MemoryExtractionResult,
    )
    grounded = canonicalize_source_spans(result, source_texts, payload["target_message_id"])
    failures = evaluate(case, grounded.extraction)
    return {
        "id": case["id"],
        "gate": case["gate"],
        "passed": not failures,
        "failures": failures,
        "candidates": [
            {"kind": candidate.kind.value, "epistemic_status": candidate.epistemic_status.value}
            for candidate in grounded.extraction.candidates
        ],
    }


async def main_async(args: argparse.Namespace) -> int:
    document = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    cases = document["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
    settings = Settings(llama_server_url=args.llama_url)
    prompt = load_prompt(
        REPOSITORY_ROOT,
        settings.memory_extraction_prompt_id,
        settings.memory_extraction_prompt_version,
    )
    backend = LlamaCppBackend(settings)
    results = [await run_case(backend, prompt.content, case) for case in cases]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.model_name,
        "model_sha256": settings.model_expected_sha256,
        "prompt": {"id": prompt.id, "version": prompt.version, "sha256": prompt.sha256},
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-url", default="http://127.0.0.1:8080")
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "reports" / "milestone-f-corpus.json",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
