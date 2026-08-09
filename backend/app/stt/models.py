from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID


class VoiceJobStatus(StrEnum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    TRANSCRIPT_READY = "transcript_ready"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class VoiceJob:
    voice_input_id: UUID
    conversation_id: str
    client_turn_id: UUID
    audio_path: Path
    started_at: float
    audio_bytes: int = 0
    duration_ms: int | None = None
    status: VoiceJobStatus = VoiceJobStatus.RECORDING
    transcript: str | None = None
    error_code: str | None = None
    task: object | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {
            VoiceJobStatus.TRANSCRIPT_READY,
            VoiceJobStatus.CANCELLED,
            VoiceJobStatus.FAILED,
        }
