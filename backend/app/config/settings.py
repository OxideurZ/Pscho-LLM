import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def default_data_directory() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "PsychLocal"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PsychLocal"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "PsychLocal"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    psych_local_host: str = "127.0.0.1"
    psych_local_port: int = 8000
    llama_server_url: str = "http://127.0.0.1:8080"
    llama_server_path: Path = REPOSITORY_ROOT / "tools" / "llama.cpp-b9637" / "llama-server.exe"
    app_version: str = "0.1.0-dev"
    frontend_dist: Path = REPOSITORY_ROOT / "frontend" / "dist"
    data_directory: Path = Field(default_factory=default_data_directory)
    database_path: Path = Field(
        default_factory=lambda: default_data_directory() / "data" / "app.sqlite"
    )
    sqlite_busy_timeout_ms: int = Field(default=30_000, ge=1)
    session_timeout_seconds: int = Field(default=30 * 60, ge=1)
    stream_checkpoint_seconds: float = Field(default=1.0, gt=0)
    stream_checkpoint_characters: int = Field(default=512, ge=1)
    context_safety_margin_tokens: int = Field(default=512, ge=0)
    summary_budget_tokens: int = Field(default=4096, ge=256)
    recent_raw_budget_tokens: int = Field(default=24_000, ge=256)
    embedding_model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_model_path: Path = Field(
        default_factory=lambda: (
            default_data_directory() / "models" / "retrieval" / "Qwen3-Embedding-0.6B"
        )
    )
    embedding_model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    embedding_model_expected_sha256: str = (
        "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
    )
    reranker_model_name: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_model_path: Path = Field(
        default_factory=lambda: (
            default_data_directory() / "models" / "retrieval" / "Qwen3-Reranker-0.6B"
        )
    )
    reranker_model_revision: str = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
    reranker_model_expected_sha256: str = (
        "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"
    )
    retrieval_device: str = "cpu"
    retrieval_embedding_dimensions: int = Field(default=1024, ge=32, le=1024)
    retrieval_embedding_dtype: str = "float32"
    retrieval_rerank_top_k: int = Field(default=8, ge=1, le=32)
    retrieval_interactive_timeout_ms: int = Field(default=6000, ge=100, le=30_000)
    summary_prompt_id: str = "rolling_summary"
    summary_prompt_version: str = "0.1.1"
    memory_extraction_prompt_id: str = "memory_extraction"
    memory_extraction_prompt_version: str = "0.1.2"
    memory_enabled: bool = True
    memory_background_idle_seconds: int = Field(default=120, ge=1)
    memory_job_max_attempts: int = Field(default=3, ge=1, le=10)
    memory_previous_user_context: int = Field(default=4, ge=0, le=4)
    memory_backfill_enqueue_batch_size: int = Field(default=25, ge=1, le=50)
    memory_debug_tools_enabled: bool = False
    prompt_id: str = "conversation_system"
    prompt_version: str = "0.1.2"
    model_name: str = "Qwen3.6-35B-A3B-Q4_K_M"
    model_path: Path = REPOSITORY_ROOT / "models/Qwen3.6-35B-A3B-Q4_K_M.gguf"
    model_expected_sha256: str = "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
    llama_cpp_version: str = "b9637"
    llama_cpp_build: str = "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3"
    llama_cpp_reasoning: str = "off"
    default_temperature: float = Field(default=0.7, ge=0, le=2)
    default_top_p: float = Field(default=0.9, gt=0, le=1)
    default_max_tokens: int = Field(default=800, gt=0)
    default_seed: int = 42
    context_size: int = 32768
    backend_accelerator: str = "unknown"
    gpu_name: str = "unknown"
    gpu_vram_mb: int | None = None
    launcher_engine_timeout_seconds: int = Field(default=120, ge=1)
    launcher_backend_timeout_seconds: int = Field(default=45, ge=1)
    max_recording_duration_seconds: int = Field(default=15 * 60, ge=1, le=15 * 60)
    max_voice_audio_bytes: int = Field(default=128 * 1024 * 1024, ge=1)
    voice_silence_rms_threshold: int = Field(default=80, ge=0)
    whisper_cpp_path: Path = (
        REPOSITORY_ROOT / "tools" / "whisper.cpp-v1.9.2-cublas-12.4" / "Release" / "whisper-cli.exe"
    )
    whisper_model_path: Path = Field(
        default_factory=lambda: (
            default_data_directory()
            / "stt"
            / "whisper.cpp-large-v3-turbo"
            / "ggml-large-v3-turbo.bin"
        )
    )
    whisper_model_expected_sha256: str = (
        "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
    )
    security_enabled: bool = True
    database_key_name: str = "database-encryption"
    local_access_secret_name: str = "local-access"
    backup_retention_count: int = Field(default=7, ge=1)
    backup_interval_seconds: int = Field(default=6 * 60 * 60, ge=60)


@lru_cache
def get_settings() -> Settings:
    return Settings()
