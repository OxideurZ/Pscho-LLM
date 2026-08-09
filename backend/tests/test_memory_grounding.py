import pytest

from backend.app.memory import (
    GroundingError,
    MemoryExtractionResult,
    canonicalize_source_spans,
)


def extraction_with_span(
    *, message_id: str = "target", start: int = 0, end: int = 4, quote: str = "test"
) -> MemoryExtractionResult:
    return MemoryExtractionResult.model_validate(
        {
            "schema_version": "1.0",
            "candidates": [
                {
                    "kind": "personal_fact",
                    "content": "L'utilisateur a fourni une information.",
                    "epistemic_status": "stated",
                    "source_spans": [
                        {
                            "message_id": message_id,
                            "start_char": start,
                            "end_char": end,
                            "quote": quote,
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


def test_exact_source_span_is_retained() -> None:
    result = canonicalize_source_spans(extraction_with_span(), {"target": "test source"}, "target")

    assert result.corrected_offset_count == 0
    assert result.extraction.candidates[0].source_spans[0].start_char == 0


def test_unique_exact_quote_repairs_only_model_offset_arithmetic() -> None:
    result = canonicalize_source_spans(
        extraction_with_span(start=0, end=3), {"target": "test source"}, "target"
    )

    span = result.extraction.candidates[0].source_spans[0]
    assert result.corrected_offset_count == 1
    assert (span.start_char, span.end_char) == (0, 4)


@pytest.mark.parametrize(
    ("source", "code"),
    [("different source", "SOURCE_SPAN_MISMATCH"), ("test and test", "SOURCE_SPAN_AMBIGUOUS")],
)
def test_missing_or_ambiguous_quote_is_rejected(source: str, code: str) -> None:
    with pytest.raises(GroundingError, match=code):
        canonicalize_source_spans(
            extraction_with_span(start=1, end=2), {"target": source}, "target"
        )


def test_target_message_must_support_every_candidate() -> None:
    with pytest.raises(GroundingError, match="TARGET_NOT_INCLUDED"):
        canonicalize_source_spans(
            extraction_with_span(message_id="context"),
            {"target": "target", "context": "test"},
            "target",
        )


def test_unknown_message_is_rejected() -> None:
    with pytest.raises(GroundingError, match="SOURCE_NOT_FOUND"):
        canonicalize_source_spans(
            extraction_with_span(message_id="missing"),
            {"target": "target"},
            "missing",
        )
