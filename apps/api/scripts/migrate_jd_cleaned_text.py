"""Deterministic migration to canonical cleaned JD text (no model calls).

For every JD it computes the canonical cleaned text, stores it, and remaps all
extraction Evidence coordinates from the original raw text to the cleaned text.
Any JD that cannot be remapped exactly is written to a review report instead of
being mutated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import create_database
from app.domain.text_cleaning import clean_jd_text
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult


def _non_overlapping_occurrences(text: str, quote: str) -> list[int]:
    starts: list[int] = []
    offset = 0
    while True:
        start = text.find(quote, offset)
        if start < 0:
            return starts
        starts.append(start)
        offset = start + len(quote)


def _remap_evidence(raw_text: str, cleaned_text: str, evidence: dict) -> str | None:
    alignment = evidence.get("alignment")
    start = evidence.get("start")
    end = evidence.get("end")
    quote = evidence.get("quote")
    if alignment != "exact" or not isinstance(start, int) or not isinstance(end, int):
        return None
    if not isinstance(quote, str) or raw_text[start:end] != quote:
        return "evidence_span_mismatch"
    cleaned_quote = clean_jd_text(quote)
    if cleaned_quote != quote:
        starts = _non_overlapping_occurrences(cleaned_text, cleaned_quote)
        occurrence_index = evidence.get("occurrence_index")
        if isinstance(occurrence_index, int) and occurrence_index < len(starts):
            new_start = starts[occurrence_index]
        elif len(starts) == 1:
            new_start = starts[0]
        else:
            return "cleaned_quote_ambiguous"
        evidence["start"] = new_start
        evidence["end"] = new_start + len(cleaned_quote)
        evidence["quote"] = cleaned_quote
        return None
    starts = _non_overlapping_occurrences(cleaned_text, quote)
    occurrence_index = evidence.get("occurrence_index")
    if isinstance(occurrence_index, int) and occurrence_index < len(starts):
        new_start = starts[occurrence_index]
    elif len(starts) == 1:
        new_start = starts[0]
    elif start < len(cleaned_text) and cleaned_text[start:end] == quote:
        new_start = start
    else:
        return "cleaned_quote_not_found"
    evidence["start"] = new_start
    evidence["end"] = new_start + len(quote)
    return None


def _walk_evidence(payload, raw_text: str, cleaned_text: str, failures: list[str]) -> None:
    if isinstance(payload, dict):
        if {"start", "end", "quote"}.issubset(payload):
            error = _remap_evidence(raw_text, cleaned_text, payload)
            if error is not None:
                failures.append(error)
        for value in payload.values():
            _walk_evidence(value, raw_text, cleaned_text, failures)
    elif isinstance(payload, list):
        for value in payload:
            _walk_evidence(value, raw_text, cleaned_text, failures)


_SKIP_SEMANTIC_KEYS = frozenset(
    {
        "evidence",
        "requirement_id",
        "fact_id",
        "source_id",
        "document_id",
        "occurrence_index",
        "id",
        "skill_id",
    }
)


def _clean_semantic_values(payload) -> bool:
    """Clean watermark artifacts from semantic string fields, not identifiers."""
    changed = False
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _SKIP_SEMANTIC_KEYS:
                continue
            if isinstance(value, str):
                cleaned = clean_jd_text(value)
                if cleaned != value:
                    payload[key] = cleaned
                    changed = True
            elif _clean_semantic_values(value):
                changed = True
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            if isinstance(value, str):
                cleaned = clean_jd_text(value)
                if cleaned != value:
                    payload[index] = cleaned
                    changed = True
            elif _clean_semantic_values(value):
                changed = True
    return changed


def migrate() -> dict:
    database = create_database(settings.DATABASE_URL)
    report_path = (
        Path(__file__).resolve().parents[1] / "data" / "jd_cleaned_text_migration_report.jsonl"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("", encoding="utf-8")
    summary = {"changed": 0, "unchanged": 0, "failed": 0}
    with database.session_factory() as session:
        rows = session.query(JobDescription).order_by(JobDescription.id).all()
        for jd in rows:
            cleaned = clean_jd_text(jd.raw_text)
            if jd.cleaned_text == cleaned:
                if cleaned == jd.raw_text:
                    summary["unchanged"] += 1
                else:
                    summary["changed"] += 1
                continue
            parsed = (
                session.query(JDParseResult)
                .filter(JDParseResult.jd_id == jd.id)
                .first()
            )
            extraction_candidate = (
                deepcopy(parsed.extraction_result)
                if parsed is not None and parsed.extraction_result
                else None
            )
            normalized_candidate = (
                deepcopy(parsed.normalized_result)
                if parsed is not None and parsed.normalized_result
                else None
            )
            if cleaned == jd.raw_text:
                extraction_changed = (
                    _clean_semantic_values(extraction_candidate)
                    if extraction_candidate is not None
                    else False
                )
                normalized_changed = (
                    _clean_semantic_values(normalized_candidate)
                    if normalized_candidate is not None
                    else False
                )
                if parsed is not None and (extraction_changed or normalized_changed):
                    parsed.extraction_result = extraction_candidate
                    parsed.normalized_result = normalized_candidate
                    flag_modified(parsed, "extraction_result")
                    flag_modified(parsed, "normalized_result")
                jd.cleaned_text = jd.raw_text
                summary["unchanged"] += 1
                continue
            failures: list[str] = []
            json_changed = False
            if extraction_candidate is not None:
                _walk_evidence(extraction_candidate, jd.raw_text, cleaned, failures)
                json_changed = (
                    _clean_semantic_values(extraction_candidate) or json_changed
                )
            if normalized_candidate is not None:
                _walk_evidence(normalized_candidate, jd.raw_text, cleaned, failures)
                json_changed = (
                    _clean_semantic_values(normalized_candidate) or json_changed
                )
            if failures:
                summary["failed"] += 1
                report_path.open("a", encoding="utf-8").write(
                    json.dumps(
                        {
                            "jd_id": jd.id,
                            "status": "failed",
                            "failures": failures,
                            "cleaned_text_preview": cleaned[:200],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                continue
            if parsed is not None and json_changed:
                parsed.extraction_result = extraction_candidate
                parsed.normalized_result = normalized_candidate
                flag_modified(parsed, "extraction_result")
                flag_modified(parsed, "normalized_result")
            jd.cleaned_text = cleaned
            summary["changed"] += 1
            report_path.open("a", encoding="utf-8").write(
                json.dumps(
                    {
                        "jd_id": jd.id,
                        "status": "changed",
                        "cleaned_text_preview": cleaned[:200],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        session.commit()
    database.dispose()
    return summary


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=False))
