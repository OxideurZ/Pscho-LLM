import json

from backend.app.chat.sse import serialize_sse


def test_sse_serialization_preserves_unicode_and_frame_boundary() -> None:
    frame = serialize_sse("delta", {"text": "Écoute\nencore"})
    lines = frame.rstrip().splitlines()

    assert lines[0] == "event: delta"
    assert json.loads(lines[1].removeprefix("data: ")) == {"text": "Écoute\nencore"}
    assert frame.endswith("\n\n")
