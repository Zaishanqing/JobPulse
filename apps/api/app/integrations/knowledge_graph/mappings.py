from __future__ import annotations

from jobgraph_contracts.extraction_v2 import JDExtractionResult as KGJDExtractionResult
from jobgraph_contracts.normalization_v2 import JDNormalizedResult as KGJDNormalizedResult


def _evidence(value: dict) -> dict:
    return {
        "source_id": value["source_id"],
        "quote": value["quote"],
        "start": value["start"],
        "end": value["end"],
        "alignment": value["alignment"],
        "occurrence_index": value["occurrence_index"],
    }


def _text_for_requirement(value: dict) -> str:
    for key in ("value", "duration_text", "role", "domain", "label"):
        if value.get(key):
            return str(value[key])
    if value.get("certificates"):
        return "、".join(value["certificates"])
    if value.get("skills"):
        return "、".join(value["skills"])
    if value.get("tools"):
        return "、".join(value["tools"])
    if value.get("majors"):
        return "、".join(value["majors"])
    return value["evidence"]["quote"]


_ITEM_TYPES = {
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


def extraction_to_kg(payload: dict) -> dict:
    if payload.get("schema_version") != "v2":
        raise ValueError("Only extraction schema_version v2 can be synchronized")
    result = {
        "schema_version": "v2",
        "document_id": payload["document_id"],
        "job_title": None,
        "responsibilities": [],
        "requirements": [],
        "company_facts": [],
        "employment_facts": [],
    }
    if payload.get("job_title"):
        result["job_title"] = {
            "text": payload["job_title"]["value"],
            "evidence": _evidence(payload["job_title"]["evidence"]),
        }
    for item in payload.get("responsibilities", []):
        result["responsibilities"].append(
            {
                "requirement_id": item["requirement_id"],
                "text": item["action"],
                "evidence": _evidence(item["evidence"]),
            }
        )
    for item in payload.get("requirements", []):
        base = {
            "requirement_id": item["requirement_id"],
            "kind": item["kind"],
            "modality": item.get("modality", "unknown"),
            "evidence": _evidence(item["evidence"]),
        }
        if item["kind"] == "skill":
            base["items"] = [
                {
                    "name": skill["name"],
                    "item_type": _ITEM_TYPES.get(skill.get("item_type"), "other"),
                }
                for skill in item.get("items", [])
            ]
            base["proficiency"] = item.get("proficiency")
        elif item["kind"] == "tool":
            base["tools"] = list(item.get("tools", []))
        elif item["kind"] == "education":
            base.update(
                {
                    "text": _text_for_requirement(item),
                    "minimum_degree": item.get("minimum_degree"),
                    "majors": list(item.get("majors", [])),
                    "school_constraints": list(
                        item.get("school_constraints", [])
                    ),
                    "admission_type": item.get("admission_type"),
                    "graduation_year": item.get("graduation_year"),
                    "student_cohort": item.get("student_cohort"),
                }
            )
        elif item["kind"] == "experience":
            base.update(
                {
                    "text": _text_for_requirement(item),
                    "minimum_years": item.get("minimum_years"),
                    "maximum_years": item.get("maximum_years"),
                    "domain": item.get("domain"),
                    "role": item.get("role"),
                    "duration_text": item.get("duration_text"),
                    "experience_unlimited": item.get(
                        "experience_unlimited", False
                    ),
                }
            )
        elif item["kind"] == "certificate":
            base.update(
                {
                    "text": _text_for_requirement(item),
                    "certificates": list(item.get("certificates", [])),
                }
            )
        elif item["kind"] == "soft_skill":
            base.update(
                {
                    "text": _text_for_requirement(item),
                    "skills": list(item.get("skills", [])),
                }
            )
        else:
            base["text"] = _text_for_requirement(item)
        result["requirements"].append(base)
    for item in payload.get("company_facts", []):
        result["company_facts"].append(
            {
                "fact_id": item["fact_id"],
                "text": item["value"],
                "evidence": _evidence(item["evidence"]),
            }
        )
    employment_types = {
        "work_location": "location",
        "employment_type": "employment_type",
        "salary": "salary",
        "work_schedule": "schedule",
    }
    for item in payload.get("employment_facts", []):
        result["employment_facts"].append(
            {
                "fact_id": item["fact_id"],
                "fact_type": employment_types.get(item["kind"], "other"),
                "text": item["value"],
                "evidence": _evidence(item["evidence"]),
            }
        )
    return KGJDExtractionResult.model_validate(result).model_dump(mode="json")


def _normalized_key(value: str | None) -> str:
    return "".join((value or "").casefold().split())


def unique_name_match(values: list[dict], name: str, field: str) -> dict | None:
    key = _normalized_key(name)
    matches = [value for value in values if _normalized_key(value.get(field)) == key]
    return matches[0] if len(matches) == 1 else None


def normalization_to_kg(
    payload: dict,
    extraction_payload: dict,
    *,
    kg_skills: list[dict],
    kg_positions: list[dict],
    position_override: dict | None = None,
    explicit_skill_mappings: dict[str, str] | None = None,
) -> tuple[dict, dict[str, dict], dict | None]:
    if payload.get("schema_version") != "v2":
        raise ValueError("Only normalization schema_version v2 can be synchronized")
    requirements = []
    for requirement in extraction_payload.get("requirements", []):
        normalized_skills = []
        requirements.append(
            {
                "requirement_id": requirement["requirement_id"],
                "kind": requirement["kind"],
                "normalized_skills": normalized_skills,
            }
        )
    requirements_by_id = {item["requirement_id"]: item for item in requirements}
    skill_matches: dict[str, dict] = {}
    unresolved = []
    explicit_skill_mappings = explicit_skill_mappings or {}
    active_skills = [skill for skill in kg_skills if skill.get("status", "active") == "active"]
    for item in payload.get("normalized_requirements", []):
        if item.get("resolution_status") not in {"resolved", "manually_confirmed"}:
            continue
        source_name = item["source_name"]
        main_skill_id = str(item.get("skill_id") or "")
        explicit_target = explicit_skill_mappings.get(main_skill_id)
        match = None
        resolution_source = "unresolved"
        invalid_explicit_target = False
        if explicit_target:
            match = next(
                (skill for skill in active_skills if str(skill.get("skill_id")) == explicit_target),
                None,
            )
            if match:
                resolution_source = "explicit_mapping"
            else:
                invalid_explicit_target = True
        elif main_skill_id:
            match = next(
                (skill for skill in active_skills if str(skill.get("skill_id")) == main_skill_id),
                None,
            )
            if match:
                resolution_source = "same_id"
        declared_requirement_id = item.get("requirement_id")
        requirement_ids = [declared_requirement_id] if declared_requirement_id in requirements_by_id else []
        resolved = bool(match and len(requirement_ids) == 1)
        mapped = {
            "source_name": source_name,
            "skill_id": match["skill_id"] if resolved else None,
            "canonical_name": match.get("canonical_name") if resolved else None,
            "category_code": match.get("category_code") if resolved else None,
            "subcategory_code": match.get("subcategory_code") if resolved else None,
            "resolution_status": "resolved" if resolved else "unresolved",
            "resolution_source": resolution_source if resolved else "unresolved",
        }
        if requirement_ids:
            requirements_by_id[requirement_ids[0]]["normalized_skills"].append(mapped)
        if resolved:
            skill_matches[item.get("skill_id") or f"name:{source_name}"] = match
        else:
            unresolved.append(
                {
                    "source_name": source_name,
                    "item_type": "skill",
                    "reason": (
                        f"Confirmed skill mapping target {explicit_target} is missing or inactive"
                        if invalid_explicit_target
                        else "Resolved skill lacks an exact catalog or requirement-id mapping"
                    ),
                }
            )
    classification = payload.get("job_classification") or {}
    position_match = position_override
    position_code = classification.get("position_code")
    if position_match is None and position_code:
        matches = [
            position
            for position in kg_positions
            if position.get("position_code") == position_code
        ]
        position_match = matches[0] if len(matches) == 1 else None
    source_title = classification.get("source_title")
    job_classification = {
        "schema_version": classification.get("schema_version"),
        "taxonomy_version": classification.get("taxonomy_version"),
        "source_title": source_title,
        "position_id": position_match.get("position_id") if position_match else None,
        "position_code": position_code,
        "position_name": classification.get("position_name"),
        "family_code": classification.get("family_code"),
        "family_name": classification.get("family_name"),
        "candidate_positions": list(classification.get("candidate_positions") or []),
        "career_level": classification.get("career_level"),
        "leadership_scope": classification.get("leadership_scope"),
        "technology_focus_codes": list(
            classification.get("technology_focus_codes") or []
        ),
        "industry_context_codes": list(
            classification.get("industry_context_codes") or []
        ),
        "observed_skill_domain_codes": list(
            classification.get("observed_skill_domain_codes") or []
        ),
        "confidence": classification.get("confidence"),
        "classification_status": classification.get("classification_status"),
        "review_reason_codes": list(
            classification.get("review_reason_codes") or []
        ),
        "evidence_refs": list(classification.get("evidence_refs") or []),
        "classification_policy_version": classification.get(
            "classification_policy_version"
        ),
    }
    salary = payload.get("salary")
    if salary:
        salary = {
            "currency": salary.get("currency") or "CNY",
            "minimum": salary.get("minimum"),
            "maximum": salary.get("maximum"),
            "period": salary.get("period") or "unknown",
        }
    for item in payload.get("unresolved_items", []):
        unresolved.append(
            {
                "source_name": item.get("source_value", ""),
                "item_type": "position" if item.get("item_type") == "job_title" else "skill",
                "reason": item["reason"],
            }
        )
    result = {
        "schema_version": "v2",
        "document_id": payload["document_id"],
        "job_classification": job_classification,
        "normalized_requirements": requirements,
        "salary": salary,
        "unresolved_items": unresolved,
    }
    validated = KGJDNormalizedResult.model_validate(result).model_dump(mode="json")
    return (
        validated,
        skill_matches,
        position_match,
    )
