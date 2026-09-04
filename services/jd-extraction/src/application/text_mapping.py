from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

from ..models import Evidence, JDExtractionResult
from ..preprocess import normalize_jd_text


class EvidenceMappingError(ValueError):
    """Raised when normalized Evidence cannot be mapped to one exact raw span."""


@dataclass(frozen=True)
class _NormalizationUnit:
    raw_start: int
    raw_end: int
    model_text: str


class NFKCTextMap:
    """Exact boundary map between raw text and the Pipeline's NFKC model text."""

    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.model_text = normalize_jd_text(raw_text)
        self._boundaries = self._build_boundaries()

    def _build_boundaries(self) -> dict[int, list[int]]:
        units = [
            _NormalizationUnit(index, index + 1, normalize_jd_text(character))
            for index, character in enumerate(self.raw_text)
        ]
        index = 0
        while index + 1 < len(units):
            left = units[index]
            right = units[index + 1]
            combined_text = normalize_jd_text(self.raw_text[left.raw_start : right.raw_end])
            if left.model_text + right.model_text != combined_text:
                units[index : index + 2] = [
                    _NormalizationUnit(left.raw_start, right.raw_end, combined_text)
                ]
                index = max(0, index - 1)
            else:
                index += 1

        if "".join(unit.model_text for unit in units) != self.model_text:
            raise EvidenceMappingError("Unable to build an exact NFKC boundary map.")

        boundaries: dict[int, list[int]] = {0: [0]}
        model_offset = 0
        for unit in units:
            boundaries.setdefault(model_offset, []).append(unit.raw_start)
            model_offset += len(unit.model_text)
            boundaries.setdefault(model_offset, []).append(unit.raw_end)
        if model_offset != len(self.model_text):
            raise EvidenceMappingError("NFKC boundary map length does not match model text.")
        return {offset: sorted(set(raw_offsets)) for offset, raw_offsets in boundaries.items()}

    def remap_evidence(self, evidence: Evidence) -> None:
        start = evidence.start
        end = evidence.end
        if (
            evidence.alignment != "exact"
            or start is None
            or end is None
            or start < 0
            or end < start
            or end > len(self.model_text)
            or self.model_text[start:end] != evidence.quote
        ):
            raise EvidenceMappingError("Pipeline Evidence is not exact for the NFKC model text.")

        candidates = {
            (raw_start, raw_end)
            for raw_start in self._boundaries.get(start, [])
            for raw_end in self._boundaries.get(end, [])
            if 0 <= raw_start <= raw_end <= len(self.raw_text)
            and normalize_jd_text(self.raw_text[raw_start:raw_end]) == evidence.quote
        }
        if len(candidates) != 1:
            raise EvidenceMappingError("NFKC Evidence does not have one exact raw-text span.")
        raw_start, raw_end = candidates.pop()
        object.__setattr__(evidence, "start", raw_start)
        object.__setattr__(evidence, "end", raw_end)
        object.__setattr__(evidence, "quote", self.raw_text[raw_start:raw_end])
        object.__setattr__(evidence, "alignment", "exact")
        occurrence_starts = _non_overlapping_occurrence_starts(
            self.raw_text, evidence.quote
        )
        try:
            object.__setattr__(
                evidence, "occurrence_index", occurrence_starts.index(raw_start)
            )
        except ValueError as exc:
            raise EvidenceMappingError(
                "Remapped Evidence start is not an exact non-overlapping occurrence."
            ) from exc
        Evidence.model_validate(evidence.model_dump())


def _non_overlapping_occurrence_starts(text: str, quote: str) -> list[int]:
    if not quote:
        raise EvidenceMappingError("Evidence quote must not be empty.")
    starts: list[int] = []
    offset = 0
    while True:
        start = text.find(quote, offset)
        if start < 0:
            return starts
        starts.append(start)
        offset = start + len(quote)


def _all_evidence(result: JDExtractionResult) -> Iterable[Evidence]:
    if result.job_title is not None:
        yield result.job_title.evidence
    for item in result.responsibilities:
        yield item.evidence
    for item in result.requirements:
        yield item.evidence
    for item in result.company_facts:
        yield item.evidence
    for item in result.employment_facts:
        yield item.evidence


def remap_extraction_evidence(
    result: JDExtractionResult,
    text_map: NFKCTextMap,
) -> JDExtractionResult:
    remapped = deepcopy(result)
    for evidence in _all_evidence(remapped):
        text_map.remap_evidence(evidence)
        if not (
            evidence.alignment == "exact"
            and evidence.start is not None
            and evidence.end is not None
            and 0 <= evidence.start <= evidence.end <= len(text_map.raw_text)
            and text_map.raw_text[evidence.start : evidence.end] == evidence.quote
        ):
            raise EvidenceMappingError("Remapped Evidence is not exact for raw text.")
    return remapped
