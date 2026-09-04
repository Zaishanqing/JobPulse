from __future__ import annotations

from typing import Any, Iterator

from .exceptions import EvidenceAlignmentError
from .preprocess import normalize_cv_text


def _iter_evidence_payload(
    value: Any, path: str = ""
) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "evidence" and isinstance(child, dict):
                yield child_path, child
            else:
                yield from _iter_evidence_payload(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_evidence_payload(child, f"{path}[{index}]")


def inject_evidence_document_id(payload: Any, document_id: str) -> Any:
    if isinstance(payload, dict):
        for key, child in list(payload.items()):
            if key == "evidence" and isinstance(child, dict):
                child["source_document_id"] = document_id
            else:
                inject_evidence_document_id(child, document_id)
    elif isinstance(payload, list):
        for child in payload:
            inject_evidence_document_id(child, document_id)
    return payload


def _occurrence_positions(text: str, quote: str) -> list[int]:
    positions: list[int] = []
    cursor = 0
    while True:
        position = text.find(quote, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + max(1, len(quote))
    return positions


def validate_evidence_alignment(
    payload: Any,
    *,
    document_id: str,
    raw_text: str,
    source_blocks: list[dict[str, Any]],
) -> None:
    normalized_text = normalize_cv_text(raw_text)
    blocks_by_id = {
        str(block["source_id"]): block
        for block in source_blocks
    }
    for path, evidence in _iter_evidence_payload(payload):
        if evidence.get("source_document_id") != document_id:
            raise EvidenceAlignmentError(
                f"Evidence source_document_id mismatch at {path}"
            )
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote:
            raise EvidenceAlignmentError(f"Evidence quote is empty at {path}")
        alignment = evidence.get("alignment")
        if alignment in {"unresolved", "review_required"}:
            continue
        start = evidence.get("start")
        end = evidence.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            raise EvidenceAlignmentError(f"Evidence offsets are missing at {path}")
        if start < 0 or end < start or end > len(normalized_text):
            raise EvidenceAlignmentError(f"Evidence offsets are invalid at {path}")
        if alignment == "exact":
            if normalized_text[start:end] != quote:
                raise EvidenceAlignmentError(
                    f"Exact evidence slice does not match quote at {path}"
                )
            source_id = evidence.get("source_id")
            block = blocks_by_id.get(str(source_id)) if source_id is not None else None
            if block is None:
                raise EvidenceAlignmentError(
                    f"Evidence source_id has no source block at {path}"
                )
            block_start = int(block["start"])
            block_end = int(block["end"])
            if start < block_start or end > block_end:
                raise EvidenceAlignmentError(
                    f"Evidence offsets are outside the source block at {path}"
                )
            positions = _occurrence_positions(
                normalized_text[block_start:block_end],
                quote,
            )
            occurrence_index = evidence.get("occurrence_index")
            if not isinstance(occurrence_index, int) or not (
                0 <= occurrence_index < len(positions)
            ):
                raise EvidenceAlignmentError(
                    f"Evidence occurrence_index is out of range at {path}"
                )
            if positions[occurrence_index] != start - block_start:
                raise EvidenceAlignmentError(
                    f"Evidence occurrence_index is not reproducible at {path}"
                )
        else:
            raise EvidenceAlignmentError(
                f"Evidence alignment is not exact, unresolved, or review_required at {path}"
            )
