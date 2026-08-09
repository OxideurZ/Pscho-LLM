"""Small resumable downloader shared by local runtime preparation scripts."""

import os
import sys
import urllib.request
from pathlib import Path

CHUNK_SIZE = 8 * 1024 * 1024


def download(url: str, destination: Path, expected_size: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    downloaded = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "Psych-local/0.1"})
    if downloaded:
        request.add_header("Range", f"bytes={downloaded}-")

    with urllib.request.urlopen(request, timeout=60) as response:
        append = downloaded > 0 and response.status == 206
        if not append:
            downloaded = 0
        mode = "ab" if append else "wb"
        with partial.open(mode) as output:
            while chunk := response.read(CHUNK_SIZE):
                output.write(chunk)
                downloaded += len(chunk)
                total = expected_size or 0
                progress = f" / {total / 1_000_000_000:.2f} GB" if total else ""
                print(
                    f"\rDownloaded {downloaded / 1_000_000_000:.2f} GB{progress}",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    print(file=sys.stderr)
    if expected_size is not None and downloaded != expected_size:
        raise ValueError(f"Size mismatch: expected {expected_size}, downloaded {downloaded}")
    os.replace(partial, destination)
    return destination
