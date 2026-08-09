"""Run the non-destructive Milestone E security checks available to the current user."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings


def command(name: str, *args: str) -> dict[str, object]:
    try:
        result = subprocess.run(
            [name, *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "UNKNOWN", "detail": str(error)}
    output = f"{result.stdout}\n{result.stderr}".lower()
    inaccessible = result.returncode == 2147749891 or (
        "access denied" in output or "accès refusé" in output or "zugriff verweigert" in output
    )
    return {
        "status": "UNKNOWN" if inaccessible else ("PASS" if result.returncode == 0 else "FAIL"),
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def unauthorized_api(base_url: str) -> dict[str, object]:
    request = Request(f"{base_url.rstrip('/')}/v1/conversations", method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return {
                "status": "FAIL",
                "http_status": response.status,
                "detail": "unauthorized request returned content",
            }
    except URLError as error:
        status = getattr(error, "code", None)
        if status in {401, 403}:
            return {"status": "PASS", "http_status": status}
        return {"status": "UNKNOWN", "detail": str(error)}


def browser_storage_audit(repo: Path) -> dict[str, object]:
    forbidden = []
    for path in (repo / "frontend" / "src").rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        for marker in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "CacheStorage",
            "caches.open",
        ):
            if marker in text:
                forbidden.append(f"{path}:{marker}")
    return {"status": "PASS" if not forbidden else "FAIL", "matches": forbidden}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    results: dict[str, object] = {
        "platform": sys.platform,
        "data_directory": str(settings.data_directory),
        "security_enabled": settings.security_enabled,
        "bitlocker": command("manage-bde", "-status", Path(settings.data_directory).drive or "C:"),
        "data_directory_acl": command("icacls", str(settings.data_directory)),
        "unauthorized_local_api": unauthorized_api(args.api),
        "browser_storage": browser_storage_audit(args.repo),
    }
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    hard_failures = [
        key
        for key in ("bitlocker", "unauthorized_local_api", "browser_storage")
        if results[key].get("status") == "FAIL"
    ]
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
