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
from base64 import urlsafe_b64encode
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import psutil

from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.security import WindowsDpapiSecretStore
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


def reachable(url: str, timeout: float = 2) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local URLs only
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
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def process_identity_matches(pid: int, *tokens: str) -> bool:
    """Check command-line identity before treating a PID as ours.

    A PID in instance.json is advisory only: Windows may reuse it after a
    crash. This is intentionally conservative when process inspection fails.
    """
    try:
        command = " ".join(psutil.Process(pid).cmdline()).lower()
    except (psutil.Error, OSError):
        return False
    return all(token.lower() in command for token in tokens)


def discover_processes(*tokens: str) -> list[int]:
    """Find root processes for a role, started from this repository."""
    candidates: list[tuple[int, int]] = []
    expected_cwd = REPOSITORY_ROOT.resolve()
    for process in psutil.process_iter(["pid", "ppid", "cmdline", "cwd"]):
        try:
            command = [str(argument).lower() for argument in process.info["cmdline"] or []]
            cwd = Path(process.info["cwd"] or "").resolve()
            exact_tokens = {token.lower() for token in tokens}
            executable_stem = Path(command[0]).stem if command else ""
            matches = all(
                token in command or (token == "llama-server" and executable_stem == token)
                for token in exact_tokens
            )
            if cwd == expected_cwd and matches:
                candidates.append((process.info["pid"], process.info["ppid"]))
        except (psutil.Error, OSError):
            continue
    candidate_pids = {pid for pid, _ in candidates}
    return [pid for pid, parent in candidates if parent not in candidate_pids]


def discover_process(*tokens: str) -> int | None:
    roots = discover_processes(*tokens)
    return roots[0] if len(roots) == 1 else None


def terminate_owned(pid: int) -> bool:
    if not pid_alive(pid):
        return True
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return False
    else:
        os.kill(pid, 15)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


class Launcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.runtime = settings.data_directory / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.logs = settings.data_directory / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
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
        backend_url = local_url(self.settings.psych_local_host, instance.backend_port, "/v1/live")
        llama_url = f"http://{self.settings.psych_local_host}:{instance.llama_port}/health"
        valid = (
            instance.backend_port == self.backend_port
            and instance.llama_port == self.llama_port
            and pid_alive(instance.backend_pid)
            and pid_alive(instance.llama_pid)
            and process_identity_matches(instance.backend_pid, "uvicorn", "backend.app.main:app")
            and process_identity_matches(instance.llama_pid, "llama-server")
            and reachable(backend_url, timeout=5)
            and reachable(llama_url, timeout=5)
        )
        if not valid:
            self.log("instance_state=stale")
        return valid

    def recover_healthy_instance(self) -> Instance | None:
        backend_pid = discover_process("uvicorn", "backend.app.main:app")
        llama_pid = discover_process("llama-server", str(self.settings.model_path))
        if backend_pid is None or llama_pid is None:
            return None
        instance = Instance(
            str(uuid.uuid4()),
            datetime.now(UTC).isoformat(),
            os.getpid(),
            backend_pid,
            llama_pid,
            None,
            self.backend_port,
            self.llama_port,
        )
        self.instance_path.write_text(json.dumps(asdict(instance), indent=2), encoding="utf-8")
        return instance

    def cleanup_owned_processes(self) -> None:
        pids = {
            *discover_processes("uvicorn", "backend.app.main:app"),
            *discover_processes("llama-server", str(self.settings.model_path)),
        }
        if not pids:
            return
        print("Nettoyage d’une instance Psych-local incomplète…")
        if not all(terminate_owned(pid) for pid in pids):
            raise RuntimeError("Impossible d’arrêter complètement l’ancienne instance Psych-local.")
        self.instance_path.unlink(missing_ok=True)
        self.log("instance_state=partial_owned_cleanup")

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
        if not self.settings.whisper_cpp_path.is_file():
            raise FileNotFoundError(
                "whisper-cli introuvable : "
                f"{self.settings.whisper_cpp_path}. Corrigez WHISPER_CPP_PATH."
            )
        if not self.settings.whisper_model_path.is_file():
            raise FileNotFoundError(
                "Modèle STT attendu absent : "
                f"{self.settings.whisper_model_path}. "
                "Aucun téléchargement automatique n’est effectué."
            )
        verify(self.settings.whisper_model_path, self.settings.whisper_model_expected_sha256)

    def assert_ports_available(self) -> None:
        if not port_is_free(self.settings.psych_local_host, self.backend_port):
            raise RuntimeError(
                f"Port backend {self.backend_port} occupé par un processus tiers ou inconnu ; "
                "aucun processus n’a été arrêté."
            )
        if not port_is_free(self.settings.psych_local_host, self.llama_port):
            raise RuntimeError(
                f"Port moteur {self.llama_port} occupé par un processus tiers ou inconnu ; "
                "aucun processus n’a été arrêté."
            )

    def start(self, open_browser: bool) -> None:
        if self.healthy_instance():
            self.log("instance_state=existing_healthy")
            print("Psych-local est déjà démarré et opérationnel.")
            if open_browser:
                webbrowser.open(self.browser_url())
            return
        # A healthy local API plus a healthy engine is a stronger identity signal
        # than a stale/missing PID file. Do not create a duplicate stack.
        if reachable(
            local_url(self.settings.psych_local_host, self.backend_port, "/v1/live")
        ) and reachable(f"{self.settings.llama_server_url}/health"):
            if self.recover_healthy_instance() is not None:
                self.log("instance_state=recovered_healthy")
                print("Psych-local est déjà démarré et opérationnel.")
                if open_browser:
                    webbrowser.open(self.browser_url())
                return
        self.cleanup_owned_processes()
        self.clear_stale()
        print("Vérification de l’installation et du modèle…")
        self.verify_installation()
        self.assert_ports_available()
        llama: subprocess.Popen[bytes] | None = None
        backend: subprocess.Popen[bytes] | None = None
        try:
            print("Chargement du modèle local… Cette étape peut prendre environ une minute.")
            with (self.logs / "llama-server.log").open("ab", buffering=0) as llama_log:
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
                    stdout=llama_log,
                    stderr=subprocess.STDOUT,
                )
            self.log(f"llama_state=started pid={llama.pid}")
            wait_ready(
                f"{self.settings.llama_server_url}/health",
                self.settings.launcher_engine_timeout_seconds,
                "Le moteur local",
            )
            print("Démarrage de l’application…")
            with (self.logs / "backend.log").open("ab", buffering=0) as backend_log:
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
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                )
            self.log(f"backend_state=started pid={backend.pid}")
            wait_ready(
                local_url(self.settings.psych_local_host, self.backend_port, "/v1/live"),
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
            print(
                "Psych-local est prêt. Ouverture du navigateur…"
                if open_browser
                else "Psych-local est prêt."
            )
            if open_browser:
                webbrowser.open(self.browser_url())
        except Exception:
            if backend is not None:
                terminate_owned(backend.pid)
            if llama is not None:
                terminate_owned(llama.pid)
            self.log("instance_state=start_failed_cleanup=attempted")
            raise

    def browser_url(self) -> str:
        root = local_url(self.settings.psych_local_host, self.backend_port, "/")
        if not self.settings.security_enabled:
            return root
        launch_secret = WindowsDpapiSecretStore(self.settings.data_directory / "security").get(
            self.settings.local_access_secret_name
        )
        bootstrap_token = urlsafe_b64encode(launch_secret).rstrip(b"=").decode("ascii")
        return f"{root}#bootstrap={bootstrap_token}"

    def stop(self) -> None:
        instance = self.read_instance()
        if instance is None:
            print("Aucune instance Psych-local gérée n’est active.")
            return
        # A recorded PID plus command identity proves ownership. Requiring a responding
        # health endpoint here would leave a wedged owned process running indefinitely.
        backend_owned = process_identity_matches(
            instance.backend_pid, "uvicorn", "backend.app.main:app"
        )
        llama_owned = process_identity_matches(instance.llama_pid, "llama-server")
        backend_stopped = not backend_owned or terminate_owned(instance.backend_pid)
        llama_stopped = not llama_owned or terminate_owned(instance.llama_pid)
        if not backend_stopped or not llama_stopped:
            self.log("instance_state=stop_failed_process_still_alive")
            raise RuntimeError(
                "Arrêt local non confirmé ; l’état d’instance est conservé pour reprise."
            )
        self.instance_path.unlink(missing_ok=True)
        self.log("instance_state=stopped")
        print("Psych-local est arrêté. La RAM et la VRAM ont été libérées.")


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
