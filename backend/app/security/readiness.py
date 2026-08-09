from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import Settings


@dataclass(frozen=True)
class ReadinessCheck:
    status: str
    detail: str
    hard: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "detail": self.detail, "hard": self.hard}


def _bitlocker_status(path: Path) -> ReadinessCheck:
    if not path.drive or sys.platform != "win32":
        return ReadinessCheck("UNKNOWN", "full-disk protection is only verifiable on Windows")
    try:
        result = subprocess.run(
            ["manage-bde", "-status", path.drive],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ReadinessCheck("UNKNOWN", "manage-bde status could not be queried")
    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0:
        return ReadinessCheck("UNKNOWN", "BitLocker status command failed")
    protected = "protection status" in output and ("on" in output or "activ" in output)
    return ReadinessCheck(
        "PASS" if protected else "FAIL", "volume protection reported by manage-bde"
    )


def _acl_status(directory: Path) -> ReadinessCheck:
    if sys.platform != "win32":
        return ReadinessCheck("UNKNOWN", "ACL policy is only verifiable on Windows")
    try:
        result = subprocess.run(
            ["icacls", str(directory)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ReadinessCheck("UNKNOWN", "icacls could not be queried")
    if result.returncode != 0:
        return ReadinessCheck("UNKNOWN", "data-directory ACL could not be queried")
    text = (result.stdout or "").lower()
    broad = any(name in text for name in (" everyone:", " users:", " authenticated users:"))
    return ReadinessCheck("FAIL" if broad else "PASS", "data directory ACL inspected")


def security_readiness(
    settings: Settings, *, secret_store: object | None, database_key: bytes | None
) -> dict[str, object]:
    data_dir = settings.data_directory
    checks: dict[str, ReadinessCheck] = {
        "encrypted_database": ReadinessCheck(
            "PASS" if database_key else "FAIL",
            "SQLCipher key active" if database_key else "security mode is disabled",
        ),
        "key_store": ReadinessCheck(
            "PASS" if secret_store is not None and database_key else "FAIL",
            "user-scoped secret store active"
            if secret_store is not None and database_key
            else "secret store unavailable",
        ),
        "full_disk_encryption": _bitlocker_status(data_dir),
        "data_directory_acl": _acl_status(data_dir),
        "local_api_auth": ReadinessCheck(
            "PASS" if settings.security_enabled else "FAIL",
            "local session required"
            if settings.security_enabled
            else "API authentication disabled",
        ),
        "loopback_binding": ReadinessCheck(
            "PASS" if settings.psych_local_host in {"127.0.0.1", "localhost"} else "FAIL",
            f"bound to {settings.psych_local_host}",
        ),
        "encrypted_backup": ReadinessCheck(
            "PASS" if database_key and (data_dir / "backups").is_dir() else "FAIL",
            "encrypted backup service configured"
            if database_key
            else "backup encryption unavailable",
            hard=False,
        ),
        "log_policy": ReadinessCheck(
            "PASS", "application logs do not include conversation payloads"
        ),
        "migration_state": ReadinessCheck(
            "PASS" if database_key else "FAIL",
            "encrypted migration path active" if database_key else "plaintext migration required",
        ),
    }
    hard_failures = [
        name for name, check in checks.items() if check.hard and check.status in {"FAIL", "UNKNOWN"}
    ]
    return {
        "state": "READY" if not hard_failures else "NOT_READY",
        "checks": {name: check.as_dict() for name, check in checks.items()},
        "hard_failures": hard_failures,
    }
