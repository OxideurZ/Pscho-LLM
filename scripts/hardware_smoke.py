#!/usr/bin/env python3
"""Exercise real cancellation or a brutal llama-server crash through FastAPI."""

import argparse
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psutil


def stream_events(response: httpx.Response) -> Iterator[tuple[str, dict[str, Any]]]:
    event = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                yield event, json.loads("\n".join(data_lines))
            event, data_lines = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())


def persisted_status(database: Path, run_id: str) -> str | None:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT status FROM model_runs WHERE id = ?", (run_id,)).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cancel", "crash"])
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--llama-pid", type=int)
    parser.add_argument("--after-deltas", type=int, default=10)
    parser.add_argument("--database", type=Path, default=Path("psych-local.db"))
    args = parser.parse_args()
    if args.mode == "crash" and args.llama_pid is None:
        parser.error("--llama-pid is required in crash mode")

    payload = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Écris un long récit synthétique en paragraphes, sans t'arrêter avant la fin."
                ),
            }
        ],
        "generation": {"max_tokens": 1000, "seed": 42},
    }
    events: list[str] = []
    run_id: str | None = None
    delta_count = 0
    action_taken = False
    recovery_terminal: str | None = None
    with httpx.Client(timeout=300) as client:
        with client.stream("POST", f"{args.api_url}/v1/chat", json=payload) as response:
            response.raise_for_status()
            for event, data in stream_events(response):
                events.append(event)
                if event == "run_started":
                    run_id = data["run_id"]
                elif event == "delta":
                    delta_count += 1
                if delta_count >= args.after_deltas and not action_taken:
                    if args.mode == "cancel":
                        assert run_id is not None
                        cancel = client.post(f"{args.api_url}/v1/runs/{run_id}/cancel")
                        if cancel.status_code != 202:
                            raise RuntimeError(f"Cancel returned HTTP {cancel.status_code}")
                    else:
                        psutil.Process(args.llama_pid).kill()
                    action_taken = True

        if args.mode == "cancel":
            recovery = client.post(
                f"{args.api_url}/v1/chat",
                json={
                    "messages": [{"role": "user", "content": "Réponds brièvement : prêt ?"}],
                    "generation": {"max_tokens": 64, "seed": 42},
                },
            )
            recovery.raise_for_status()
            recovery_events = [event for event, _ in stream_events(recovery)]
            recovery_terminal = next(
                (event for event in reversed(recovery_events) if event in {"done", "error"}), None
            )
            if recovery_terminal != "done":
                raise RuntimeError(f"Slot did not recover after cancellation: {recovery_events}")

    expected = "cancelled" if args.mode == "cancel" else "error"
    if expected not in events or "done" in events:
        raise RuntimeError(f"Unexpected event sequence: {events}")
    assert run_id is not None
    database_status = persisted_status(args.database, run_id)
    expected_status = "cancelled" if args.mode == "cancel" else "failed"
    if database_status != expected_status:
        raise RuntimeError(f"Expected DB status {expected_status}, got {database_status}")
    print(
        json.dumps(
            {
                "mode": args.mode,
                "run_id": run_id,
                "deltas_before_action": delta_count,
                "events": events,
                "database_status": database_status,
                "recovery_terminal": recovery_terminal,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
