import asyncio
import wave
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.config import Settings
from backend.app.stt.backend import STTBackend, STTModelInfo
from backend.app.stt.models import VoiceJobStatus
from backend.app.stt.registry import VoiceJobConflictError, VoiceJobRegistry


class FakeSTT(STTBackend):
    def __init__(self, transcript: str = "dictée synthétique") -> None:
        self.transcript = transcript
        self.cancelled = False

    async def transcribe(self, audio_path: Path, cancel_event: asyncio.Event) -> str:
        if self.transcript == "wait":
            await asyncio.Event().wait()
        return self.transcript

    async def health(self) -> STTModelInfo:
        return STTModelInfo("fake", "fake", True)

    async def cancel(self) -> None:
        self.cancelled = True


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_directory=tmp_path / "runtime",
        database_path=tmp_path / "app.sqlite",
        model_expected_sha256="a" * 64,
    )


def wav_chunk(amplitude: int = 1_000, frames: int = 1_600, click: bool = False) -> bytes:
    path = Path("audio.wav")
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        samples = [amplitude] * frames
        if click:
            samples[0] = 12_000
        target.writeframes(
            b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)
        )
    try:
        return path.read_bytes()
    finally:
        path.unlink()


@pytest.mark.asyncio
async def test_voice_job_keeps_audio_ephemeral_and_returns_transcript(tmp_path: Path) -> None:
    registry = VoiceJobRegistry(settings_for(tmp_path), FakeSTT())
    voice_input_id = uuid4()
    job = await registry.create(voice_input_id, "conversation-a", uuid4())
    await registry.append_chunk(voice_input_id, wav_chunk())
    job = await registry.finalize(voice_input_id)
    assert job.status is VoiceJobStatus.TRANSCRIBING

    for _ in range(20):
        job = await registry.get(voice_input_id)
        if job.terminal:
            break
        await asyncio.sleep(0.01)

    assert job.status is VoiceJobStatus.TRANSCRIPT_READY
    assert job.transcript == "dictée synthétique"
    assert not job.audio_path.parent.exists()

    cancelled = await registry.cancel(voice_input_id)
    assert cancelled.status is VoiceJobStatus.CANCELLED
    assert cancelled.transcript is None


@pytest.mark.asyncio
async def test_silence_never_creates_a_transcript(tmp_path: Path) -> None:
    registry = VoiceJobRegistry(settings_for(tmp_path), FakeSTT())
    voice_input_id = uuid4()
    await registry.create(voice_input_id, "conversation-a", uuid4())
    await registry.append_chunk(voice_input_id, wav_chunk(amplitude=0))
    job = await registry.finalize(voice_input_id)
    assert job.status is VoiceJobStatus.FAILED
    assert job.error_code == "STT_NO_SPEECH"
    assert job.transcript is None


@pytest.mark.asyncio
@pytest.mark.parametrize("amplitude,click", [(20, False), (45, False), (0, True)])
async def test_low_energy_noise_never_creates_a_transcript(
    tmp_path: Path, amplitude: int, click: bool
) -> None:
    registry = VoiceJobRegistry(settings_for(tmp_path), FakeSTT("hallucinated words"))
    voice_input_id = uuid4()
    await registry.create(voice_input_id, "conversation-a", uuid4())
    await registry.append_chunk(voice_input_id, wav_chunk(amplitude=amplitude, click=click))

    job = await registry.finalize(voice_input_id)

    assert job.status is VoiceJobStatus.FAILED
    assert job.error_code == "STT_NO_SPEECH"
    assert job.transcript is None


@pytest.mark.asyncio
async def test_cancel_terminates_active_voice_job_and_releases_slot(tmp_path: Path) -> None:
    backend = FakeSTT("wait")
    registry = VoiceJobRegistry(settings_for(tmp_path), backend)
    voice_input_id = uuid4()
    await registry.create(voice_input_id, "conversation-a", uuid4())
    await registry.append_chunk(voice_input_id, wav_chunk())
    await registry.finalize(voice_input_id)
    job = await registry.cancel(voice_input_id)
    assert backend.cancelled
    assert job.status is VoiceJobStatus.CANCELLED
    assert not job.audio_path.parent.exists()

    next_id = uuid4()
    await registry.create(next_id, "conversation-b", uuid4())
    await registry.append_chunk(next_id, wav_chunk())
    assert (await registry.finalize(next_id)).status is VoiceJobStatus.TRANSCRIBING
    await registry.cancel(next_id)


@pytest.mark.asyncio
async def test_second_transcription_is_rejected_while_first_is_active(tmp_path: Path) -> None:
    registry = VoiceJobRegistry(settings_for(tmp_path), FakeSTT("wait"))
    first_id, second_id = uuid4(), uuid4()
    await registry.create(first_id, "conversation-a", uuid4())
    await registry.append_chunk(first_id, wav_chunk())
    await registry.finalize(first_id)
    await registry.create(second_id, "conversation-b", uuid4())
    await registry.append_chunk(second_id, wav_chunk())
    with pytest.raises(VoiceJobConflictError):
        await registry.finalize(second_id)
    await registry.cancel(first_id)


@pytest.mark.asyncio
async def test_shutdown_abandons_incomplete_jobs_and_removes_ephemeral_audio(
    tmp_path: Path,
) -> None:
    backend = FakeSTT("wait")
    registry = VoiceJobRegistry(settings_for(tmp_path), backend)
    voice_input_id = uuid4()
    job = await registry.create(voice_input_id, "conversation-a", uuid4())
    await registry.append_chunk(voice_input_id, wav_chunk())
    await registry.finalize(voice_input_id)

    await registry.shutdown()

    assert backend.cancelled
    assert not job.audio_path.parent.exists()
