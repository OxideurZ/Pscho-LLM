import asyncio

import pytest

from backend.app.runs import RunRegistry, RunStatus
from backend.app.runs.registry import RunAlreadyFinishedError, RunNotFoundError


@pytest.mark.asyncio
async def test_registry_lifecycle_and_cleanup() -> None:
    registry = RunRegistry()
    run = await registry.create()

    assert run.id.startswith("run_")
    assert await registry.get(run.id) is run
    assert run.status is RunStatus.STARTING

    await registry.transition(run, RunStatus.GENERATING)
    await registry.transition(run, RunStatus.COMPLETE)
    with pytest.raises(RunAlreadyFinishedError):
        await registry.cancel(run.id)

    await registry.cleanup(run.id)
    assert len(registry) == 0
    with pytest.raises(RunNotFoundError):
        await registry.get(run.id)


@pytest.mark.asyncio
async def test_cancel_signals_and_cancels_task() -> None:
    registry = RunRegistry()
    run = await registry.create()
    run.task = asyncio.create_task(asyncio.sleep(60))

    await registry.cancel(run.id)

    assert run.cancel_event.is_set()
    with pytest.raises(asyncio.CancelledError):
        await run.task


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected() -> None:
    registry = RunRegistry()
    run = await registry.create()
    with pytest.raises(ValueError):
        await registry.transition(run, RunStatus.COMPLETE)
