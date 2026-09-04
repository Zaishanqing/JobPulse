from base64 import b64encode
from io import BytesIO
import json
from math import isfinite

from openpyxl import Workbook

from app.domain.jd import Document, Evidence, NormalizationResult
from app.domain.json_types import (
    FrozenJsonArray,
    FrozenJsonObject,
    JsonValue,
    MutableJsonObject,
    MutableJsonValue,
    freeze_json,
    thaw_json,
    thaw_json_object,
)

ExcelCell = str | int | float | bool | None

WORKSHEETS = (
    "document_summary", "responsibilities", "requirements", "skills",
    "company_facts", "employment_facts", "skill_normalization", "salary",
    "review_flags",
)


def _evidence_columns(evidence: Evidence) -> MutableJsonObject:
    return {
        "evidence_source_id": evidence.source_id,
        "evidence_quote": evidence.quote,
        "evidence_start": evidence.start,
        "evidence_end": evidence.end,
        "evidence_alignment": evidence.alignment,
        "evidence_occurrence_index": evidence.occurrence_index,
    }


def to_export_rows(
    document: Document, normalization: NormalizationResult
) -> dict[str, list[MutableJsonObject]]:
    versions = {
        "schema_version": document.contract_version,
        "normalization_schema_version": normalization.contract_version,
    }
    requirement_rows: list[MutableJsonObject] = []
    skill_rows: list[MutableJsonObject] = []
    for item in document.requirements:
        raw_payload = thaw_json_object(item.raw_payload)
        requirement_rows.append(
            {
                "requirement_id": item.requirement_id, "kind": item.kind,
                "modality": item.modality, **versions, **raw_payload,
                **_evidence_columns(item.evidence),
            }
        )
        for skill in raw_payload.get("items", []):
            if not isinstance(skill, dict):
                raise ValueError("requirement raw_payload.items must contain JSON objects")
            skill_rows.append(
                {
                    "requirement_id": item.requirement_id,
                    **versions,
                    **skill,
                    "modality": item.modality,
                }
            )
    return {
        "document_summary": [{
            "document_id": document.document_id,
            "schema_version": document.contract_version,
            "normalization_schema_version": normalization.contract_version,
            "job_title": document.title,
            "responsibility_count": len(document.responsibilities),
            "requirement_count": len(document.requirements),
        }],
        "responsibilities": [
            {
                "requirement_id": item.responsibility_id, "kind": "task",
                "modality": item.modality, **versions,
                **thaw_json_object(item.raw_payload),
                **_evidence_columns(item.evidence),
            }
            for item in document.responsibilities
        ],
        "requirements": requirement_rows,
        "skills": skill_rows,
        "company_facts": [
            {"fact_id": item.fact_id, "kind": item.kind, "value": item.value, **versions,
             **thaw_json_object(item.raw_payload), **_evidence_columns(item.evidence)}
            for item in document.facts if item.scope == "company"
        ],
        "employment_facts": [
            {"fact_id": item.fact_id, "kind": item.kind, "value": item.value, **versions,
             **thaw_json_object(item.raw_payload), **_evidence_columns(item.evidence)}
            for item in document.facts if item.scope == "employment"
        ],
        "skill_normalization": [
            {"source_name": item.source_value, "resolution_status": item.resolution_status,
             **versions,
             "skill_id": item.skill_id, "canonical_name": item.canonical_name,
             "category_code": item.category_code,
             "subcategory_code": item.subcategory_code,
             **thaw_json_object(item.raw_payload)}
            for item in normalization.items if item.item_type == "skill"
        ],
        "salary": [
            {**versions, **thaw_json_object(normalization.salary)}
        ] if normalization.salary else [],
        "review_flags": [
            {"item_type": item.flag_type, "source_value": item.source_value, **versions,
             "reason": item.reason, "severity": item.severity,
             "source": item.source, "code": item.code,
             **thaw_json_object(item.raw_payload)}
            for item in normalization.review_flags
        ],
    }


def export_workbook(
    document: Document, normalization: NormalizationResult
) -> tuple[bytes, dict[str, list[MutableJsonObject]]]:
    rows_by_sheet = to_export_rows(document, normalization)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in WORKSHEETS:
        sheet = workbook.create_sheet(sheet_name)
        rows = rows_by_sheet[sheet_name]
        headers = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
        sheet.append(headers)
        for row in rows:
            sheet.append([_excel_cell(row.get(header)) for header in headers])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue(), rows_by_sheet


def _excel_cell(value: JsonValue | MutableJsonValue) -> ExcelCell:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Excel cell contains a non-finite JSON number")
        return value
    if isinstance(value, (dict, list, FrozenJsonObject, FrozenJsonArray)):
        frozen = value if isinstance(
            value, (FrozenJsonObject, FrozenJsonArray)
        ) else freeze_json(value, field="Excel cell")
        standard = thaw_json(frozen)
        return json.dumps(
            standard,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    raise TypeError(f"Unsupported Excel cell value: {type(value).__name__}")


def serialize_export(
    document: Document, normalization: NormalizationResult
) -> MutableJsonObject:
    content, rows = export_workbook(document, normalization)
    return {
        "filename": f"jd_{document.document_id}_{document.contract_version}.xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content_base64": b64encode(content).decode("ascii"),
        "worksheets": list(rows),
    }
