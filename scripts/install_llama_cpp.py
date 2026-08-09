#!/usr/bin/env python3
"""Install the pinned llama.cpp release for Windows CUDA or macOS Apple Silicon."""

import argparse
import platform
import tarfile
import zipfile
from pathlib import Path

from scripts.download import download
from scripts.verify_model import verify

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/llama-cpp.lock"


def read_lock() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        root = destination.resolve()
        for member in bundle.infolist():
            if not (destination / member.filename).resolve().is_relative_to(root):
                raise ValueError(f"Unsafe archive member: {member.filename}")
        bundle.extractall(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["windows-cuda", "macos-arm64"])
    args = parser.parse_args()
    lock = read_lock()
    selected = args.platform
    if selected is None:
        selected = "windows-cuda" if platform.system() == "Windows" else "macos-arm64"
    version = lock["LLAMA_CPP_VERSION"]
    release_url = f"https://github.com/ggml-org/llama.cpp/releases/download/{version}"
    cache = ROOT / ".cache" / "llama.cpp" / version
    destination = ROOT / "tools" / f"llama.cpp-{version}"
    destination.mkdir(parents=True, exist_ok=True)

    if selected == "windows-cuda":
        artifacts = [
            (lock["WINDOWS_CUDA_ARCHIVE"], lock["WINDOWS_CUDA_SHA256"]),
            (lock["WINDOWS_CUDART_ARCHIVE"], lock["WINDOWS_CUDART_SHA256"]),
        ]
        for name, digest in artifacts:
            archive = cache / name
            if not archive.exists():
                download(f"{release_url}/{name}", archive)
            verify(archive, digest)
            safe_extract_zip(archive, destination)
        executable = next(destination.rglob("llama-server.exe"), None)
    else:
        name, digest = lock["MACOS_ARM64_ARCHIVE"], lock["MACOS_ARM64_SHA256"]
        archive = cache / name
        if not archive.exists():
            download(f"{release_url}/{name}", archive)
        verify(archive, digest)
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")
        executable = next(destination.rglob("llama-server"), None)

    if executable is None:
        raise FileNotFoundError("llama-server was not present in the verified release archives")
    print(f"llama-server ready: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
