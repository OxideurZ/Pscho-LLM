#!/usr/bin/env python3
"""Download (with resume) and verify the canonical Milestone A GGUF."""

import argparse
from pathlib import Path

from scripts.download import download
from scripts.verify_model import verify

MODEL_REPOSITORY = "ggml-org/Qwen3.6-35B-A3B-GGUF"
MODEL_FILENAME = "Qwen3.6-35B-A3B-Q4_K_M.gguf"
MODEL_SIZE = 20_419_565_568
MODEL_SHA256 = "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
MODEL_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/main/{MODEL_FILENAME}?download=true"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("models") / MODEL_FILENAME)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and not args.destination.exists():
        download(MODEL_URL, args.destination, MODEL_SIZE)
    actual = verify(args.destination, MODEL_SHA256)
    print(f"Model ready: {args.destination} sha256={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
