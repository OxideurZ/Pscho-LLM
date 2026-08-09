#!/usr/bin/env python3
"""Measure growing conversations with natural, controlled, and cold-restart paths."""

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from benchmark.scripts.context_factory import ContextScenario, build_context
from benchmark.scripts.run_technical import (
    ResourceSampler,
    parse_sse,
    require_reference_metadata,
)

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (5_000, 10_000, 15_000, 20_000, 30_000)
CACHE_CONFIGURATION = {
    "cache_prompt": True,
    "cache_reuse": 0,
    "cache_reuse_description": "llama-server longest-prefix reuse; KV-shift reuse disabled",
    "parallel": 1,
    "number_of_slots": 1,
    "slot_prompt_similarity": 0.10,
    "context_size": 32_768,
}


def synthetic_turn(target_tokens: int, seed: int, turn: int) -> str:
    scenario = ContextScenario(
        id=f"incremental-{turn}",
        target_tokens=max(target_tokens, 300),
        seed=seed + turn,
        task=(
            "Réponds brièvement en relevant le fil principal de ces notes et une incertitude "
            "qui mérite de rester ouverte."
        ),
    )
    return build_context(scenario)


def direct_llama_metrics(body: str) -> tuple[str, dict[str, Any], str]:
    answer_parts: list[str] = []
    usage: dict[str, int] = {}
    timings: dict[str, int | float | None] = {}
    finish_reason: str | None = None
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        usage = chunk.get("usage") or usage
        timings = chunk.get("timings") or timings
        choices = chunk.get("choices") or []
        if choices:
            answer_parts.append(choices[0].get("delta", {}).get("content") or "")
            finish_reason = choices[0].get("finish_reason") or finish_reason
    input_tokens = usage.get("prompt_tokens")
    evaluated = timings.get("prompt_n")
    observable = isinstance(input_tokens, int) and isinstance(evaluated, int)
    reused = max(input_tokens - evaluated, 0) if observable else None
    metrics = {
        "input_tokens": input_tokens,
        "output_tokens": usage.get("completion_tokens"),
        "ttft_ms": timings.get("prompt_ms"),
        "prompt_eval_ms": timings.get("prompt_ms"),
        "generation_ms": timings.get("predicted_ms"),
        "tokens_per_second": timings.get("predicted_per_second"),
        "total_ms": timings.get("prompt_ms", 0) + timings.get("predicted_ms", 0),
        "evaluated_prompt_tokens": evaluated if observable else None,
        "reused_prompt_tokens": reused,
        "cache_reuse_observable": observable,
        "finish_reason": finish_reason,
        "hit_max_tokens": finish_reason == "length",
    }
    return "".join(answer_parts), metrics, "done" if finish_reason else "error"


def request_natural(
    client: httpx.Client, api_url: str, messages: list[dict[str, str]], max_tokens: int
) -> tuple[str, dict[str, Any], str]:
    response = client.post(
        f"{api_url}/v1/chat",
        json={"messages": messages, "generation": {"max_tokens": max_tokens, "seed": 42}},
    )
    response.raise_for_status()
    events = parse_sse(response.text)
    answer = "".join(data.get("text", "") for event, data in events if event == "delta")
    metrics = next((data for event, data in events if event == "metrics"), {})
    terminal = next(
        (event for event, _ in reversed(events) if event in {"done", "error", "cancelled"}),
        "error",
    )
    return answer, metrics, terminal


def request_controlled(
    client: httpx.Client,
    llama_url: str,
    prompt: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> tuple[str, dict[str, Any], str]:
    response = client.post(
        f"{llama_url}/v1/chat/completions",
        json={
            "model": Settings().model_name,
            "messages": [{"role": "system", "content": prompt}, *messages],
            "temperature": 0.7,
            "top_p": 0.9,
            "seed": 42,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "id_slot": 0,
        },
    )
    response.raise_for_status()
    return direct_llama_metrics(response.text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("natural", "controlled", "cold-after-restart"), required=True
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--llama-url", default="http://127.0.0.1:8080")
    parser.add_argument("--llama-pid", type=int)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument(
        "--history", type=Path, default=ROOT / "benchmark/results/incremental-history.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark/results/incremental.jsonl")
    args = parser.parse_args()

    try:
        metadata = require_reference_metadata()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    settings = Settings()
    prompt = load_prompt(REPOSITORY_ROOT, settings.prompt_id, settings.prompt_version).content
    process = psutil.Process(args.llama_pid) if args.llama_pid else None
    messages: list[dict[str, str]] = []
    current_input_tokens = 0
    targets = TARGETS
    if args.mode == "cold-after-restart":
        messages = json.loads(args.history.read_text(encoding="utf-8"))["messages"]
        messages.append(
            {
                "role": "user",
                "content": "En reprenant ce fil, quel point reste le plus incertain ?",
            }
        )
        targets = (30_000,)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output, httpx.Client(timeout=1800) as client:
        for turn, target in enumerate(targets, 1):
            if args.mode != "cold-after-restart":
                requested_segment = max(target - current_input_tokens - 150, 300)
                messages.append(
                    {"role": "user", "content": synthetic_turn(requested_segment, 4610, turn)}
                )
            ram_before = psutil.virtual_memory().used
            with ResourceSampler(process) as resources:
                if args.mode == "controlled":
                    answer, metrics, terminal = request_controlled(
                        client, args.llama_url, prompt, messages, args.max_tokens
                    )
                else:
                    answer, metrics, terminal = request_natural(
                        client, args.api_url, messages, args.max_tokens
                    )
            current_input_tokens = metrics.get("input_tokens") or target
            result = {
                "date": datetime.now(UTC).isoformat(),
                "benchmark": f"INCREMENTAL-{args.mode.upper()}",
                "turn": turn,
                "target_context_tokens": target,
                "success": terminal == "done",
                "terminal_event": terminal,
                "generation": {"max_tokens": args.max_tokens, "seed": 42},
                "cache_configuration": CACHE_CONFIGURATION,
                "ram_used_before_bytes": ram_before,
                "ram_used_after_bytes": psutil.virtual_memory().used,
                "ram_peak_bytes": resources.ram_peak_bytes,
                "llama_rss_peak_bytes": resources.llama_rss_peak_bytes or None,
                "vram_peak_mb": resources.vram_peak_mb,
                "os": platform.platform(),
                **metadata,
                **metrics,
            }
            output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            print(
                args.mode, target, terminal,
                f"input={metrics.get('input_tokens')}",
                f"evaluated={metrics.get('evaluated_prompt_tokens')}",
                f"reused={metrics.get('reused_prompt_tokens')}",
                f"ttft={metrics.get('ttft_ms')}",
            )
            if terminal != "done":
                return 1
            if args.mode != "cold-after-restart":
                messages.append({"role": "assistant", "content": answer})

    if args.mode != "cold-after-restart":
        args.history.write_text(
            json.dumps(
                {"source_mode": args.mode, "messages": messages},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
