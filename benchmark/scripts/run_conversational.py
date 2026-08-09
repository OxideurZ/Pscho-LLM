#!/usr/bin/env python3
"""Capture versioned synthetic conversations for the Milestone A10.1 evaluation."""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from benchmark.scripts.run_technical import parse_sse, require_reference_metadata

ROOT = Path(__file__).resolve().parents[2]


def run_metadata(database: Path, run_id: str) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT prompt_id, prompt_version, prompt_sha256, generation_config_json,
                   model_name, model_sha256, backend_version, backend_build, app_git_commit
            FROM model_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Run metadata not found: {run_id}")
    return dict(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--database", type=Path, default=ROOT / "psych-local.db")
    parser.add_argument("--expected-prompt-version", required=True)
    parser.add_argument("--only-blind-subset", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = require_reference_metadata()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    scenarios = json.loads(
        (ROOT / "benchmark/scenarios/conversation.json").read_text(encoding="utf-8")
    )
    if args.only_blind_subset:
        scenarios = [scenario for scenario in scenarios if scenario["blind_compare"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output, httpx.Client(timeout=900) as client:
        for scenario in scenarios:
            response = client.post(
                f"{args.api_url}/v1/chat",
                json={
                    "messages": scenario["messages"],
                    "generation": {"max_tokens": args.max_tokens, "seed": 42},
                },
            )
            response.raise_for_status()
            events = parse_sse(response.text)
            run_started = next(data for event, data in events if event == "run_started")
            metrics = next((data for event, data in events if event == "metrics"), {})
            answer = "".join(data.get("text", "") for event, data in events if event == "delta")
            terminal = next(
                (event for event, _ in reversed(events) if event in {"done", "error"}), "error"
            )
            persisted = run_metadata(args.database, run_started["run_id"])
            if persisted["prompt_version"] != args.expected_prompt_version:
                raise RuntimeError(
                    f"Expected prompt {args.expected_prompt_version}, "
                    f"got {persisted['prompt_version']}"
                )
            result = {
                "date": datetime.now(UTC).isoformat(),
                "scenario_id": scenario["id"],
                "category": scenario["category"],
                "messages": scenario["messages"],
                "answer": answer,
                "terminal_event": terminal,
                "output_tokens": metrics.get("output_tokens"),
                "finish_reason": metrics.get("finish_reason"),
                "hit_max_tokens": metrics.get("hit_max_tokens"),
                "metrics": metrics,
                "run_id": run_started["run_id"],
                **metadata,
                **persisted,
            }
            output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            print(
                scenario["id"],
                terminal,
                f"tokens={metrics.get('output_tokens')}",
                f"truncated={metrics.get('hit_max_tokens')}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
