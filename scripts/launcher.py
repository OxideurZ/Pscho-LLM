"""Windows-local lifecycle coordinator for Psych-local.

It deliberately only finds, validates, starts, polls and stops processes.  The
FastAPI application remains the authority for migrations and conversation
reconciliation.  No conversation payload is written to launcher logs.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.request import urlopen

from backend.app.config.settings import REPOSITORY_ROOT, Settings
from scripts.verify_model import verify


@dataclass(frozen=True)
class Instance:
    instance_id: str
    started_at: str
    launcher_pid: int
    backend_pid: int
    llama_pid: int
    frontend_pid: int | None
    backend_port: int
    llama_port: int
    frontend_port: int | None = None


def port_from_url(url: str) -> int:
    return int(url.rsplit(":", 1)[1].split("/", 1)[0])


def local_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def reachable(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - local URLs only
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def wait_ready(url: str, timeout: int, component: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if reachable(url):
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"{component} n’est pas prêt après {timeout}s. Consultez la configuration et les prérequis."
    )


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # On Windows this asks the OS; on POSIX it is equally harmless.
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_owned(pid: int) -> None:
    if not pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.kill(pid, 15)


class Launcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.runtime = settings.data_directory / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.instance_path = self.runtime / "instance.json"
        self.log_path = self.runtime / "launcher.log"
        self.backend_port = settings.psych_local_port
        self.llama_port = port_from_url(settings.llama_server_url)

    def log(self, message: str) -> None:
        # Only lifecycle metadata is admitted here: never requests, responses or summaries.
        line = f"{datetime.now(UTC).isoformat()} {message}\n"
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(line)

    def read_instance(self) -> Instance | None:
        if not self.instance_path.is_file():
            return None
        try:
            return Instance(**json.loads(self.instance_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            self.log("instance_state=invalid")
            return None

    def healthy_instance(self) -> bool:
        instance = self.read_instance()
        if instance is None:
            return False
        backend_url = local_url(self.settings.psych_local_host, instance.backend_port, "/v1/health")
        valid = (
            instance.backend_port == self.backend_port
            and instance.llama_port == self.llama_port
            and pid_alive(instance.backend_pid)
            and pid_alive(instance.llama_pid)
            and reachable(backend_url)
        )
        if not valid:
            self.log("instance_state=stale")
        return valid

    def clear_stale(self) -> None:
        if self.instance_path.exists() and not self.healthy_instance():
            self.instance_path.unlink(missing_ok=True)

    def verify_installation(self) -> None:
        if not self.settings.llama_server_path.is_file():
            raise FileNotFoundError(
                "llama-server introuvable : "
                f"{self.settings.llama_server_path}. "
                "Lancez .\\setup.ps1 ou corrigez LLAMA_SERVER_PATH."
            )
        if not self.settings.model_path.is_file():
            raise FileNotFoundError(
                f"Modèle attendu absent : {self.settings.model_name} à {self.settings.model_path}. "
                f"SHA attendu : {self.settings.model_expected_sha256}. "
                "Aucun téléchargement automatique n’est effectué."
            )
        verify(self.settings.model_path, self.settings.model_expected_sha256)

    def assert_ports_available(self) -> None:
        backend_health = local_url(self.settings.psych_local_host, self.backend_port, "/v1/health")
        if not port_is_free(self.settings.psych_local_host, self.backend_port) and not reachable(
            backend_health
        ):
            raise RuntimeError(
                f"Port backend {self.backend_port} occupé par un processus tiers ou inconnu ; "
                "aucun processus n’a été arrêté."
            )
        if not port_is_free(self.settings.psych_local_host, self.llama_port) and not reachable(
            f"{self.settings.llama_server_url}/health"
        ):
            raise RuntimeError(
                f"Port moteur {self.llama_port} occupé par un processus tiers ou inconnu ; "
                "aucun processus n’a été arrêté."
            )

    def start(self, open_browser: bool) -> None:
        if self.healthy_instance():
            self.log("instance_state=existing_healthy")
            if open_browser:
                webbrowser.open(local_url(self.settings.psych_local_host, self.backend_port, "/"))
            return
        self.clear_stale()
        self.verify_installation()
        self.assert_ports_available()
        llama: subprocess.Popen[bytes] | None = None
        backend: subprocess.Popen[bytes] | None = None
        try:
            llama = subprocess.Popen(
                [
                    str(self.settings.llama_server_path),
                    "-m",
                    str(self.settings.model_path),
                    "--alias",
                    self.settings.model_name,
                    "--host",
                    self.settings.psych_local_host,
                    "--port",
                    str(self.llama_port),
                    "-c",
                    str(self.settings.context_size),
                    "--parallel",
                    "1",
                    "--jinja",
                    "--reasoning",
                    self.settings.llama_cpp_reasoning,
                ],
                cwd=REPOSITORY_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log(f"llama_state=started pid={llama.pid}")
            wait_ready(
                f"{self.settings.llama_server_url}/health",
                self.settings.launcher_engine_timeout_seconds,
                "Le moteur local",
            )
            backend = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    self.settings.psych_local_host,
                    "--port",
                    str(self.backend_port),
                    "--no-access-log",
                ],
                cwd=REPOSITORY_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log(f"backend_state=started pid={backend.pid}")
            wait_ready(
                local_url(self.settings.psych_local_host, self.backend_port, "/v1/health"),
                self.settings.launcher_backend_timeout_seconds,
                "Le backend",
            )
            instance = Instance(
                str(uuid.uuid4()),
                datetime.now(UTC).isoformat(),
                os.getpid(),
                backend.pid,
                llama.pid,
                None,
                self.backend_port,
                self.llama_port,
            )
            self.instance_path.write_text(json.dumps(asdict(instance), indent=2), encoding="utf-8")
            self.log("instance_state=ready")
            if open_browser:
                webbrowser.open(local_url(self.settings.psych_local_host, self.backend_port, "/"))
        except Exception:
            if backend is not None:
                terminate_owned(backend.pid)
            if llama is not None:
                terminate_owned(llama.pid)
            self.log("instance_state=start_failed_cleanup=attempted")
            raise

    def stop(self) -> None:
        instance = self.read_instance()
        if instance is None:
            print("Aucune instance Psych-local gérée n’est active.")
            return
        # Require both identity signals (recorded PID and local health) before owning a process.
        if reachable(
            local_url(self.settings.psych_local_host, instance.backend_port, "/v1/health")
        ):
            terminate_owned(instance.backend_pid)
        if reachable(f"http://{self.settings.psych_local_host}:{instance.llama_port}/health"):
            terminate_owned(instance.llama_pid)
        self.instance_path.unlink(missing_ok=True)
        self.log("instance_state=stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Psych-local lifecycle coordinator")
    parser.add_argument("command", choices=("start", "stop", "status"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    launcher = Launcher(Settings())
    try:
        if args.command == "start":
            launcher.start(open_browser=not args.no_browser)
        elif args.command == "stop":
            launcher.stop()
        else:
            print("READY" if launcher.healthy_instance() else "NOT_READY")
        return 0
    except Exception as error:
        print(f"Psych-local: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
