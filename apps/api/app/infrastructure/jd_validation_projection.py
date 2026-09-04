from __future__ import annotations

from datetime import datetime

from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1


_SKILL_TYPES = {
    "programming_language": "language",
    "framework": "framework",
    "library": "technology",
    "database": "technology",
    "tool": "tool",
    "platform": "platform",
    "methodology": "method",
    "domain_knowledge": "other",
    "other": "other",
}
_EMPLOYMENT_TYPES = {
    "salary": "salary",
    "work_location": "location",
    "location": "location",
    "employment_type": "employment_type",
    "work_schedule": "schedule",
    "schedule": "schedule",
    "benefit": "other",
    "training": "other",
    "headcount": "headcount",
    "other": "other",
}
_RESOLUTION_SOURCES = {
    "capability_catalog_alias": "alias",
    "capability_catalog_exact_match": "canonical_name",
    "capability_catalog_id": "same_id",
    "explicit_mapping": "explicit_mapping",
    "manual_review": "explicit_mapping",
    "same_id": "same_id",
    "canonical_name": "canonical_name",
    "alias": "alias",
    "unresolved": "unresolved",
}


def _evidence(value: dict) -> dict:
    return {
        "source_id": value["source_id"],
        "quote": value["quote"],
        "start": value.get("start"),
        "end": value.get("end"),
        "alignment": value.get("alignment", "unresolved"),
        "occurrence_index": value.get("occurrence_index"),
    }


def _requirement(value: dict) -> dict:
    kind = value["kind"]
    evidence_text = str(value.get("text") or value["evidence"]["quote"])
    result = {
        "requirement_id": value["requirement_id"],
        "kind": kind,
        "modality": value.get("modality", "unknown"),
        "evidence": _evidence(value["evidence"]),
    }
    if kind == "skill":
        result["items"] = [
            {"name": item["name"], "item_type": _SKILL_TYPES[item["item_type"]]}
            for item in value.get("items", [])
        ]
        result["proficiency"] = value.get("proficiency")
    elif kind == "tool":
        result["tools"] = list(value.get("tools", []))
    elif kind == "education":
        result["text"] = evidence_text
        for field in (
            "minimum_degree", "majors", "school_constraints", "admission_type",
            "graduation_year", "student_cohort",
        ):
            if field in value:
                result[field] = value[field]
    elif kind == "experience":
        result["text"] = evidence_text
        for field in (
            "minimum_years", "maximum_years", "domain", "role", "duration_text",
            "experience_unlimited",
        ):
            if field in value:
                result[field] = value[field]
    elif kind == "certificate":
        result["text"] = evidence_text
        result["certificates"] = list(value.get("certificates", []))
    elif kind == "soft_skill":
        result["text"] = evidence_text
        result["skills"] = list(value.get("skills", []))
    elif kind == "other":
        result["text"] = str(value.get("value") or evidence_text)
    else:
        raise ValueError(f"Unsupported reviewed requirement kind: {kind}")
    return result


def reviewed_fact_duplicate_value(collection: str, value: dict) -> dict:
    if collection == "responsibilities":
        projected = {
            "requirement_id": value["requirement_id"],
            "text": value["action"],
            "evidence": _evidence(value["evidence"]),
        }
    elif collection == "requirements":
        projected = _requirement(value)
    elif collection == "company_facts":
        projected = {
            "fact_id": value["fact_id"],
            "text": str(value["value"]),
            "evidence": _evidence(value["evidence"]),
        }
    elif collection == "employment_facts":
        projected = {
            "fact_id": value["fact_id"],
            "fact_type": _EMPLOYMENT_TYPES[value["kind"]],
            "text": str(value["value"]),
            "evidence": _evidence(value["evidence"]),
        }
    else:
        raise ValueError(f"Unsupported extraction fact collection: {collection}")
    return {
        key: child
        for key, child in projected.items()
        if key not in {"evidence", "requirement_id", "fact_id"}
    }


def build_reviewed_extraction_bundle(
    *,
    source_platform: str,
    source_record_id: str,
    source_version: str,
    cleaned_text: str,
    extraction_result: dict,
    normalized_result: dict,
    provider: str,
    model_version: str,
    run_id: str,
    timestamp: datetime,
) -> ExtractedJDBundleV1:
    document_id = str(extraction_result["document_id"])
    requirements = [_requirement(item) for item in extraction_result.get("requirements", [])]
    skill_requirement_by_name = {
        item["name"]: requirement["requirement_id"]
        for requirement in extraction_result.get("requirements", [])
        if requirement.get("kind") == "skill"
        for item in requirement.get("items", [])
    }
    normalized_groups: dict[tuple[str, str], list[dict]] = {}
    for item in normalized_result.get("normalized_requirements", []):
        status = item["resolution_status"]
        if status not in {"resolved", "manually_confirmed"}:
            continue
        requirement_id = item.get("requirement_id") or skill_requirement_by_name.get(item["source_name"])
        if not requirement_id:
            raise ValueError(
                f"Reviewed skill has no source requirement: {item['source_name']}"
            )
        source = _RESOLUTION_SOURCES[item.get("resolution_source") or "unresolved"]
        normalized_groups.setdefault(
            (str(requirement_id), str(item.get("requirement_kind") or "skill")), []
        ).append(
            {
                "source_name": item["source_name"],
                "skill_id": item.get("skill_id"),
                "canonical_name": item.get("canonical_name"),
                "category_code": item.get("category_code"),
                "subcategory_code": item.get("subcategory_code"),
                "resolution_status": status,
                "resolution_source": source,
            }
        )
    classification = normalized_result.get("job_classification") or {}
    salary = normalized_result.get("salary")
    return ExtractedJDBundleV1(
        source_platform=source_platform,
        source_record_id=source_record_id,
        source_version=source_version,
        cleaned_text=cleaned_text,
        extraction_result={
            "schema_version": "v2",
            "document_id": document_id,
            "job_title": (
                {
                    "text": extraction_result["job_title"]["value"],
                    "evidence": _evidence(extraction_result["job_title"]["evidence"]),
                }
                if extraction_result.get("job_title") else None
            ),
            "responsibilities": [
                {
                    "requirement_id": item["requirement_id"],
                    "text": item["action"],
                    "evidence": _evidence(item["evidence"]),
                }
                for item in extraction_result.get("responsibilities", [])
            ],
            "requirements": requirements,
            "company_facts": [
                {
                    "fact_id": item["fact_id"],
                    "text": str(item["value"]),
                    "evidence": _evidence(item["evidence"]),
                }
                for item in extraction_result.get("company_facts", [])
            ],
            "employment_facts": [
                {
                    "fact_id": item["fact_id"],
                    "fact_type": _EMPLOYMENT_TYPES[item["kind"]],
                    "text": str(item["value"]),
                    "evidence": _evidence(item["evidence"]),
                }
                for item in extraction_result.get("employment_facts", [])
            ],
        },
        normalized_result={
            "schema_version": "v2",
            "document_id": document_id,
            "job_classification": {
                "schema_version": classification.get("schema_version"),
                "taxonomy_version": classification.get("taxonomy_version"),
                "source_title": classification.get("source_title"),
                "position_id": classification.get("position_id"),
                "position_code": classification.get("position_code"),
                "position_name": classification.get("position_name"),
                "family_code": classification.get("family_code"),
                "family_name": classification.get("family_name"),
                "candidate_positions": classification.get("candidate_positions", []),
                "career_level": classification.get("career_level"),
                "leadership_scope": classification.get("leadership_scope"),
                "technology_focus_codes": classification.get("technology_focus_codes", []),
                "industry_context_codes": classification.get("industry_context_codes", []),
                "observed_skill_domain_codes": classification.get("observed_skill_domain_codes", []),
                "confidence": classification.get("confidence"),
                "classification_status": classification.get("classification_status", "ambiguous"),
                "review_reason_codes": classification.get("review_reason_codes", []),
                "evidence_refs": classification.get("evidence_refs", []),
                "classification_policy_version": classification.get("classification_policy_version"),
            },
            "normalized_requirements": [
                {
                    "requirement_id": requirement_id,
                    "kind": kind,
                    "normalized_skills": skills,
                }
                for (requirement_id, kind), skills in normalized_groups.items()
            ],
            "salary": (
                {
                    "currency": salary.get("currency") or "CNY",
                    "minimum": salary.get("minimum"),
                    "maximum": salary.get("maximum"),
                    "period": salary.get("period") or "unknown",
                }
                if salary else None
            ),
            "unresolved_items": [
                {
                    "source_name": item["source_value"],
                    "item_type": "position" if item["item_type"] == "job_title" else "skill",
                    "reason": item["reason"],
                }
                for item in normalized_result.get("unresolved_items", [])
            ],
        },
        review_flags=list(normalized_result.get("unresolved_items", [])),
        extraction_provider=provider,
        model_version=model_version,
        extraction_run_id=run_id,
        extraction_started_at=timestamp,
        extraction_finished_at=timestamp,
    )
