import platform
import subprocess
from pathlib import Path
from typing import Any

import psutil

from backend.app.config.settings import Settings


def git_commit(repository_root: Path) -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repository_root.as_posix()}",
                "rev-parse",
                "HEAD",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def runtime_info(settings: Settings) -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or "unknown",
        "ram_total_bytes": psutil.virtual_memory().total,
        "gpu": settings.gpu_name,
        "vram_mb": settings.gpu_vram_mb,
        "backend_accelerator": settings.backend_accelerator,
        "llama_cpp_reasoning": settings.llama_cpp_reasoning,
        "llama_server_url": settings.llama_server_url,
        "python": platform.python_version(),
    }
