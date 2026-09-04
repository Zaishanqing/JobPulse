from __future__ import annotations

from copy import deepcopy
import unicodedata
from typing import Any

from .exceptions import SourceBindingError
from .models import Evidence, JDExtractionResult


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


def canonicalize_evidence_quotes(
    payload: dict[str, Any],
    source_blocks: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Restore exact source spans only after a unique punctuation-insensitive match."""
    canonicalized = deepcopy(payload)
    blocks = {str(block.get("source_id")): str(block.get("text", "")) for block in source_blocks}
    corrections: list[dict[str, Any]] = []

    objects: list[tuple[str, dict[str, Any]]] = []
    job_title = canonicalized.get("job_title")
    if isinstance(job_title, dict):
        objects.append(("job_title", job_title))
    for collection in ("responsibilities", "requirements", "company_facts", "employment_facts"):
        values = canonicalized.get(collection)
        for index, item in enumerate(values if isinstance(values, list) else []):
            if isinstance(item, dict):
                objects.append((f"{collection}[{index}]", item))

    for path, item in objects:
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        quote = evidence.get("quote")
        source_text = blocks.get(str(evidence.get("source_id")))
        if not isinstance(quote, str) or source_text is None or quote in source_text:
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
        exact_quote = source_text[source_indices[start]:source_indices[start + len(quote_stream) - 1] + 1]
        evidence["quote"] = exact_quote
        corrections.append({
            "path": f"{path}.evidence.quote",
            "from": quote,
            "to": exact_quote,
            "authority": "unique_source_span",
        })
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


def align_all_evidence(
    result: JDExtractionResult,
    source_blocks: list[dict[str, int | str]],
) -> JDExtractionResult:
    blocks_by_id = {str(block["source_id"]): block for block in source_blocks}
    errors: list[dict] = []

    def collect(path: str, evidence: Evidence) -> None:
        try:
            _align_evidence(evidence, blocks_by_id)
        except SourceBindingError as exc:
            detail = dict(exc.details)
            detail["object_path"] = path
            errors.append(detail)

    if result.job_title is not None:
        collect("job_title", result.job_title.evidence)
    for index, requirement in enumerate(result.responsibilities):
        collect(f"responsibilities[{index}]", requirement.evidence)
    for index, requirement in enumerate(result.requirements):
        collect(f"requirements[{index}]", requirement.evidence)
    for index, fact in enumerate(result.company_facts):
        collect(f"company_facts[{index}]", fact.evidence)
    for index, fact in enumerate(result.employment_facts):
        collect(f"employment_facts[{index}]", fact.evidence)
    if errors:
        details: dict | list[dict] = errors[0] if len(errors) == 1 else errors
        raise SourceBindingError(
            f"{len(errors)} evidence object(s) failed exact source binding",
            details=details,
        )
    return result
