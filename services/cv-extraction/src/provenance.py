from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterator
import unicodedata

from pydantic import BaseModel

from .exceptions import SourceBindingError
from .models import CVExtractionResult, Evidence


def _search_stream(value: str) -> tuple[str, list[int]]:
    stream: list[str] = []
    source_indices: list[int] = []
    for source_index, character in enumerate(value):
        for normalized in unicodedata.normalize("NFKC", character).casefold():
            if unicodedata.category(normalized)[0] in {"P", "Z"} or normalized.isspace():
                continue
            stream.append(normalized)
            source_indices.append(source_index)
    return "".join(stream), source_indices


def _iter_payload_evidence(value: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "evidence" and isinstance(child, dict):
                yield child_path, child
            else:
                yield from _iter_payload_evidence(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_payload_evidence(child, f"{path}[{index}]")


def collect_payload_evidence_binding_errors(
    payload: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect exact-binding failures without requiring a schema-valid model."""
    blocks = {str(block["source_id"]): str(block["text"]) for block in source_blocks}
    errors: list[dict[str, Any]] = []
    for path, evidence in _iter_payload_evidence(payload):
        source_id = evidence.get("source_id")
        quote = evidence.get("quote")
        if not isinstance(source_id, str) or not isinstance(quote, str):
            continue
        source_text = blocks.get(source_id)
        object_path = path.removesuffix(".evidence")
        if source_text is None:
            errors.append(
                {
                    "code": "unknown_source_id",
                    "source_id": source_id,
                    "object_path": object_path,
                }
            )
        elif quote not in source_text:
            errors.append(
                {
                    "code": "quote_not_exact_substring",
                    "source_id": source_id,
                    "invalid_quote": quote,
                    "exact_source_text": source_text,
                    "instruction": "evidence.quote must be copied character-for-character from exact_source_text",
                    "object_path": object_path,
                }
            )
    return errors


def canonicalize_evidence_quotes(
    payload: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Restore exact source spans only after a unique punctuation-insensitive match."""
    canonicalized = deepcopy(payload)
    blocks = {str(block["source_id"]): str(block["text"]) for block in source_blocks}
    corrections: list[dict[str, Any]] = []

    for path, evidence in _iter_payload_evidence(canonicalized):
        quote = evidence.get("quote")
        source_text = blocks.get(str(evidence.get("source_id")))
        if not isinstance(quote, str) or source_text is None or quote in source_text:
            continue
        source_stream, _ = _search_stream(source_text)
        if len(source_stream) >= 3 and source_text in quote:
            evidence["quote"] = source_text
            corrections.append(
                {
                    "path": f"{path}.quote",
                    "from": quote,
                    "to": source_text,
                    "authority": "source_block_containment",
                }
            )
            continue
        quote_stream, _ = _search_stream(quote)
        source_stream, source_indices = _search_stream(source_text)
        if len(quote_stream) < 3:
            continue
        starts: list[int] = []
        cursor = 0
        while True:
            start = source_stream.find(quote_stream, cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + 1
        if len(starts) != 1:
            continue
        start = starts[0]
        exact_quote = source_text[
            source_indices[start]:source_indices[start + len(quote_stream) - 1] + 1
        ]
        evidence["quote"] = exact_quote
        corrections.append(
            {
                "path": f"{path}.quote",
                "from": quote,
                "to": exact_quote,
                "authority": "unique_source_span",
            }
        )
    return canonicalized, corrections


def _align_evidence(evidence: Evidence, blocks_by_id: dict[str, dict[str, int | str]]) -> None:
    block = blocks_by_id.get(evidence.source_id)
    if block is None:
        raise SourceBindingError(
            f"Unknown evidence source_id: {evidence.source_id}",
            details={"code": "unknown_source_id", "source_id": evidence.source_id},
        )
    text = str(block["text"])
    positions: list[int] = []
    cursor = 0
    while True:
        position = text.find(evidence.quote, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + max(1, len(evidence.quote))
    if not positions:
        raise SourceBindingError(
            f"Evidence quote is not an exact continuous substring: source_id={evidence.source_id}, "
            f"quote={evidence.quote!r}",
            details={
                "code": "quote_not_exact_substring",
                "source_id": evidence.source_id,
                "invalid_quote": evidence.quote,
                "exact_source_text": text,
                "instruction": "evidence.quote must be copied character-for-character from exact_source_text",
            },
        )
    selected_index = evidence.occurrence_index or 0
    if selected_index < 0 or selected_index >= len(positions):
        raise SourceBindingError(
            f"Evidence occurrence_index out of range: source_id={evidence.source_id}, "
            f"occurrence_index={selected_index}, occurrences={len(positions)}",
            details={
                "code": "occurrence_index_out_of_range",
                "source_id": evidence.source_id,
                "occurrence_index": selected_index,
                "occurrences": len(positions),
                "exact_source_text": text,
            },
        )
    relative_start = positions[selected_index]
    object.__setattr__(evidence, "start", int(block["start"]) + relative_start)
    object.__setattr__(evidence, "end", evidence.start + len(evidence.quote))
    object.__setattr__(evidence, "alignment", "exact")
    object.__setattr__(evidence, "occurrence_index", selected_index)
    Evidence.model_validate(evidence.model_dump())


def _iter_model_evidence(value: Any, path: str = "") -> Iterator[tuple[str, Evidence]]:
    if isinstance(value, Evidence):
        yield path, value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            child = getattr(value, field_name)
            child_path = f"{path}.{field_name}" if path else field_name
            yield from _iter_model_evidence(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_model_evidence(child, f"{path}[{index}]")


def align_all_evidence(
    result: CVExtractionResult,
    source_blocks: list[dict[str, int | str]],
) -> CVExtractionResult:
    blocks_by_id = {str(block["source_id"]): block for block in source_blocks}
    errors: list[dict[str, Any]] = []
    for path, evidence in _iter_model_evidence(result):
        try:
            _align_evidence(evidence, blocks_by_id)
        except SourceBindingError as exc:
            detail = dict(exc.details)
            detail["object_path"] = path.removesuffix(".evidence")
            errors.append(detail)
    if errors:
        raise SourceBindingError(
            f"{len(errors)} evidence object(s) failed exact source binding",
            details=errors[0] if len(errors) == 1 else errors,
        )
    return result
