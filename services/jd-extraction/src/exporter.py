from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .models import JDExtractionResult, JDNormalizedResult, SkillRequirement


REVIEW_FLAG_COLUMNS = (
    "jd_id", "requirement_id", "item_id", "issue_type", "severity", "rule_scope",
    "issue_description", "raw_text", "suggested_action",
)


def _jsonl_line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + "\n"


def _write_jsonl(records: list, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = record.model_dump(exclude_none=True) if hasattr(record, "model_dump") else record
            handle.write(_jsonl_line(payload))


def export_jsonl(records: list[JDExtractionResult], path: str) -> None:
    _write_jsonl(records, path)


def export_normalized_jsonl(records: list[JDNormalizedResult], path: str) -> None:
    _write_jsonl(records, path)


def export_nested_json(records: list[JDExtractionResult], path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([record.model_dump(exclude_none=True) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_normalized_json(records: list[JDNormalizedResult], path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([record.model_dump(exclude_none=True) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_failed_cases(records: list[dict], path: str) -> None:
    _write_jsonl(records, path)


def export_illegal_enum_cases(records: list[dict], path: str) -> None:
    _write_jsonl(records, path)


def export_review_flags(records: list[dict], path: str) -> None:
    _write_jsonl(records, path)


def _evidence_columns(evidence) -> dict:
    return {
        "source_id": evidence.source_id,
        "quote": evidence.quote,
        "source_start": evidence.start,
        "source_end": evidence.end,
        "alignment": evidence.alignment,
        "occurrence_index": evidence.occurrence_index,
    }


def export_xlsx(
    extractions: list[JDExtractionResult],
    normalized_results: list[dict],
    review_flags: list[dict],
    path: str,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_rows, responsibility_rows, requirement_rows, skill_rows = [], [], [], []
    company_rows, employment_rows, normalization_rows, salary_rows = [], [], [], []
    normalized_by_id = {result["document_id"]: result for result in normalized_results}

    for extraction in extractions:
        normalized = normalized_by_id[extraction.document_id]
        summary_rows.append(
            {
                "document_id": extraction.document_id,
                "job_title": extraction.job_title.value if extraction.job_title else None,
                "responsibility_count": len(extraction.responsibilities),
                "requirement_count": len(extraction.requirements),
                "company_fact_count": len(extraction.company_facts),
                "employment_fact_count": len(extraction.employment_facts),
                "unresolved_skill_count": len(normalized["unresolved_items"]),
            }
        )
        for task in extraction.responsibilities:
            responsibility_rows.append(
                {"document_id": extraction.document_id, "requirement_id": task.requirement_id,
                 "kind": task.kind, "modality": task.modality, "action": task.action,
                 **_evidence_columns(task.evidence)}
            )
        for requirement in extraction.requirements:
            payload = requirement.model_dump(exclude={"requirement_id", "kind", "modality", "evidence"}, exclude_none=True)
            requirement_rows.append(
                {"document_id": extraction.document_id, "requirement_id": requirement.requirement_id,
                 "kind": requirement.kind, "modality": requirement.modality,
                 "payload_json": json.dumps(payload, ensure_ascii=False), **_evidence_columns(requirement.evidence)}
            )
            if isinstance(requirement, SkillRequirement):
                for item in requirement.items:
                    skill_rows.append(
                        {"document_id": extraction.document_id, "requirement_id": requirement.requirement_id,
                         "name": item.name, "item_type": item.item_type,
                         "proficiency": requirement.proficiency, **_evidence_columns(requirement.evidence)}
                    )
        for fact in extraction.company_facts:
            company_rows.append(
                {"document_id": extraction.document_id, "fact_id": fact.fact_id,
                 "kind": fact.kind, "value": fact.value, **_evidence_columns(fact.evidence)}
            )
        for fact in extraction.employment_facts:
            employment_rows.append(
                {"document_id": extraction.document_id, "fact_id": fact.fact_id,
                 "kind": fact.kind, "value": fact.value, **_evidence_columns(fact.evidence)}
            )
        for requirement in normalized["normalized_requirements"]:
            for skill in requirement["skills"]:
                normalization_rows.append(
                    {
                        "document_id": normalized["document_id"],
                        "skill_taxonomy_version": normalized[
                            "skill_taxonomy_version"
                        ],
                        "requirement_id": requirement["requirement_id"],
                        **{
                            **skill,
                            "classifications": json.dumps(
                                skill["classifications"], ensure_ascii=False
                            ),
                        },
                    }
                )
        salary = normalized.get("salary")
        if salary is not None:
            salary_rows.append({"document_id": normalized["document_id"], **salary})

    sheets = {
        "document_summary": summary_rows,
        "responsibilities": responsibility_rows,
        "requirements": requirement_rows,
        "skills": skill_rows,
        "company_facts": company_rows,
        "employment_facts": employment_rows,
        "skill_normalization": normalization_rows,
        "salary": salary_rows,
        "review_flags": review_flags,
    }
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            columns = REVIEW_FLAG_COLUMNS if sheet_name == "review_flags" else None
            pd.DataFrame(rows, columns=columns).to_excel(writer, sheet_name=sheet_name, index=False)
