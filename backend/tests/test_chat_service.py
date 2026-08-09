import asyncio
import json
from pathlib import Path

import pytest

from backend.app.chat import ChatService
from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.llm.models import GenerationOptions, LLMMessage, LLMRole
from backend.app.runs import RunRegistry, RunRepository
from backend.tests.fakes import FakeBackend


def event_data(frame: str) -> dict[str, str]:
    return json.loads(frame.split("data: ", 1)[1])


@pytest.mark.asyncio
async def test_cancellation_closes_producer_and_emits_terminal_event(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    await Database(path, REPOSITORY_ROOT / "migrations").migrate()
    registry = RunRegistry()
    backend = FakeBackend("slow")
    service = ChatService(
        Settings(database_path=path),
        backend,
        registry,
        RunRepository(path),
        REPOSITORY_ROOT,
    )

    async def connected() -> bool:
        return False

    stream = service.stream(
        [LLMMessage(role=LLMRole.USER, content="Longue réponse")],
        GenerationOptions(),
        connected,
    )
    started = await anext(stream)
    run_id = event_data(started)["run_id"]
    terminal = asyncio.create_task(anext(stream))
    for _ in range(100):
        if (await registry.get(run_id)).status.value == "generating":
            break
        await asyncio.sleep(0.001)
    await registry.cancel(run_id)

    assert (await terminal).startswith("event: cancelled")
    assert backend.closed
    assert await RunRepository(path).get(run_id) is not None
    assert (await RunRepository(path).get(run_id))["status"] == "cancelled"
    await stream.aclose()


@pytest.mark.asyncio
async def test_client_disconnect_cancels_run_and_cleans_registry(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    await Database(path, REPOSITORY_ROOT / "migrations").migrate()
    registry = RunRegistry()
    service = ChatService(
        Settings(database_path=path),
        FakeBackend("slow"),
        registry,
        RunRepository(path),
        REPOSITORY_ROOT,
    )

    async def disconnected() -> bool:
        return True

    stream = service.stream(
        [LLMMessage(role=LLMRole.USER, content="Déconnexion")],
        GenerationOptions(),
        disconnected,
    )
    run_id = event_data(await anext(stream))["run_id"]
    assert (await anext(stream)).startswith("event: cancelled")
    await stream.aclose()

    assert len(registry) == 0
    assert (await RunRepository(path).get(run_id))["status"] == "cancelled"
