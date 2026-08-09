"""Release model VRAM and stop the managed local application after an API response."""

from __future__ import annotations

import argparse
import time

from backend.app.config.settings import Settings
from scripts.launcher import Launcher, process_identity_matches, terminate_owned


def offload(settings: Settings) -> bool:
    launcher = Launcher(settings)
    instance = launcher.read_instance()
    if instance is None:
        return False

    llama_owned = process_identity_matches(instance.llama_pid, "llama-server")
    backend_owned = process_identity_matches(
        instance.backend_pid, "uvicorn", "backend.app.main:app"
    )
    llama_stopped = not llama_owned or terminate_owned(instance.llama_pid)
    if not llama_stopped:
        launcher.log("instance_state=offload_failed_llama_alive")
        return False

    # Remove state before stopping the backend because the helper may itself be
    # terminated as part of the backend process tree on Windows.
    launcher.instance_path.unlink(missing_ok=True)
    launcher.log("instance_state=offloaded model_vram=released")
    if backend_owned:
        terminate_owned(instance.backend_pid)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()
    if args.delay > 0:
        time.sleep(args.delay)
    return 0 if offload(Settings()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
