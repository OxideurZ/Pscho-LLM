import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class STTError(RuntimeError):
    code = "STT_FAILED"


class STTUnavailableError(STTError):
    code = "STT_UNAVAILABLE"


class STTSilenceError(STTError):
    code = "STT_NO_SPEECH"


@dataclass(frozen=True)
class STTModelInfo:
    backend_name: str
    model_name: str
    available: bool


class STTBackend(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: Path, cancel_event: asyncio.Event) -> str: ...

    @abstractmethod
    async def health(self) -> STTModelInfo: ...

    @abstractmethod
    async def cancel(self) -> None: ...


class WhisperCppBackend(STTBackend):
    """One cancellable whisper-cli child per active VoiceJob."""

    def __init__(self, executable: Path, model: Path) -> None:
        self.executable = executable
        self.model = model
        self._process: asyncio.subprocess.Process | None = None
        self._process_lock = asyncio.Lock()

    async def health(self) -> STTModelInfo:
        return STTModelInfo("whisper.cpp", "Whisper Large-v3-Turbo", self._available())

    async def cancel(self) -> None:
        async with self._process_lock:
            if self._process is not None and self._process.returncode is None:
                self._process.kill()

    async def transcribe(self, audio_path: Path, cancel_event: asyncio.Event) -> str:
        if not self._available():
            raise STTUnavailableError()
        command = [
            str(self.executable),
            "-m",
            str(self.model),
            "-f",
            str(audio_path),
            "-l",
            "fr",
            "-np",
            "-nt",
        ]
        async with self._process_lock:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            process = self._process
        process_task = asyncio.create_task(process.communicate())
        cancelled_task = asyncio.create_task(cancel_event.wait())
        done, _pending = await asyncio.wait(
            {process_task, cancelled_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancelled_task in done:
            await self.cancel()
            await process_task
            raise asyncio.CancelledError
        cancelled_task.cancel()
        stdout, _stderr = await process_task
        async with self._process_lock:
            self._process = None
        if process.returncode != 0:
            raise STTError()
        # stdout is kept in memory only. whisper-cli does not write a transcript file with -np.
        return "\n".join(
            line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()
        )

    def _available(self) -> bool:
        return self.executable.is_file() and self.model.is_file()
