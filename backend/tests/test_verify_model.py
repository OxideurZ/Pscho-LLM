from hashlib import sha256
from pathlib import Path

import pytest

from scripts.verify_model import calculate_sha256, verify


def test_model_hash_is_streamed_and_verified(tmp_path: Path) -> None:
    model = tmp_path / "tiny.gguf"
    content = b"GGUF-test-content" * 100
    model.write_bytes(content)
    expected = sha256(content).hexdigest()

    assert calculate_sha256(model, chunk_size=7) == expected
    assert verify(model, expected) == expected


def test_model_hash_mismatch_stops_the_workflow(tmp_path: Path) -> None:
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify(model, "0" * 64)
