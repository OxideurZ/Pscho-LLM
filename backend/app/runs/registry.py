import asyncio
from contextlib import suppress
from uuid import uuid4

from backend.app.runs.models import ActiveRun, RunStatus


class RunNotFoundError(KeyError):
    pass


class RunAlreadyFinishedError(RuntimeError):
    pass


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def create(self, run_id: str | None = None) -> ActiveRun:
        run = ActiveRun(id=run_id or f"run_{uuid4().hex}")
        async with self._lock:
            if run.id in self._runs:
                raise ValueError(f"Run already registered: {run.id}")
            self._runs[run.id] = run
        return run

    async def get(self, run_id: str) -> ActiveRun:
        async with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as error:
                raise RunNotFoundError(run_id) from error

    async def cancel(self, run_id: str) -> ActiveRun:
        run = await self.get(run_id)
        if run.status.terminal:
            raise RunAlreadyFinishedError(run_id)
        run.cancel_event.set()
        response = run.upstream_response
        if response is not None and hasattr(response, "aclose"):
            with suppress(Exception):
                await response.aclose()
        if run.task is not None and not run.task.done():
            run.task.cancel()
        return run

    async def transition(self, run: ActiveRun, status: RunStatus) -> None:
        allowed = {
            RunStatus.STARTING: {RunStatus.GENERATING, RunStatus.CANCELLED, RunStatus.FAILED},
            RunStatus.GENERATING: {RunStatus.COMPLETE, RunStatus.CANCELLED, RunStatus.FAILED},
        }
        if status not in allowed.get(run.status, set()):
            raise ValueError(f"Invalid run transition: {run.status} -> {status}")
        run.status = status

    async def cleanup(self, run_id: str) -> None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.status.terminal:
                del self._runs[run_id]

    def __len__(self) -> int:
        return len(self._runs)
