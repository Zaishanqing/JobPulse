from __future__ import annotations

import re
import unicodedata
from typing import Any

from .exceptions import InputFormatError


RAW_TEXT_FIELD_ALIASES = ("cv_text", "简历原文", "原始文本", "resume_text", "CV原文")

METADATA_FIELD_ALIASES = {
    "cv_name": ("name", "姓名", "cv_name", "candidate_name"),
    "cv_number": ("cv_number", "编号", "序号"),
}


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip()
    return text or None


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s_:\-]+", "", value).lower()


def _extract_metadata_value(row: dict, aliases: tuple[str, ...]) -> str | None:
    for field_name, raw_value in row.items():
        normalized_field_name = _normalize_key(str(field_name))
        if any(_normalize_key(alias) == normalized_field_name for alias in aliases):
            return _clean_value(raw_value)
    return None


def _extract_raw_text(row: dict) -> str | None:
    matched_fields = [
        field_name
        for field_name in row
        if _normalize_key(str(field_name)) in {_normalize_key(alias) for alias in RAW_TEXT_FIELD_ALIASES}
    ]
    if len(matched_fields) > 1:
        raise InputFormatError(f"Multiple raw text fields found: {matched_fields}")
    if not matched_fields:
        return None
    return _clean_value(row[matched_fields[0]])


def normalize_cv_text(value: str) -> str:
    """Normalize Unicode compatibility forms without filtering content."""
    return unicodedata.normalize("NFKC", value)


def build_source_blocks(cv_text: str) -> list[dict[str, int | str]]:
    blocks: list[dict[str, int | str]] = []
    for line_match in re.finditer(r"[^\r\n]+", cv_text):
        raw_line = line_match.group(0)
        for segment_match in re.finditer(r".+?(?:[。！？!?；;]+|$)", raw_line):
            raw_segment = segment_match.group(0)
            left_trimmed = len(raw_segment) - len(raw_segment.lstrip())
            text = raw_segment.strip()
            if not text:
                continue
            start = line_match.start() + segment_match.start() + left_trimmed
            blocks.append(
                {
                    "source_id": f"src_{len(blocks) + 1:04d}",
                    "text": text,
                    "start": start,
                    "end": start + len(text),
                }
            )
    return blocks


def preprocess_row(row: dict, row_index: int) -> tuple[dict | None, dict | None]:
    cv_id = _clean_value(row.get("cv_id")) or f"cv_{row_index:06d}"
    original_cv_text = _extract_raw_text(row)

    if original_cv_text is None:
        failed_case = {
            "cv_id": cv_id,
            "row_index": row_index,
            "error_type": "missing_required_input",
            "error_message": "CV raw text is missing for this row.",
            "missing_fields": ["简历原文"],
        }
        return None, failed_case

    cv_text = normalize_cv_text(original_cv_text)
    input_payload = {
        "cv_id": cv_id,
        "cv_text": cv_text,
        "cv_text_original": original_cv_text,
        "source_blocks": build_source_blocks(cv_text),
        "source_row": row,
    }
    return input_payload, None
