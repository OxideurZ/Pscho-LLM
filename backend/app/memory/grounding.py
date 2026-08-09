from dataclasses import dataclass

from backend.app.memory.models import MemoryExtractionResult


class GroundingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GroundingResult:
    extraction: MemoryExtractionResult
    corrected_offset_count: int


def _occurrences(text: str, quote: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        position = text.find(quote, start)
        if position < 0:
            return positions
        positions.append(position)
        start = position + 1


def canonicalize_source_spans(
    extraction: MemoryExtractionResult,
    source_texts: dict[str, str],
    target_message_id: str,
) -> GroundingResult:
    """Resolve exact quotes to canonical offsets without trusting model arithmetic.

    A model-provided offset is retained when it already selects the exact quote. Otherwise the
    quote may be resolved only when it occurs exactly once in the declared source. Missing or
    ambiguous quotes are rejected rather than guessed.
    """

    grounded = extraction.model_copy(deep=True)
    corrected = 0
    for candidate in grounded.candidates:
        if not any(span.message_id == target_message_id for span in candidate.source_spans):
            raise GroundingError("TARGET_NOT_INCLUDED")
        for span in candidate.source_spans:
            source_text = source_texts.get(span.message_id)
            if source_text is None:
                raise GroundingError("SOURCE_NOT_FOUND")
            if source_text[span.start_char : span.end_char] == span.quote:
                continue
            positions = _occurrences(source_text, span.quote)
            if not positions:
                raise GroundingError("SOURCE_SPAN_MISMATCH")
            if len(positions) > 1:
                raise GroundingError("SOURCE_SPAN_AMBIGUOUS")
            span.start_char = positions[0]
            span.end_char = positions[0] + len(span.quote)
            corrected += 1
    return GroundingResult(extraction=grounded, corrected_offset_count=corrected)
