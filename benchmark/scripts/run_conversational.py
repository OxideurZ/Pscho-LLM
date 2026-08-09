#!/usr/bin/env python3
"""Capture synthetic conversational responses for manual rubric evaluation."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from benchmark.scripts.run_technical import parse_sse, require_reference_metadata

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmark/results/conversational.jsonl"
    )
    args = parser.parse_args()
    try:
        metadata = require_reference_metadata()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    scenarios = json.loads(
        (ROOT / "benchmark/scenarios/conversation.json").read_text(encoding="utf-8")
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output, httpx.Client(timeout=600) as client:
        for scenario in scenarios:
            response = client.post(
                f"{args.api_url}/v1/chat",
                json={
                    "messages": [{"role": "user", "content": scenario["prompt"]}],
                    "generation": {"max_tokens": 800, "seed": 42},
                },
            )
            events = parse_sse(response.text)
            answer = "".join(data.get("text", "") for event, data in events if event == "delta")
            terminal = next(
                (event for event, _ in reversed(events) if event in {"done", "error"}), "error"
            )
            result = {
                "date": datetime.now(UTC).isoformat(),
                "scenario_id": scenario["id"],
                "synthetic_prompt": scenario["prompt"],
                "answer": answer,
                "terminal_event": terminal,
                "generation": {"max_tokens": 800, "seed": 42},
                **metadata,
            }
            output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            print(scenario["id"], terminal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
