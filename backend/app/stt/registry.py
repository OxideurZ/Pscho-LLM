import asyncio
import shutil
import time
import wave
from pathlib import Path
from uuid import UUID

from backend.app.config.settings import Settings
from backend.app.stt.backend import STTBackend, STTError, STTSilenceError
from backend.app.stt.models import VoiceJob, VoiceJobStatus


class VoiceJobNotFoundError(KeyError):
    pass


class VoiceJobConflictError(RuntimeError):
    pass


class VoiceJobRegistry:
    """Ephemeral voice state. Audio and transcripts are never persisted to SQLite."""

    def __init__(self, settings: Settings, backend: STTBackend) -> None:
        self.settings = settings
        self.backend = backend
        self._jobs: dict[UUID, VoiceJob] = {}
        self._lock = asyncio.Lock()
        self._active_transcription: UUID | None = None
        self._directory = settings.data_directory / "runtime" / "voice-jobs"

    async def create(
        self, voice_input_id: UUID, conversation_id: str, client_turn_id: UUID
    ) -> VoiceJob:
        async with self._lock:
            if voice_input_id in self._jobs:
                raise VoiceJobConflictError()
            job_directory = self._directory / str(voice_input_id)
            job_directory.mkdir(parents=True, exist_ok=False)
            job = VoiceJob(
                voice_input_id=voice_input_id,
                conversation_id=conversation_id,
                client_turn_id=client_turn_id,
                audio_path=job_directory / "input.wav",
                started_at=time.monotonic(),
            )
            self._jobs[voice_input_id] = job
            return job

    async def append_chunk(self, voice_input_id: UUID, chunk: bytes) -> VoiceJob:
        async with self._lock:
            job = self._get(voice_input_id)
            if job.status is not VoiceJobStatus.RECORDING:
                raise VoiceJobConflictError()
            if job.audio_bytes + len(chunk) > self.settings.max_voice_audio_bytes:
                raise ValueError("VOICE_AUDIO_TOO_LARGE")
            with job.audio_path.open("ab") as target:
                target.write(chunk)
            job.audio_bytes += len(chunk)
            return job

    async def finalize(self, voice_input_id: UUID) -> VoiceJob:
        async with self._lock:
            job = self._get(voice_input_id)
            if job.status is not VoiceJobStatus.RECORDING:
                raise VoiceJobConflictError()
            if self._active_transcription is not None:
                raise VoiceJobConflictError()
            job.duration_ms = _wav_duration_ms(job.audio_path)
            if job.duration_ms > self.settings.max_recording_duration_seconds * 1000:
                await self._fail_and_cleanup(job, "VOICE_DURATION_EXCEEDED")
                return job
            if _is_silence(job.audio_path, self.settings.voice_silence_rms_threshold):
                await self._fail_and_cleanup(job, "STT_NO_SPEECH")
                return job
            job.status = VoiceJobStatus.TRANSCRIBING
            self._active_transcription = voice_input_id
            job.task = asyncio.create_task(
                self._transcribe(job), name=f"voice-stt-{voice_input_id}"
            )
            return job

    async def get(self, voice_input_id: UUID) -> VoiceJob:
        async with self._lock:
            return self._get(voice_input_id)

    async def cancel(self, voice_input_id: UUID) -> VoiceJob:
        async with self._lock:
            job = self._get(voice_input_id)
            if job.terminal:
                raise VoiceJobConflictError()
            job.status = VoiceJobStatus.CANCEL_REQUESTED
            if self._active_transcription == voice_input_id:
                await self.backend.cancel()
            await self._cancel_and_cleanup(job)
            return job

    async def _transcribe(self, job: VoiceJob) -> None:
        cancel_event = asyncio.Event()
        try:
            transcript = await self.backend.transcribe(job.audio_path, cancel_event)
            async with self._lock:
                if job.status is VoiceJobStatus.CANCEL_REQUESTED:
                    await self._cancel_and_cleanup(job)
                elif not transcript.strip():
                    await self._fail_and_cleanup(job, "STT_NO_SPEECH")
                else:
                    job.transcript = transcript
                    job.status = VoiceJobStatus.TRANSCRIPT_READY
                    self._cleanup_audio(job)
        except asyncio.CancelledError:
            async with self._lock:
                await self._cancel_and_cleanup(job)
        except STTSilenceError:
            async with self._lock:
                await self._fail_and_cleanup(job, "STT_NO_SPEECH")
        except STTError as error:
            async with self._lock:
                await self._fail_and_cleanup(job, error.code)
        except Exception:
            async with self._lock:
                await self._fail_and_cleanup(job, "STT_FAILED")
        finally:
            async with self._lock:
                if self._active_transcription == job.voice_input_id:
                    self._active_transcription = None

    def _get(self, voice_input_id: UUID) -> VoiceJob:
        try:
            return self._jobs[voice_input_id]
        except KeyError as error:
            raise VoiceJobNotFoundError(voice_input_id) from error

    async def _cancel_and_cleanup(self, job: VoiceJob) -> None:
        task = job.task
        if (
            isinstance(task, asyncio.Task)
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
        self._cleanup_audio(job)
        job.transcript = None
        job.status = VoiceJobStatus.CANCELLED
        if self._active_transcription == job.voice_input_id:
            self._active_transcription = None

    async def _fail_and_cleanup(self, job: VoiceJob, code: str) -> None:
        self._cleanup_audio(job)
        job.transcript = None
        job.error_code = code
        job.status = VoiceJobStatus.FAILED

    def _cleanup_audio(self, job: VoiceJob) -> None:
        shutil.rmtree(job.audio_path.parent, ignore_errors=True)


def _wav_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as source:
            return round(source.getnframes() * 1000 / source.getframerate())
    except (EOFError, wave.Error) as error:
        raise ValueError("VOICE_AUDIO_INVALID") from error


def _is_silence(path: Path, threshold: int) -> bool:
    with wave.open(str(path), "rb") as source:
        frames = source.readframes(source.getnframes())
        if source.getsampwidth() != 2 or not frames:
            return True
    samples = memoryview(frames).cast("h")
    average = sum(abs(sample) for sample in samples) / len(samples)
    return average < threshold
