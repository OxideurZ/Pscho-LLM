import pytest
from pydantic import ValidationError

from backend.app.memory import MemoryExtractionResult, MemorySourceSpan


def test_memory_extraction_schema_accepts_only_f_kinds_and_statuses() -> None:
    result = MemoryExtractionResult.model_validate(
        {
            "schema_version": "1.0",
            "candidates": [
                {
                    "kind": "belief",
                    "content": "L'utilisateur se considère comme mauvais en maths.",
                    "epistemic_status": "stated",
                    "source_spans": [
                        {
                            "message_id": "msg-1",
                            "start_char": 0,
                            "end_char": 21,
                            "quote": "Je suis nul en maths.",
                        }
                    ],
                    "time": {
                        "text": None,
                        "start_at": None,
                        "end_at": None,
                        "precision": None,
                    },
                    "entities": [],
                }
            ],
        }
    )

    assert result.candidates[0].kind == "belief"
    assert result.candidates[0].epistemic_status == "stated"


@pytest.mark.parametrize("field,value", [("kind", "diagnosis"), ("epistemic_status", "0.92")])
def test_memory_extraction_schema_rejects_out_of_scope_labels(field: str, value: str) -> None:
    candidate = {
        "kind": "belief",
        "content": "L'utilisateur exprime une croyance.",
        "epistemic_status": "stated",
        "source_spans": [{"message_id": "msg-1", "start_char": 0, "end_char": 4, "quote": "test"}],
        "time": {"text": None, "start_at": None, "end_at": None, "precision": None},
        "entities": [],
    }
    candidate[field] = value

    with pytest.raises(ValidationError):
        MemoryExtractionResult.model_validate({"schema_version": "1.0", "candidates": [candidate]})


def test_source_span_requires_forward_bounds() -> None:
    with pytest.raises(ValidationError):
        MemorySourceSpan(message_id="msg-1", start_char=4, end_char=4, quote="x")


def test_memory_extraction_schema_caps_candidates() -> None:
    candidate = {
        "kind": "goal",
        "content": "L'utilisateur veut courir un trail.",
        "epistemic_status": "stated",
        "source_spans": [{"message_id": "msg-1", "start_char": 0, "end_char": 5, "quote": "trail"}],
        "time": {"text": None, "start_at": None, "end_at": None, "precision": None},
        "entities": [],
    }

    with pytest.raises(ValidationError):
        MemoryExtractionResult.model_validate(
            {"schema_version": "1.0", "candidates": [candidate] * 9}
        )
