from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    psych_local_host: str = "127.0.0.1"
    psych_local_port: int = 8000
    llama_server_url: str = "http://127.0.0.1:8080"
    app_version: str = "0.1.0-dev"
    database_path: Path = REPOSITORY_ROOT / "psych-local.db"
    prompt_id: str = "conversation_system"
    prompt_version: str = "0.1.0"
    model_name: str = "Qwen3.6-35B-A3B-Q4_K_M"
    model_path: Path = REPOSITORY_ROOT / "models/Qwen3.6-35B-A3B-Q4_K_M.gguf"
    model_expected_sha256: str = "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
    llama_cpp_version: str = "b9637"
    llama_cpp_build: str = "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3"
    default_temperature: float = Field(default=0.7, ge=0, le=2)
    default_top_p: float = Field(default=0.9, gt=0, le=1)
    default_max_tokens: int = Field(default=800, gt=0)
    default_seed: int = 42
    context_size: int = 32768
    backend_accelerator: str = "unknown"
    gpu_name: str = "unknown"
    gpu_vram_mb: int | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
