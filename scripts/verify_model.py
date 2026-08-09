#!/usr/bin/env python3
"""Verify a GGUF file before it is used for a reference benchmark."""

import argparse
import hashlib
import os
import sys
from pathlib import Path

CHUNK_SIZE = 8 * 1024 * 1024


def calculate_sha256(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model:
        while chunk := model.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Model file not found: {path}")
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("Expected SHA256 must contain exactly 64 hexadecimal characters")
    actual = calculate_sha256(path)
    if actual != expected:
        raise ValueError(f"SHA256 mismatch: expected {expected}, calculated {actual}")
    return actual


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "model",
        nargs="?",
        type=Path,
        default=os.getenv("MODEL_PATH"),
        help="GGUF path (or MODEL_PATH)",
    )
    parser.add_argument(
        "--expected",
        default=os.getenv("MODEL_EXPECTED_SHA256"),
        help="Expected SHA256 (or MODEL_EXPECTED_SHA256)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.model or not args.expected:
        print(
            "error: provide model path and --expected (or matching environment variables)",
            file=sys.stderr,
        )
        return 2
    try:
        actual = verify(Path(args.model), args.expected)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"OK {Path(args.model)} sha256={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
