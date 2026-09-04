from __future__ import annotations

import re
import unicodedata
from typing import Any

from .exceptions import InputFormatError
from .text_cleaning import clean_jd_text

RAW_TEXT_FIELD_ALIASES = ("jd_text", "原始文本", "JD原文")

METADATA_FIELD_ALIASES = {
    "job_title_raw": ("jobtitle", "jobtitleraw", "岗位名称", "职位名称", "招聘职位", "岗位title", "职位title"),
    "company": ("company", "companyname", "公司名称", "企业名称", "所属公司"),
    "region": ("region", "location", "工作地点", "城市", "地区", "地点"),
    "salary": ("salary", "薪资范围", "薪资水平", "薪酬范围"),
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


def extract_raw_text(row: dict) -> str | None:
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


def normalize_jd_text(value: str) -> str:
    """Normalize Unicode compatibility forms without filtering content."""
    return unicodedata.normalize("NFKC", value)


def build_source_blocks(jd_text: str) -> list[dict[str, int | str]]:
    blocks: list[dict[str, int | str]] = []
    for line_match in re.finditer(r"[^\r\n]+", jd_text):
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


def preprocess_row(
    row: dict,
    row_index: int,
    document_id: str | None = None,
) -> tuple[dict | None, dict | None]:
    jd_id = _clean_value(document_id) or _clean_value(row.get("jd_id")) or f"jd_{row_index:06d}"
    original_jd_text = extract_raw_text(row)

    if original_jd_text is None:
        failed_case = {
            "jd_id": jd_id,
            "row_index": row_index,
            "error_type": "missing_required_input",
            "error_message": "JD raw text is missing for this row.",
            "missing_fields": ["JD 原文"],
        }
        return None, failed_case

    jd_text = clean_jd_text(original_jd_text)
    input_payload = {
        "jd_id": jd_id,
        "job_title_raw": _extract_metadata_value(row, METADATA_FIELD_ALIASES["job_title_raw"]) or "未提及",
        "jd_text": jd_text,
        "jd_text_original": original_jd_text,
        "cleaned_text": jd_text,
        "source_blocks": build_source_blocks(jd_text),
        "company": _extract_metadata_value(row, METADATA_FIELD_ALIASES["company"]) or "未提及",
        "region": _extract_metadata_value(row, METADATA_FIELD_ALIASES["region"]) or "未提及",
        "salary": _extract_metadata_value(row, METADATA_FIELD_ALIASES["salary"]) or "未提及",
        "source_row": row,
    }
    return input_payload, None
