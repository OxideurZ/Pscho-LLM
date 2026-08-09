import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.llm import GenerationOptions, LlamaCppBackend
from backend.app.llm.errors import (
    LLMBackendProtocolError,
    LLMBackendTimeout,
    LLMBackendUnavailable,
)
from backend.app.llm.models import LLMMessage, LLMRole
from backend.app.runs.models import ActiveRun


@pytest.mark.asyncio
async def test_llama_backend_maps_request_and_parses_stream() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = "".join(
            [
                'data: {"choices":[{"delta":{"content":"Bon"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"jour"}}],',
                '"usage":{"prompt_tokens":7,"completion_tokens":2},',
                '"timings":{"prompt_ms":5,"predicted_ms":8,"predicted_per_second":250}}\n\n',
                "data: [DONE]\n\n",
            ]
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    ) as client:
        backend = LlamaCppBackend(Settings(), client)
        events = [
            event
            async for event in backend.chat_stream(
                [LLMMessage(role=LLMRole.USER, content="Salut")],
                GenerationOptions(top_k=40, min_p=0.05, seed=7),
                ActiveRun("run_test"),
            )
        ]

    assert captured["stream"] is True
    assert captured["top_k"] == 40
    assert captured["min_p"] == 0.05
    assert [event.text for event in events if event.type == "delta"] == ["Bon", "jour"]
    metrics = next(event.metrics for event in events if event.type == "metrics")
    assert metrics and metrics["input_tokens"] == 7
    assert metrics["output_tokens"] == 2
    assert metrics["tokens_per_second"] == 250


@pytest.mark.asyncio
async def test_llama_backend_maps_connection_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    ) as client:
        backend = LlamaCppBackend(Settings(), client)
        with pytest.raises(LLMBackendUnavailable):
            async for _ in backend.chat_stream(
                [LLMMessage(role=LLMRole.USER, content="Salut")],
                GenerationOptions(),
                ActiveRun("run_test"),
            ):
                pass


@pytest.mark.asyncio
async def test_health_recovers_without_recreating_backend() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    ) as client:
        backend = LlamaCppBackend(Settings(), client)
        first = await backend.health()
        second = await backend.health()

    assert first.status == "unavailable"
    assert second.status == "ok"
    assert second.model_loaded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_error", "expected"),
    [
        (httpx.ReadTimeout("slow"), LLMBackendTimeout),
        (httpx.DecodingError("bad stream"), LLMBackendProtocolError),
    ],
)
async def test_transport_errors_are_mapped(
    upstream_error: Exception, expected: type[Exception]
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(upstream_error, httpx.ReadTimeout):
            raise httpx.ReadTimeout("slow", request=request)
        raise upstream_error

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    ) as client:
        backend = LlamaCppBackend(Settings(), client)
        with pytest.raises(expected):
            async for _ in backend.chat_stream(
                [LLMMessage(role=LLMRole.USER, content="Salut")],
                GenerationOptions(),
                ActiveRun("run_test"),
            ):
                pass
