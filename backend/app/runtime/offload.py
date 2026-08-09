from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend.app.config.settings import REPOSITORY_ROOT, Settings


class RuntimeNotManagedError(RuntimeError):
    pass


class RuntimeOffloadService:
    """Schedule a detached helper that releases all model processes after the HTTP response."""

    def __init__(self, settings: Settings, repository_root: Path = REPOSITORY_ROOT) -> None:
        self.settings = settings
        self.repository_root = repository_root

    def schedule(self) -> None:
        instance_path = self.settings.data_directory / "runtime" / "instance.json"
        if not instance_path.is_file():
            raise RuntimeNotManagedError()
        creation_flags = 0
        start_new_session = sys.platform != "win32"
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [sys.executable, "-m", "scripts.offload_runtime", "--delay", "0.5"],
            cwd=self.repository_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
            start_new_session=start_new_session,
        )
