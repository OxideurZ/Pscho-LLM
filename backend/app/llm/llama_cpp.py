import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from time import perf_counter

import httpx
from pydantic import BaseModel

from backend.app.config import Settings
from backend.app.llm.errors import (
    LLMBackendProtocolError,
    LLMBackendTimeout,
    LLMBackendUnavailable,
    LLMError,
)
from backend.app.llm.models import (
    GenerationOptions,
    HealthStatus,
    LLMMessage,
    LLMStreamEvent,
    ModelInfo,
)
from backend.app.runs.models import ActiveRun


class LlamaCppBackend:
    name = "llama.cpp"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    def _payload(self, messages: list[LLMMessage], options: GenerationOptions) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.settings.model_name,
            "messages": [message.model_dump(mode="json") for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": options.temperature,
            "top_p": options.top_p,
            "max_tokens": options.max_tokens,
            "seed": options.seed,
            "stop": options.stop,
        }
        if options.top_k is not None:
            payload["top_k"] = options.top_k
        if options.min_p is not None:
            payload["min_p"] = options.min_p
        return payload

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        run: ActiveRun,
    ) -> AsyncIterator[LLMStreamEvent]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self.settings.llama_server_url,
            timeout=httpx.Timeout(connect=5, read=300, write=10, pool=5),
        )
        started = perf_counter()
        first_token_at: float | None = None
        output_tokens = 0
        usage: dict[str, int] = {}
        timings: dict[str, int | float | None] = {}
        finish_reason: str | None = None

        try:
            async with client.stream(
                "POST", "/v1/chat/completions", json=self._payload(messages, options)
            ) as response:
                run.upstream_response = response
                response.raise_for_status()
                yield LLMStreamEvent(type="generation_started")
                async for line in response.aiter_lines():
                    if run.cancel_event.is_set():
                        raise asyncio.CancelledError
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise LLMBackendProtocolError("Invalid upstream SSE JSON") from error
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    if chunk.get("timings"):
                        timings = chunk["timings"]
                    choices = chunk.get("choices") or []
                    if choices and choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                    text = choices[0].get("delta", {}).get("content") if choices else None
                    if text:
                        if first_token_at is None:
                            first_token_at = perf_counter()
                        output_tokens += 1
                        yield LLMStreamEvent(type="delta", text=text)

                finished = perf_counter()
                generation_ms = (
                    round((finished - first_token_at) * 1000) if first_token_at else None
                )
                reported_output = usage.get("completion_tokens", output_tokens)
                input_tokens = usage.get("prompt_tokens")
                evaluated_prompt_tokens = timings.get("prompt_n")
                cache_reuse_observable = isinstance(evaluated_prompt_tokens, int)
                reused_prompt_tokens = (
                    max(input_tokens - evaluated_prompt_tokens, 0)
                    if input_tokens is not None and isinstance(evaluated_prompt_tokens, int)
                    else None
                )
                tokens_per_second = (
                    round(reported_output / ((finished - first_token_at) or 1), 3)
                    if first_token_at
                    else None
                )
                yield LLMStreamEvent(
                    type="metrics",
                    metrics={
                        "ttft_ms": round((first_token_at - started) * 1000)
                        if first_token_at
                        else None,
                        "input_tokens": input_tokens,
                        "output_tokens": reported_output,
                        "finish_reason": finish_reason,
                        "hit_max_tokens": finish_reason == "length",
                        "prompt_eval_ms": timings.get("prompt_ms"),
                        "evaluated_prompt_tokens": evaluated_prompt_tokens,
                        "reused_prompt_tokens": reused_prompt_tokens,
                        "cache_reuse_observable": cache_reuse_observable,
                        "generation_ms": timings.get("predicted_ms", generation_ms),
                        "tokens_per_second": timings.get("predicted_per_second", tokens_per_second),
                        "total_ms": round((finished - started) * 1000),
                    },
                )
        except asyncio.CancelledError:
            raise
        except httpx.ConnectError as error:
            raise LLMBackendUnavailable("llama-server is unavailable") from error
        except httpx.TimeoutException as error:
            raise LLMBackendTimeout("llama-server timed out") from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise LLMBackendUnavailable("llama-server returned an error") from error
            raise LLMBackendProtocolError("llama-server rejected the request") from error
        except (httpx.DecodingError, httpx.RemoteProtocolError) as error:
            raise LLMBackendProtocolError("llama-server returned an invalid stream") from error
        except httpx.HTTPError as error:
            raise LLMError("llama-server stream failed") from error
        finally:
            run.upstream_response = None
            if owns_client:
                with suppress(Exception):
                    await client.aclose()

    async def generate_structured(
        self, messages: list[LLMMessage], schema: type[BaseModel]
    ) -> BaseModel:
        raise NotImplementedError("Structured generation is reserved for a later milestone")

    async def health(self) -> HealthStatus:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self.settings.llama_server_url, timeout=2
        )
        try:
            response = await client.get("/health")
            available = response.is_success
            return HealthStatus(
                status="ok" if available else "unavailable",
                backend=self.name,
                model_loaded=available,
            )
        except httpx.HTTPError:
            return HealthStatus(status="unavailable", backend=self.name, model_loaded=False)
        finally:
            if owns_client:
                await client.aclose()

    async def model_info(self) -> ModelInfo:
        health = await self.health()
        return ModelInfo(
            backend=self.name,
            model=self.settings.model_name,
            available=health.status == "ok",
            context_size=self.settings.context_size,
            metadata={
                "version": self.settings.llama_cpp_version,
                "build": self.settings.llama_cpp_build,
            },
        )
