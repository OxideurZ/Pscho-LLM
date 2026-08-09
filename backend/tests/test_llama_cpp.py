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
from backend.app.summaries import RollingSummary


@pytest.mark.asyncio
async def test_llama_backend_maps_request_and_parses_stream() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = "".join(
            [
                'data: {"choices":[{"delta":{"content":"Bon"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"jour"},"finish_reason":"stop"}],',
                '"usage":{"prompt_tokens":7,"completion_tokens":2},',
                '"timings":{"prompt_n":2,"prompt_ms":5,"predicted_ms":8,',
                '"predicted_per_second":250}}\n\n',
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
    assert metrics["finish_reason"] == "stop"
    assert metrics["hit_max_tokens"] is False
    assert metrics["evaluated_prompt_tokens"] == 2
    assert metrics["reused_prompt_tokens"] == 5
    assert metrics["cache_reuse_observable"] is True


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
async def test_structured_generation_uses_json_schema_and_validates_response() -> None:
    captured: dict[str, object] = {}
    summary = {
        "conversation_progression": ["L'utilisateur rapporte X."],
        "user_stated_facts": ["X"],
        "user_interpretations": [],
        "assistant_proposals": [],
        "topics_discussed": ["X"],
        "open_questions": [],
        "decisions_or_actions": [],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(summary)}}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    ) as client:
        result = await LlamaCppBackend(Settings(), client).generate_structured(
            [LLMMessage(role=LLMRole.USER, content="Source")], RollingSummary
        )

    assert result == RollingSummary.model_validate(summary)
    assert captured["stream"] is False
    response_format = captured["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


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
