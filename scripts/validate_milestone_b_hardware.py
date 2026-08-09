#!/usr/bin/env python3
"""Run the destructive Milestone B smoke matrix with self-owned local processes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.conversations import ConversationRepository
from backend.app.conversations.repository import new_id
from backend.app.db import Database

MODEL_SHA256 = "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
LLAMA_BUILD = "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3"


@dataclass
class TurnResult:
    run_id: str | None
    events: list[str]
    content: str
    terminal: str | None
    disconnected: bool = False


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def wait_ready(url: str, timeout: float, *, require_healthy: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # The API health route itself allows two seconds for the engine probe.
            # Keep the harness timeout wider so a degraded response can arrive cleanly.
            response = httpx.get(url, timeout=5)
            if response.is_success:
                body = response.json()
                if not require_healthy or body.get("status") == "healthy":
                    return body
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {url}") from last_error


def start_process(
    command: list[str], env: dict[str, str], log_path: Path
) -> subprocess.Popen[bytes]:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    log.close()
    return process


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def create_conversation(client: httpx.Client) -> str:
    response = client.post("/v1/conversations", json={})
    response.raise_for_status()
    return str(response.json()["id"])


def run_turn(
    client: httpx.Client,
    conversation_id: str,
    content: str,
    *,
    max_tokens: int,
    action: Callable[[str, str], bool] | None = None,
    allow_disconnect: bool = False,
) -> TurnResult:
    events: list[str] = []
    output = ""
    run_id: str | None = None
    action_taken = False
    disconnected = False
    try:
        with client.stream(
            "POST",
            f"/v1/conversations/{conversation_id}/turns",
            json={
                "client_turn_id": str(uuid4()),
                "content": content,
                "generation": {"max_tokens": max_tokens, "seed": 42},
            },
            timeout=600,
        ) as response:
            response.raise_for_status()
            for event, data in stream_events(response):
                events.append(event)
                if event == "run_started":
                    run_id = str(data["run_id"])
                elif event == "delta":
                    output += str(data["text"])
                if action and run_id and output and not action_taken:
                    action_taken = action(run_id, output)
    except httpx.HTTPError:
        if not allow_disconnect:
            raise
        disconnected = True
    terminal = next(
        (event for event in reversed(events) if event in {"done", "cancelled", "error"}),
        None,
    )
    return TurnResult(run_id, events, output, terminal, disconnected)


def latest_assistant(database_path: Path, conversation_id: str) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT m.id, m.status AS message_status, m.content, m.model_run_id,
                   r.status AS run_status, r.error_code
            FROM messages m JOIN model_runs r ON r.id = m.model_run_id
            WHERE m.conversation_id = ? AND m.role = 'assistant'
            ORDER BY m.sequence_no DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("No persisted assistant row")
    return dict(row)


def database_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        values = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM conversations),
              (SELECT COUNT(*) FROM messages),
              (SELECT COUNT(*) FROM summaries),
              (SELECT COUNT(*) FROM model_runs WHERE run_kind = 'rolling_summary'),
              (SELECT COUNT(*) FROM model_runs WHERE status IN ('starting', 'generating')),
              (SELECT COUNT(*) FROM messages WHERE status = 'streaming')
            """
        ).fetchone()
    assert values is not None
    keys = (
        "conversations",
        "messages",
        "summaries",
        "summary_runs",
        "nonterminal_runs",
        "streaming_messages",
    )
    return dict(zip(keys, (int(value) for value in values), strict=True))


async def seed_long_history(database_path: Path, conversation_id: str) -> None:
    database = Database(database_path, REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    metadata = {
        "model_name": "Qwen3.6-35B-A3B-Q4_K_M",
        "model_sha256": MODEL_SHA256,
        "backend_name": "llama.cpp",
        "backend_version": "b9637",
        "backend_build": LLAMA_BUILD,
        "prompt_id": "conversation_system",
        "prompt_version": "0.1.2",
        "prompt_sha256": "5a23b62014bf5fed816007d28391e3a6318e344bf28bb79984fb387036a161c6",
        "generation_config": {"hardware_fixture": True},
        "seed": 42,
        "app_version": "0.1.0-dev",
        "app_git_commit": None,
        "runtime_info": {"fixture": "milestone-b-long-context"},
        "context_size": 8192,
    }
    for index in range(3):
        ids = {
            "user_message_id": new_id("msg"),
            "assistant_message_id": new_id("msg"),
            "session_id": new_id("session"),
            "run_id": new_id("run"),
        }
        turn = await repository.begin_turn(
            conversation_id=conversation_id,
            client_turn_id=str(uuid4()),
            content=(f"TEST {index} CARIBOU " + "CARIBOU " * 250).strip(),
            input_type="text",
            ids=ids,
            run_metadata=metadata,
        )
        await repository.finalize(
            conversation_id=conversation_id,
            assistant_message_id=turn.assistant_message.id,
            run_id=turn.run_id,
            content=(f"PROPOSITION {index} " + "PROPOSITION " * 170).strip(),
            message_status="complete",
            run_status="complete",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        type=Path,
        default=REPOSITORY_ROOT / "models" / "Qwen3.6-35B-A3B-Q4_K_M.gguf",
    )
    parser.add_argument(
        "--llama-server",
        type=Path,
        default=REPOSITORY_ROOT / "tools" / "llama.cpp-b9637" / "llama-server.exe",
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "reports" / "milestone-b-hardware.json",
    )
    parser.add_argument("--startup-timeout", type=float, default=600)
    parser.add_argument("--skip-model-hash", action="store_true")
    args = parser.parse_args()

    model = args.model.resolve()
    server = args.llama_server.resolve()
    if not model.is_file() or not server.is_file():
        raise FileNotFoundError("Pinned model or llama-server executable is missing")
    if not args.skip_model_hash:
        observed_hash = file_sha256(model)
        if observed_hash != MODEL_SHA256:
            raise RuntimeError(f"Model SHA256 mismatch: {observed_hash}")
    else:
        observed_hash = MODEL_SHA256

    runtime = Path(tempfile.mkdtemp(prefix="psych-local-milestone-b-"))
    database_path = runtime / "data" / "app.sqlite"
    llama_log = runtime / "llama.log"
    api_log = runtime / "api.log"
    llama_port, api_port = free_port(), free_port()
    llama_url = f"http://127.0.0.1:{llama_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIRECTORY": str(runtime),
            "DATABASE_PATH": str(database_path),
            "LLAMA_SERVER_URL": llama_url,
            "CONTEXT_SIZE": "8192",
            "DEFAULT_MAX_TOKENS": "128",
            "CONTEXT_SAFETY_MARGIN_TOKENS": "256",
            "SUMMARY_BUDGET_TOKENS": "4096",
            "RECENT_RAW_BUDGET_TOKENS": "2000",
            "STREAM_CHECKPOINT_SECONDS": "0.25",
            "STREAM_CHECKPOINT_CHARACTERS": "64",
            "MODEL_PATH": str(model),
            "MODEL_EXPECTED_SHA256": observed_hash,
        }
    )
    llama_command = [
        str(server),
        "-m",
        str(model),
        "--alias",
        "Qwen3.6-35B-A3B-Q4_K_M",
        "--host",
        "127.0.0.1",
        "--port",
        str(llama_port),
        "-c",
        "32768",
        "--parallel",
        "1",
        "--jinja",
        "--reasoning",
        "off",
        "--metrics",
    ]
    api_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port),
    ]
    llama: subprocess.Popen[bytes] | None = None
    api: subprocess.Popen[bytes] | None = None
    results: dict[str, Any] = {
        "configuration": {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "os": platform.platform(),
            "python": platform.python_version(),
            "llama_cpp_version": "b9637",
            "llama_cpp_commit": LLAMA_BUILD,
            "model": model.name,
            "model_sha256": observed_hash,
            "conversation_prompt_sha256": (
                "5a23b62014bf5fed816007d28391e3a6318e344bf28bb79984fb387036a161c6"
            ),
            "summary_prompt_version": "0.1.1",
            "summary_prompt_sha256": file_sha256(
                REPOSITORY_ROOT / "prompts" / "rolling_summary" / "v0.1.1.md"
            ),
            "context_size_app": 8192,
            "context_size_engine": 32768,
            "checkpoint_seconds": 0.25,
            "checkpoint_characters": 64,
            "runtime_directory": str(runtime),
        }
    }
    try:
        llama = start_process(llama_command, env, llama_log)
        wait_ready(f"{llama_url}/health", args.startup_timeout)
        api = start_process(api_command, env, api_log)
        wait_ready(f"{api_url}/v1/health", 60, require_healthy=True)

        with httpx.Client(base_url=api_url, timeout=600) as client:
            conversation = create_conversation(client)
            normal = run_turn(client, conversation, "Réponds brièvement : prêt ?", max_tokens=64)
            if normal.terminal != "done":
                raise RuntimeError(f"Normal generation failed: {normal.events}")
            results["normal"] = asdict(normal)

            def cancel_action(run_id: str, output: str) -> bool:
                if len(output) < 64:
                    return False
                response = client.post(f"/v1/runs/{run_id}/cancel")
                if response.status_code != 202:
                    raise RuntimeError(f"Cancel returned {response.status_code}")
                return True

            cancelled = run_turn(
                client,
                conversation,
                "Écris au moins mille mots de texte artificiel continu.",
                max_tokens=800,
                action=cancel_action,
            )
            if cancelled.terminal != "cancelled":
                raise RuntimeError(f"Cancellation failed: {cancelled.events}")
            cancelled_row = latest_assistant(database_path, conversation)
            if (cancelled_row["message_status"], cancelled_row["run_status"]) != (
                "interrupted",
                "cancelled",
            ):
                raise RuntimeError(f"Invalid cancelled DB state: {cancelled_row}")
            results["cancel"] = {**asdict(cancelled), "database": cancelled_row}

            after_cancel = run_turn(client, conversation, "Réponds OK.", max_tokens=32)
            if after_cancel.terminal != "done":
                raise RuntimeError("Engine slot did not recover after cancellation")
            results["after_cancel"] = asdict(after_cancel)

            long_conversation = create_conversation(client)
            asyncio.run(seed_long_history(database_path, long_conversation))
            long_context = run_turn(
                client,
                long_conversation,
                "Réponds seulement OK après avoir lu le contexte artificiel.",
                max_tokens=64,
            )
            long_counts = database_counts(database_path)
            if long_context.terminal != "done" or long_counts["summaries"] < 1:
                raise RuntimeError(
                    f"Long context did not summarize successfully: {long_context.events}"
                )
            results["long_context"] = {**asdict(long_context), "counts": long_counts}

            crash_conversation = create_conversation(client)

            def kill_api(_run_id: str, output: str) -> bool:
                nonlocal api
                if len(output) < 96 or api is None:
                    return False
                api.kill()
                api.wait(timeout=30)
                return True

            api_crash = run_turn(
                client,
                crash_conversation,
                "Écris au moins mille mots de texte artificiel continu.",
                max_tokens=800,
                action=kill_api,
                allow_disconnect=True,
            )
            before_reconciliation = latest_assistant(database_path, crash_conversation)
            if before_reconciliation["message_status"] != "streaming":
                raise RuntimeError(f"FastAPI kill was not abrupt: {before_reconciliation}")
            checkpoint_loss = len(api_crash.content) - len(before_reconciliation["content"])
            if not 0 <= checkpoint_loss < 64:
                raise RuntimeError(f"Checkpoint loss exceeded bound: {checkpoint_loss}")

        api = start_process(api_command, env, api_log)
        restart_health = wait_ready(f"{api_url}/v1/health", 60, require_healthy=True)
        after_reconciliation = latest_assistant(database_path, crash_conversation)
        if (
            after_reconciliation["message_status"],
            after_reconciliation["run_status"],
            after_reconciliation["error_code"],
        ) != ("failed", "failed", "PROCESS_INTERRUPTED"):
            raise RuntimeError(f"Startup reconciliation failed: {after_reconciliation}")
        results["fastapi_crash"] = {
            **asdict(api_crash),
            "before_reconciliation": before_reconciliation,
            "after_reconciliation": after_reconciliation,
            "checkpoint_loss_characters": checkpoint_loss,
            "restart_database_ready": restart_health["database"]["status"] == "ready",
        }

        with httpx.Client(base_url=api_url, timeout=600) as client:
            after_api_restart = run_turn(client, crash_conversation, "Réponds OK.", max_tokens=32)
            if after_api_restart.terminal != "done":
                raise RuntimeError("Conversation did not resume after FastAPI restart")
            results["after_fastapi_restart"] = asdict(after_api_restart)

            engine_crash_conversation = create_conversation(client)

            def kill_llama(_run_id: str, output: str) -> bool:
                nonlocal llama
                if len(output) < 64 or llama is None:
                    return False
                llama.kill()
                llama.wait(timeout=30)
                return True

            llama_crash = run_turn(
                client,
                engine_crash_conversation,
                "Écris au moins mille mots de texte artificiel continu.",
                max_tokens=800,
                action=kill_llama,
            )
            if llama_crash.terminal != "error":
                raise RuntimeError(f"llama-server kill did not fail the turn: {llama_crash.events}")
            llama_failed_row = latest_assistant(database_path, engine_crash_conversation)
            degraded = wait_ready(f"{api_url}/v1/health", 30)
            if degraded["status"] != "degraded" or llama_failed_row["run_status"] != "failed":
                raise RuntimeError("Engine crash state was not durable/degraded")
            results["llama_crash"] = {
                **asdict(llama_crash),
                "database": llama_failed_row,
                "health": degraded["status"],
            }

            llama = start_process(llama_command, env, llama_log)
            wait_ready(f"{llama_url}/health", args.startup_timeout)
            wait_ready(f"{api_url}/v1/health", 30, require_healthy=True)
            after_engine_restart = run_turn(
                client, engine_crash_conversation, "Réponds OK.", max_tokens=32
            )
            if after_engine_restart.terminal != "done":
                raise RuntimeError("Conversation did not resume after engine restart")
            results["after_engine_restart"] = asdict(after_engine_restart)

        final_counts = database_counts(database_path)
        if final_counts["nonterminal_runs"] or final_counts["streaming_messages"]:
            raise RuntimeError(f"Zombie durable state remains: {final_counts}")
        with sqlite3.connect(database_path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            schema_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        results["final_database"] = {
            **final_counts,
            "integrity_check": integrity,
            "foreign_key_violations": len(foreign_key_violations),
            "schema_version": schema_version,
        }
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    finally:
        stop_process(api)
        stop_process(llama)
        print(f"Hardware runtime retained at: {runtime}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
