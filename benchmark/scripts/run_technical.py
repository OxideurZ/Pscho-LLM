#!/usr/bin/env python3
"""Run reproducible cold/warm context benchmarks through Psych-local."""

import argparse
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil

from benchmark.scripts.context_factory import ContextScenario, build_context
from scripts.verify_model import verify

ROOT = Path(__file__).resolve().parents[2]


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        event = next(
            (line[6:].strip() for line in frame.splitlines() if line.startswith("event:")), None
        )
        data = next(
            (line[5:].strip() for line in frame.splitlines() if line.startswith("data:")), None
        )
        if event and data:
            events.append((event, json.loads(data)))
    return events


def require_reference_metadata() -> dict[str, str]:
    names = [
        "APP_VERSION",
        "LLAMA_CPP_VERSION",
        "LLAMA_CPP_BUILD",
        "MODEL_NAME",
        "MODEL_PATH",
        "MODEL_EXPECTED_SHA256",
    ]
    values = {name: os.getenv(name, "") for name in names}
    missing = [name for name, value in values.items() if not value or value == "REQUIRED"]
    if missing:
        raise ValueError("Missing reference metadata: " + ", ".join(missing))
    verify(Path(values["MODEL_PATH"]), values["MODEL_EXPECTED_SHA256"])
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--llama-pid", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark/results/technical.jsonl")
    args = parser.parse_args()
    try:
        metadata = require_reference_metadata()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    manifest = json.loads((ROOT / "benchmark/contexts/manifest.json").read_text(encoding="utf-8"))
    process = psutil.Process(args.llama_pid) if args.llama_pid else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output, httpx.Client(timeout=1800) as client:
        for raw in manifest["scenarios"]:
            scenario = ContextScenario(task=manifest["task"], **raw)
            context = build_context(scenario)
            for repetition in range(args.repetitions):
                mode = "cold" if repetition == 0 else "warm"
                ram_before = psutil.virtual_memory().used
                llama_rss_before = process.memory_info().rss if process else None
                response = client.post(
                    f"{args.api_url}/v1/chat",
                    json={
                        "messages": [{"role": "user", "content": context}],
                        "generation": {"max_tokens": 800, "seed": 42},
                    },
                )
                events = parse_sse(response.text)
                metrics = next((data for event, data in events if event == "metrics"), {})
                terminal = next(
                    (event for event, _ in reversed(events) if event in {"done", "error"}), "error"
                )
                result = {
                    "date": datetime.now(UTC).isoformat(),
                    "scenario": scenario.id,
                    "target_context_tokens": scenario.target_tokens,
                    "repetition": repetition + 1,
                    "mode": mode,
                    "success": response.is_success and terminal == "done",
                    "terminal_event": terminal,
                    "ram_used_before_bytes": ram_before,
                    "ram_used_after_bytes": psutil.virtual_memory().used,
                    "llama_rss_before_bytes": llama_rss_before,
                    "llama_rss_after_bytes": process.memory_info().rss if process else None,
                    "os": platform.platform(),
                    "hardware": platform.machine(),
                    "generation": {"max_tokens": 800, "seed": 42},
                    **metadata,
                    **metrics,
                }
                output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
                print(scenario.id, mode, terminal, metrics.get("tokens_per_second"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
