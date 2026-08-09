import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    STARTING = "starting"
    GENERATING = "generating"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETE, self.CANCELLED, self.FAILED}


@dataclass
class ActiveRun:
    id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: RunStatus = RunStatus.STARTING
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    upstream_response: Any | None = None
