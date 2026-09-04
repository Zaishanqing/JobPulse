from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .models import (
    CVCapabilityVerificationResult,
    CVExtractionResult,
    CVMatchFeatureResult,
    CVNormalizedResult,
)


REVIEW_FLAG_COLUMNS = (
    "cv_id", "issue_type", "severity", "rule_scope", "item_id", "description", "suggested_action",
)


def _jsonl_line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False).replace(" ", "\\u2028").replace(" ", "\\u2029") + "\n"


def _write_jsonl(records: list, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = record.model_dump(exclude_none=True) if hasattr(record, "model_dump") else record
            handle.write(_jsonl_line(payload))


def _write_json(records: list, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            [record.model_dump(exclude_none=True) if hasattr(record, "model_dump") else record for record in records],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def export_jsonl(records: list[CVExtractionResult], path: str) -> None:
    _write_jsonl(records, path)


def export_nested_json(records: list[CVExtractionResult], path: str) -> None:
    _write_json(records, path)


def export_normalized_jsonl(records: list[CVNormalizedResult], path: str) -> None:
    _write_jsonl(records, path)


def export_normalized_json(records: list[CVNormalizedResult], path: str) -> None:
    _write_json(records, path)


def export_match_feature_profiles(records: list[CVMatchFeatureResult], path: str) -> None:
    _write_json(records, path)


def export_match_features_jsonl(records: list[CVMatchFeatureResult], path: str) -> None:
    features = [feature for record in records for feature in record.features]
    _write_jsonl(features, path)


def export_capability_verification_profiles(
    records: list[CVCapabilityVerificationResult], path: str
) -> None:
    _write_json(records, path)


def export_capability_profiles_jsonl(
    records: list[CVCapabilityVerificationResult], path: str
) -> None:
    profiles = [profile for record in records for profile in record.profiles]
    _write_jsonl(profiles, path)


def export_capability_evidence_links_jsonl(
    records: list[CVCapabilityVerificationResult], path: str
) -> None:
    links = [link for record in records for link in record.evidence_links]
    _write_jsonl(links, path)


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


def _field_evidence_rows(document_id: str, object_scope: str, bindings) -> list[dict]:
    return [
        {
            "document_id": document_id,
            "object_scope": object_scope,
            "field_name": binding.field_name,
            **_evidence_columns(binding.evidence),
        }
        for binding in bindings
    ]


def export_xlsx(
    extractions: list[CVExtractionResult],
    normalized_results: list[dict],
    review_flags: list[dict],
    path: str,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_by_id = {result["document_id"]: result for result in normalized_results}

    summary_rows, education_rows, work_rows, project_rows = [], [], [], []
    responsibility_rows, achievement_rows, skill_rows = [], [], []
    work_skill_rows, project_skill_rows = [], []
    project_highlight_rows, language_rows, cert_rows, award_rows, self_eval_rows = [], [], [], [], []
    normalized_skill_rows, field_evidence_rows = [], []

    for extraction in extractions:
        personal = extraction.personal_info
        if personal is not None:
            field_evidence_rows.extend(
                _field_evidence_rows(
                    extraction.document_id, "personal_info", personal.field_evidence
                )
            )
        summary_rows.append(
            {
                "document_id": extraction.document_id,
                "name": personal.name if personal else None,
                "gender": personal.gender if personal else None,
                "education_count": len(extraction.education),
                "work_count": len(extraction.work_experience),
                "project_count": len(extraction.project_experience),
                "declared_skill_count": len(extraction.skills),
                "language_count": len(extraction.languages),
                "certificate_count": len(extraction.certificates),
                "award_count": len(extraction.awards),
            }
        )
        for entry in extraction.education:
            field_evidence_rows.extend(
                _field_evidence_rows(
                    extraction.document_id, f"education:{entry.entry_id}", entry.field_evidence
                )
            )
            education_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "school": entry.school,
                    "college": entry.college,
                    "major": entry.major,
                    "degree": entry.degree,
                    "date_start": entry.date.start if entry.date else None,
                    "date_end": entry.date.end if entry.date else None,
                    "gpa": entry.gpa,
                    "gpa_scale": entry.gpa_scale,
                    "school_tag": entry.school_tag,
                    **_evidence_columns(entry.evidence),
                }
            )
        for entry in extraction.work_experience:
            field_evidence_rows.extend(
                _field_evidence_rows(
                    extraction.document_id,
                    f"work_experience:{entry.entry_id}",
                    entry.field_evidence,
                )
            )
            work_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "company": entry.company,
                    "position": entry.position,
                    "date_start": entry.date.start if entry.date else None,
                    "date_end": entry.date.end if entry.date else None,
                    "department": entry.department,
                    "work_type": entry.work_type,
                    **_evidence_columns(entry.evidence),
                }
            )
            for item in entry.tech_stack:
                work_skill_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "item_id": item.item_id,
                        "name": item.name,
                        "item_type": item.item_type,
                        "proficiency": item.proficiency,
                        **_evidence_columns(item.evidence),
                    }
                )
            for index, fact in enumerate(entry.responsibilities, start=1):
                responsibility_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "fact_index": index,
                        "value": fact.value,
                        **_evidence_columns(fact.evidence),
                    }
                )
            for index, fact in enumerate(entry.achievements, start=1):
                achievement_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "fact_index": index,
                        "value": fact.value,
                        **_evidence_columns(fact.evidence),
                    }
                )
        for entry in extraction.project_experience:
            field_evidence_rows.extend(
                _field_evidence_rows(
                    extraction.document_id,
                    f"project_experience:{entry.entry_id}",
                    entry.field_evidence,
                )
            )
            project_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "date_start": entry.date.start if entry.date else None,
                    "date_end": entry.date.end if entry.date else None,
                    "role": entry.role,
                    "affiliation": entry.affiliation,
                    "description": entry.description.value if entry.description else None,
                    **_evidence_columns(entry.evidence),
                }
            )
            if entry.description is not None:
                project_highlight_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "fact_type": "description",
                        "fact_index": 0,
                        "value": entry.description.value,
                        **_evidence_columns(entry.description.evidence),
                    }
                )
            for item in entry.tech_stack:
                project_skill_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "item_id": item.item_id,
                        "name": item.name,
                        "item_type": item.item_type,
                        "proficiency": item.proficiency,
                        **_evidence_columns(item.evidence),
                    }
                )
            for index, fact in enumerate(entry.highlights, start=1):
                project_highlight_rows.append(
                    {
                        "document_id": extraction.document_id,
                        "entry_id": entry.entry_id,
                        "fact_type": "highlight",
                        "fact_index": index,
                        "value": fact.value,
                        **_evidence_columns(fact.evidence),
                    }
                )
        for item in extraction.skills:
            skill_rows.append(
                {
                    "document_id": extraction.document_id,
                    "item_id": item.item_id,
                    "name": item.name,
                    "item_type": item.item_type,
                    "proficiency": item.proficiency,
                    **_evidence_columns(item.evidence),
                }
            )
        for entry in extraction.languages:
            language_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "language": entry.language,
                    "proficiency": entry.proficiency,
                    **_evidence_columns(entry.evidence),
                }
            )
        for entry in extraction.certificates:
            cert_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "kind": entry.kind,
                    "issuing_body": entry.issuing_body,
                    "date": entry.date,
                    **_evidence_columns(entry.evidence),
                }
            )
        for entry in extraction.awards:
            award_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "level": entry.level,
                    "date": entry.date,
                    "issuing_body": entry.issuing_body,
                    **_evidence_columns(entry.evidence),
                }
            )
        for entry in extraction.self_evaluation:
            self_eval_rows.append(
                {
                    "document_id": extraction.document_id,
                    "entry_id": entry.entry_id,
                    "content": entry.content,
                    **_evidence_columns(entry.evidence),
                }
            )
        normalized = normalized_by_id[extraction.document_id]
        for item in normalized["normalized_skills"]:
            normalized_skill_rows.append(
                {
                    "document_id": extraction.document_id,
                    "skill_taxonomy_version": normalized[
                        "skill_taxonomy_version"
                    ],
                    **{
                        **item,
                        "classifications": json.dumps(
                            item["classifications"], ensure_ascii=False
                        ),
                    },
                }
            )

    sheets = {
        "document_summary": summary_rows,
        "education": education_rows,
        "work_experience": work_rows,
        "work_responsibilities": responsibility_rows,
        "work_achievements": achievement_rows,
        "work_tech_stack": work_skill_rows,
        "project_experience": project_rows,
        "project_facts": project_highlight_rows,
        "declared_skills": skill_rows,
        "project_tech_stack": project_skill_rows,
        "normalized_skills": normalized_skill_rows,
        "field_evidence": field_evidence_rows,
        "languages": language_rows,
        "certificates": cert_rows,
        "awards": award_rows,
        "self_evaluation": self_eval_rows,
        "review_flags": review_flags,
    }
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            columns = REVIEW_FLAG_COLUMNS if sheet_name == "review_flags" else None
            pd.DataFrame(rows, columns=columns).to_excel(writer, sheet_name=sheet_name, index=False)
