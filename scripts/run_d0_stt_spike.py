"""Measure exactly the two STT candidates mandated by Milestone D0.

The output contains operational metrics only.  It deliberately never writes
transcripts, raw audio or recognized segments to a file or log.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil


@dataclass
class SpikeResult:
    candidate: str
    audio_file: str
    audio_duration_ms: int
    audio_bytes: int
    status: str
    stt_ms: int
    rtf: float
    transcript_characters: int
    segment_count: int
    process_ram_peak_bytes: int
    gpu_peak_mib: int | None
    gpu_system_peak_mib: int | None
    error_code: str | None = None


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as source:
        return round(source.getnframes() * 1000 / source.getframerate())


def gpu_mib_for(pid: int) -> int | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    result = subprocess.run(
        [executable, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode:
        return None
    for row in result.stdout.splitlines():
        columns = [column.strip() for column in row.split(",")]
        if len(columns) == 2 and columns[0] == str(pid):
            try:
                return int(columns[1])
            except ValueError:
                return None
    return 0


def gpu_mib_total() -> int | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    result = subprocess.run(
        [executable, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode:
        return None
    try:
        return sum(int(row.strip()) for row in result.stdout.splitlines() if row.strip())
    except ValueError:
        return None


class ResourceSampler:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.ram_peak = self.process.memory_info().rss
        self.gpu_peak = gpu_mib_for(self.process.pid)
        self.gpu_system_peak = gpu_mib_total()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _sample(self) -> None:
        while self._running:
            self.ram_peak = max(self.ram_peak, self.process.memory_info().rss)
            observed_gpu = gpu_mib_for(self.process.pid)
            if observed_gpu is not None:
                self.gpu_peak = max(self.gpu_peak or 0, observed_gpu)
            observed_system_gpu = gpu_mib_total()
            if observed_system_gpu is not None:
                self.gpu_system_peak = max(self.gpu_system_peak or 0, observed_system_gpu)
            time.sleep(0.2)


def faster_whisper(audio: Path, model: Path, device: str, compute_type: str) -> tuple[int, int]:
    from faster_whisper import WhisperModel

    engine = WhisperModel(str(model), device=device, compute_type=compute_type)
    segments, _info = engine.transcribe(
        str(audio), language="fr", beam_size=5, vad_filter=False, condition_on_previous_text=False
    )
    count = 0
    characters = 0
    for segment in segments:
        count += 1
        characters += len(segment.text)
    del engine
    return characters, count


def whisper_cpp(audio: Path, executable: Path, model: Path) -> tuple[int, int]:
    command = [
        str(executable),
        "-m",
        str(model),
        "-f",
        str(audio),
        "-l",
        "fr",
        "-np",
        "-nt",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise RuntimeError("WHISPER_CPP_TRANSCRIPTION_FAILED")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sum(len(line) for line in lines), len(lines)


def run(args: argparse.Namespace) -> SpikeResult:
    audio = args.audio.resolve()
    duration_ms = wav_duration_ms(audio)
    sampler = ResourceSampler()
    started = time.perf_counter()
    sampler.start()
    try:
        if args.candidate == "faster-whisper":
            chars, segments = faster_whisper(
                audio, args.faster_model.resolve(), args.device, args.compute_type
            )
        else:
            chars, segments = whisper_cpp(
                audio, args.whisper_cpp.resolve(), args.whisper_model.resolve()
            )
        status, error_code = "complete", None
    except Exception as error:  # The report exposes only a stable code, never content.
        chars, segments, status, error_code = 0, 0, "failed", type(error).__name__.upper()
    finally:
        sampler.stop()
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return SpikeResult(
        candidate=args.candidate,
        audio_file=audio.name,
        audio_duration_ms=duration_ms,
        audio_bytes=audio.stat().st_size,
        status=status,
        stt_ms=elapsed_ms,
        rtf=round(elapsed_ms / duration_ms, 4),
        transcript_characters=chars,
        segment_count=segments,
        process_ram_peak_bytes=sampler.ram_peak,
        gpu_peak_mib=sampler.gpu_peak,
        gpu_system_peak_mib=sampler.gpu_system_peak,
        error_code=error_code,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", choices=("faster-whisper", "whisper.cpp"))
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--faster-model", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--whisper-cpp", type=Path)
    parser.add_argument("--whisper-model", type=Path)
    args = parser.parse_args()
    if args.candidate == "faster-whisper" and args.faster_model is None:
        parser.error("--faster-model is required for faster-whisper")
    if args.candidate == "whisper.cpp" and (args.whisper_cpp is None or args.whisper_model is None):
        parser.error("--whisper-cpp and --whisper-model are required for whisper.cpp")
    print(json.dumps(asdict(run(args)), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
