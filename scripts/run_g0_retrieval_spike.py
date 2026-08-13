"""Run the Milestone G0 retrieval/runtime spike on the reference workstation.

The runner uses only local model snapshots. It loads the embedder and reranker
sequentially on CPU so the chat model keeps priority over retrieval acceleration.
No personal conversation content is read or written.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil
import torch
import torch.nn.functional as functional
from sqlcipher3 import dbapi2 as sqlcipher
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
EMBEDDING_SHA256 = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
RERANKER_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
RERANKER_SHA256 = "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"

QUERY_INSTRUCTION = (
    "Given the user's current message, retrieve only autobiographical historical "
    "context that is directly useful for answering it."
)
RERANK_INSTRUCTION = (
    "Judge whether the historical document is directly useful for answering the "
    "user's current message. Prefer no context when relevance is weak."
)

QUERY = "Qu'est-ce qui m'aide généralement à apprendre ?"
DOCUMENTS = [
    "Je me sens mieux quand je comprends pourquoi les choses fonctionnent.",
    "J'ai acheté du pain hier soir.",
    "Mon ventilateur fait parfois du bruit pendant les appels.",
    "J'apprends mieux quand une explication montre les causes et les mécanismes.",
    "La terrasse du restaurant se trouvait derrière l'église.",
    "Je préfère le thé vert au café.",
    "J'ai déménagé de Sion à Lausanne.",
    "CARIBOU est un mot présent dans un ancien test de sécurité.",
]


def parse_args() -> argparse.Namespace:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    model_root = local_app_data / "PsychLocal" / "models" / "retrieval"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--embedding-model",
        type=Path,
        default=model_root / "Qwen3-Embedding-0.6B",
    )
    parser.add_argument(
        "--reranker-model",
        type=Path,
        default=model_root / "Qwen3-Reranker-0.6B",
    )
    parser.add_argument("--chat-url", default="http://127.0.0.1:8080")
    parser.add_argument("--stt-url", default="http://127.0.0.1:8000/v1/stt/health")
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 120) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback URLs only
        return json.loads(response.read())


def chat_probe(base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = http_json(
            f"{base_url}/v1/chat/completions",
            {
                "model": "Qwen3.6-35B-A3B-Q4_K_M",
                "messages": [
                    {"role": "system", "content": "Réponds uniquement par OK."},
                    {"role": "user", "content": "Test de disponibilité local."},
                ],
                "temperature": 0,
                "max_tokens": 8,
                "stream": False,
            },
        )
        content = response["choices"][0]["message"]["content"].strip()
        return {
            "ok": bool(content),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "content_length": len(content),
        }
    except (OSError, URLError, KeyError, ValueError) as error:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error_type": type(error).__name__,
        }


def vram_used_mib() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(result.stdout.splitlines()[0].strip())
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def process_rss_mib() -> float:
    return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def latency_summary(values: list[float]) -> dict[str, Any]:
    return {
        "runs_ms": [round(value, 2) for value in values],
        "p50_ms": round(median(values), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
    }


def probe_fts5() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="psych-g0-") as directory:
        database_path = Path(directory) / "fts5.sqlite3"
        key = os.urandom(32).hex()
        connection = sqlcipher.connect(str(database_path))
        connection.execute(f"PRAGMA key = \"x'{key}'\"")
        connection.execute("PRAGMA foreign_keys = ON")
        cipher_version = connection.execute("PRAGMA cipher_version").fetchone()[0]
        sqlite_version = connection.execute("SELECT sqlite_version()").fetchone()[0]
        compile_options = {
            row[0] for row in connection.execute("PRAGMA compile_options").fetchall()
        }
        connection.execute(
            "CREATE VIRTUAL TABLE retrieval_fts USING "
            "fts5(source_type, source_id UNINDEXED, content)"
        )
        connection.execute(
            "INSERT INTO retrieval_fts VALUES (?, ?, ?)",
            ("memory", "memory-1", "Lausanne terrasse CARIBOU"),
        )
        hit = connection.execute(
            "SELECT source_id, bm25(retrieval_fts) FROM retrieval_fts WHERE retrieval_fts MATCH ?",
            ("Lausanne",),
        ).fetchone()
        connection.commit()
        connection.close()
        plaintext_rejected = False
        plaintext_connection = sqlite3.connect(database_path)
        try:
            plaintext_connection.execute("SELECT * FROM retrieval_fts").fetchall()
        except sqlite3.DatabaseError:
            plaintext_rejected = True
        finally:
            plaintext_connection.close()
        return {
            "sqlite_version": sqlite_version,
            "sqlcipher_version": cipher_version,
            "enable_fts5": any("ENABLE_FTS5" in option for option in compile_options),
            "query_ok": hit is not None and hit[0] == "memory-1",
            "plaintext_reader_rejected": plaintext_rejected,
        }


def last_token_pool(hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    if bool(attention_mask[:, -1].sum() == attention_mask.shape[0]):
        return hidden_state[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    return hidden_state[torch.arange(hidden_state.shape[0]), sequence_lengths]


def embedding_inputs() -> list[str]:
    query = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {QUERY}"
    return [query, *DOCUMENTS]


def run_embedding(model_path: Path, warm_runs: int, chat_url: str) -> dict[str, Any]:
    before_rss = process_rss_mib()
    before_vram = vram_used_mib()
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, local_files_only=True, padding_side="left"
    )
    model = AutoModel.from_pretrained(
        model_path, local_files_only=True, torch_dtype=torch.float32
    ).eval()
    load_ms = (time.perf_counter() - started) * 1000
    loaded_rss = process_rss_mib()
    loaded_vram = vram_used_mib()

    inputs = tokenizer(
        embedding_inputs(), padding=True, truncation=True, max_length=512, return_tensors="pt"
    )

    def embed() -> torch.Tensor:
        with torch.inference_mode():
            output = model(**inputs)
            return functional.normalize(
                last_token_pool(output.last_hidden_state, inputs["attention_mask"]), p=2, dim=1
            )

    cold_started = time.perf_counter()
    vectors = embed()
    cold_ms = (time.perf_counter() - cold_started) * 1000
    warm_latencies: list[float] = []
    for _ in range(warm_runs):
        warm_started = time.perf_counter()
        vectors = embed()
        warm_latencies.append((time.perf_counter() - warm_started) * 1000)
    similarities = (vectors[0] @ vectors[1:].T).tolist()
    model_hash = sha256(model_path / "model.safetensors")
    return {
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "revision": EMBEDDING_REVISION,
        "model_sha256": model_hash,
        "expected_sha256": EMBEDDING_SHA256,
        "hash_ok": model_hash == EMBEDDING_SHA256,
        "device": str(model.device),
        "weight_dtype": str(model.dtype),
        "embedding_dtype": str(vectors.dtype),
        "dimensions": int(vectors.shape[1]),
        "batch_size": len(embedding_inputs()),
        "load_ms": round(load_ms, 2),
        "cold_batch_ms": round(cold_ms, 2),
        "warm_batch": latency_summary(warm_latencies),
        "rss_before_mib": before_rss,
        "rss_loaded_mib": loaded_rss,
        "rss_delta_mib": round(loaded_rss - before_rss, 2),
        "vram_before_mib": before_vram,
        "vram_loaded_mib": loaded_vram,
        "similarities": [round(score, 6) for score in similarities],
        "best_document_index": max(range(len(similarities)), key=similarities.__getitem__),
        "chat_during_load": chat_probe(chat_url),
    }


def format_reranker_input(query: str, document: str) -> str:
    return f"<Instruct>: {RERANK_INSTRUCTION}\n<Query>: {query}\n<Document>: {document}"


def run_reranker(model_path: Path, warm_runs: int, chat_url: str) -> dict[str, Any]:
    before_rss = process_rss_mib()
    before_vram = vram_used_mib()
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, local_files_only=True, padding_side="left"
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path, local_files_only=True, torch_dtype=torch.float32
    ).eval()
    load_ms = (time.perf_counter() - started) * 1000
    loaded_rss = process_rss_mib()
    loaded_vram = vram_used_mib()

    false_token_id = tokenizer.convert_tokens_to_ids("no")
    true_token_id = tokenizer.convert_tokens_to_ids("yes")
    prefix = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on "
        'the Query and the Instruct provided. Note that the answer can only be "yes" or '
        '"no".<|im_end|>\n<|im_start|>user\n'
    )
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    prefix_tokens = tokenizer.encode(prefix, add_special_tokens=False)
    suffix_tokens = tokenizer.encode(suffix, add_special_tokens=False)
    encoded = tokenizer(
        [format_reranker_input(QUERY, document) for document in DOCUMENTS],
        padding=False,
        truncation=True,
        return_attention_mask=False,
        max_length=512 - len(prefix_tokens) - len(suffix_tokens),
    )
    for index, token_ids in enumerate(encoded["input_ids"]):
        encoded["input_ids"][index] = prefix_tokens + token_ids + suffix_tokens
    inputs = tokenizer.pad(encoded, padding=True, return_tensors="pt")

    def rerank() -> torch.Tensor:
        with torch.inference_mode():
            logits = model(**inputs, logits_to_keep=1).logits[:, -1, :]
            binary_logits = torch.stack(
                [logits[:, false_token_id], logits[:, true_token_id]], dim=1
            )
            return functional.log_softmax(binary_logits, dim=1)[:, 1].exp()

    cold_started = time.perf_counter()
    scores = rerank()
    cold_ms = (time.perf_counter() - cold_started) * 1000
    warm_latencies: list[float] = []
    for _ in range(warm_runs):
        warm_started = time.perf_counter()
        scores = rerank()
        warm_latencies.append((time.perf_counter() - warm_started) * 1000)
    score_values = scores.tolist()
    model_hash = sha256(model_path / "model.safetensors")
    return {
        "model": "Qwen/Qwen3-Reranker-0.6B",
        "revision": RERANKER_REVISION,
        "model_sha256": model_hash,
        "expected_sha256": RERANKER_SHA256,
        "hash_ok": model_hash == RERANKER_SHA256,
        "device": str(model.device),
        "weight_dtype": str(model.dtype),
        "candidate_count": len(DOCUMENTS),
        "load_ms": round(load_ms, 2),
        "cold_batch_ms": round(cold_ms, 2),
        "warm_batch": latency_summary(warm_latencies),
        "rss_before_mib": before_rss,
        "rss_loaded_mib": loaded_rss,
        "rss_delta_mib": round(loaded_rss - before_rss, 2),
        "vram_before_mib": before_vram,
        "vram_loaded_mib": loaded_vram,
        "scores": [round(score, 6) for score in score_values],
        "best_document_index": max(range(len(score_values)), key=score_values.__getitem__),
        "chat_during_load": chat_probe(chat_url),
    }


def main() -> int:
    args = parse_args()
    if args.warm_runs < 1:
        raise ValueError("--warm-runs must be positive")
    for path in (args.embedding_model, args.reranker_model):
        if not (path / "model.safetensors").is_file():
            raise FileNotFoundError(f"Missing local model snapshot: {path}")

    try:
        stt = http_json(args.stt_url, timeout=10)
    except (OSError, URLError, ValueError) as error:
        stt = {"status": "unavailable", "error_type": type(error).__name__}

    fts5_result = probe_fts5()
    embedding_result = run_embedding(args.embedding_model, args.warm_runs, args.chat_url)
    gc.collect()
    reranker_result = run_reranker(args.reranker_model, args.warm_runs, args.chat_url)
    gc.collect()
    result = {
        "schema": "milestone-g-retrieval-spike:v1.0",
        "runtime": {
            "python": os.sys.version.split()[0],
            "torch": torch.__version__,
            "transformers": __import__("transformers").__version__,
            "torch_cuda_available": torch.cuda.is_available(),
            "process_model": (
                "in-process sequential CPU load; chat remains a separate llama.cpp process"
            ),
            "logical_cpu_count": psutil.cpu_count(logical=True),
            "physical_cpu_count": psutil.cpu_count(logical=False),
            "total_ram_mib": round(psutil.virtual_memory().total / (1024 * 1024), 2),
        },
        "fts5": fts5_result,
        "stt": stt,
        "chat_baseline": chat_probe(args.chat_url),
        "embedding": embedding_result,
        "reranker": reranker_result,
        "rss_after_unload_mib": process_rss_mib(),
        "vram_after_mib": vram_used_mib(),
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
