from __future__ import annotations

import asyncio
import gc
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as functional
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

from backend.app.retrieval.interfaces import Embedder, Reranker
from backend.app.retrieval.models import (
    ComponentHealth,
    ComponentStatus,
    RerankedSource,
    RerankInput,
    RetrievalModelInfo,
)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _last_token_pool(hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    if bool(attention_mask[:, -1].sum() == attention_mask.shape[0]):
        return hidden_state[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    return hidden_state[torch.arange(hidden_state.shape[0]), sequence_lengths]


class TransformerEmbedder(Embedder):
    def __init__(
        self,
        model_path: Path,
        *,
        name: str,
        revision: str,
        expected_sha256: str,
        query_instruction: str,
        dimensions: int,
        max_length: int = 1024,
        batch_size: int = 16,
    ) -> None:
        self.model_path = model_path
        self.name = name
        self.revision = revision
        self.expected_sha256 = expected_sha256
        self.query_instruction = query_instruction.strip()
        self.dimensions = dimensions
        self.max_length = max_length
        self.batch_size = batch_size
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def load(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return
            await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        weights = self.model_path / "model.safetensors"
        if not weights.is_file() or _file_sha256(weights) != self.expected_sha256:
            raise FileNotFoundError("Embedding model is missing or failed integrity validation")
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, local_files_only=True, padding_side="left"
        )
        self._model = AutoModel.from_pretrained(
            self.model_path, local_files_only=True, torch_dtype=torch.float32
        ).eval()

    async def embed_query(self, text: str) -> list[float]:
        await self.load()
        prompted = f"Instruct: {self.query_instruction}\nQuery: {text}"
        return (await asyncio.to_thread(self._embed_sync, [prompted]))[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        await self.load()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                await asyncio.to_thread(
                    self._embed_sync, list(texts[start : start + self.batch_size])
                )
            )
        return vectors

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        assert self._tokenizer is not None and self._model is not None
        inputs = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        with torch.inference_mode():
            output = self._model(**inputs)
            vectors = functional.normalize(
                _last_token_pool(output.last_hidden_state, inputs["attention_mask"]), p=2, dim=1
            )
        if vectors.shape[1] < self.dimensions:
            raise RuntimeError("Embedding model returned fewer dimensions than configured")
        vectors = functional.normalize(vectors[:, : self.dimensions], p=2, dim=1)
        return vectors.to(torch.float32).tolist()

    async def health(self) -> ComponentHealth:
        weights = self.model_path / "model.safetensors"
        if not weights.is_file():
            return ComponentHealth(
                status=ComponentStatus.UNAVAILABLE, detail_code="EMBEDDING_MODEL_MISSING"
            )
        if self._model is None:
            return ComponentHealth(
                status=ComponentStatus.DEGRADED, detail_code="EMBEDDER_NOT_LOADED"
            )
        return ComponentHealth(status=ComponentStatus.HEALTHY)

    def model_info(self) -> RetrievalModelInfo:
        return RetrievalModelInfo(
            name=self.name,
            revision=self.revision,
            model_sha256=self.expected_sha256,
            runtime="transformers-4.51.3/torch-2.6.0+cpu",
            device="cpu",
            dtype="float32",
            dimensions=self.dimensions,
        )

    async def close(self) -> None:
        self._model = None
        self._tokenizer = None
        await asyncio.to_thread(gc.collect)


class TransformerReranker(Reranker):
    def __init__(
        self,
        model_path: Path,
        *,
        name: str,
        revision: str,
        expected_sha256: str,
        instruction: str,
        max_length: int = 1024,
    ) -> None:
        self.model_path = model_path
        self.name = name
        self.revision = revision
        self.expected_sha256 = expected_sha256
        self.instruction = instruction.strip()
        self.max_length = max_length
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._false_token_id: int | None = None
        self._true_token_id: int | None = None
        self._prefix_tokens: list[int] = []
        self._suffix_tokens: list[int] = []
        self._load_lock = asyncio.Lock()

    async def load(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return
            await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        weights = self.model_path / "model.safetensors"
        if not weights.is_file() or _file_sha256(weights) != self.expected_sha256:
            raise FileNotFoundError("Reranker model is missing or failed integrity validation")
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, local_files_only=True, padding_side="left"
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_path, local_files_only=True, torch_dtype=torch.float32
        ).eval()
        self._false_token_id = self._tokenizer.convert_tokens_to_ids("no")
        self._true_token_id = self._tokenizer.convert_tokens_to_ids("yes")
        prefix = (
            "<|im_start|>system\nJudge whether the Document meets the requirements based on "
            'the Query and the Instruct provided. Note that the answer can only be "yes" or '
            '"no".<|im_end|>\n<|im_start|>user\n'
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self._prefix_tokens = self._tokenizer.encode(prefix, add_special_tokens=False)
        self._suffix_tokens = self._tokenizer.encode(suffix, add_special_tokens=False)

    async def rerank(
        self, query: str, candidates: Sequence[RerankInput], *, top_k: int
    ) -> list[RerankedSource]:
        if not candidates:
            return []
        await self.load()
        scores = await asyncio.to_thread(self._rerank_sync, query, list(candidates))
        ordered = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: (-item[1], item[0].source_type.value, item[0].source_id),
        )[:top_k]
        return [
            RerankedSource(
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                score=score,
                rank=rank,
            )
            for rank, (candidate, score) in enumerate(ordered, start=1)
        ]

    def _rerank_sync(self, query: str, candidates: list[RerankInput]) -> list[float]:
        assert self._tokenizer is not None and self._model is not None
        assert self._false_token_id is not None and self._true_token_id is not None
        pairs = [
            f"<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {item.content}"
            for item in candidates
        ]
        encoded = self._tokenizer(
            pairs,
            padding=False,
            truncation=True,
            return_attention_mask=False,
            max_length=self.max_length - len(self._prefix_tokens) - len(self._suffix_tokens),
        )
        for index, token_ids in enumerate(encoded["input_ids"]):
            encoded["input_ids"][index] = self._prefix_tokens + token_ids + self._suffix_tokens
        inputs = self._tokenizer.pad(encoded, padding=True, return_tensors="pt")
        with torch.inference_mode():
            logits = self._model(**inputs, logits_to_keep=1).logits[:, -1, :]
            binary_logits = torch.stack(
                [logits[:, self._false_token_id], logits[:, self._true_token_id]], dim=1
            )
            scores = functional.log_softmax(binary_logits, dim=1)[:, 1].exp()
        return scores.to(torch.float32).tolist()

    async def health(self) -> ComponentHealth:
        weights = self.model_path / "model.safetensors"
        if not weights.is_file():
            return ComponentHealth(
                status=ComponentStatus.UNAVAILABLE, detail_code="RERANKER_MODEL_MISSING"
            )
        if self._model is None:
            return ComponentHealth(
                status=ComponentStatus.DEGRADED, detail_code="RERANKER_NOT_LOADED"
            )
        return ComponentHealth(status=ComponentStatus.HEALTHY)

    def model_info(self) -> RetrievalModelInfo:
        return RetrievalModelInfo(
            name=self.name,
            revision=self.revision,
            model_sha256=self.expected_sha256,
            runtime="transformers-4.51.3/torch-2.6.0+cpu",
            device="cpu",
            dtype="float32",
        )

    async def close(self) -> None:
        self._model = None
        self._tokenizer = None
        await asyncio.to_thread(gc.collect)
