from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.contracts.jd.extraction_v2 import JDExtractionResult as FrameworkExtraction
from app.contracts.jd.normalization_v2 import JDNormalizedResult as FrameworkNormalization
from app.domain.jd_skill_catalog import (
    CatalogAlias,
    CatalogSkill,
    resolve_catalog_skill,
)
from app.domain.json_types import JsonObject, freeze_json_object
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1


_SKILL_TYPE_MAP = {
    "technology": "other",
    "tool": "tool",
    "language": "programming_language",
    "framework": "framework",
    "platform": "platform",
    "method": "methodology",
    "other": "other",
}


@dataclass(frozen=True)
class ExtractionDraftMaterial:
    title: str
    extraction_payload: JsonObject
    normalization_payload: JsonObject
    position_title: str | None
    responsibilities: tuple[str, ...]
    required_skills: tuple[JsonObject, ...]
    bonus_skills: tuple[JsonObject, ...]
    education: str | None
    experience: str | None
    industry: str | None
    tools: tuple[str, ...]
    business_scenarios: tuple[str, ...]


def _evidence(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _validate_evidence(raw_text: str, value: Any) -> None:
    if value.alignment != "exact":
        return
    if value.start is None or value.end is None:
        raise ValueError("Exact Evidence must include start and end")
    if value.start < 0 or value.end < value.start or value.end > len(raw_text):
        raise ValueError("Exact Evidence coordinates are outside source text")
    if raw_text[value.start : value.end] != value.quote:
        raise ValueError("Exact Evidence coordinates do not match source text")


def _validate_all_evidence(bundle: ExtractedJDBundleV1, raw_text: str) -> None:
    extraction = bundle.extraction_result
    if extraction.job_title is not None:
        _validate_evidence(raw_text, extraction.job_title.evidence)
    for item in (
        *extraction.responsibilities,
        *extraction.requirements,
        *extraction.company_facts,
        *extraction.employment_facts,
    ):
        _validate_evidence(raw_text, item.evidence)


def _requirement_payload(item: Any) -> dict[str, Any]:
    base = {
        "requirement_id": item.requirement_id,
        "kind": item.kind,
        "modality": item.modality,
        "evidence": _evidence(item.evidence),
    }
    if item.kind == "skill":
        return {
            **base,
            "items": [
                {
                    "name": skill.name,
                    "item_type": _SKILL_TYPE_MAP[skill.item_type],
                }
                for skill in item.items
            ],
            "proficiency": item.proficiency,
        }
    if item.kind == "tool":
        return {**base, "tools": list(item.tools)}
    if item.kind == "education":
        return {
            **base,
            "minimum_degree": item.text,
            "majors": [],
            "school_constraints": [],
        }
    if item.kind == "experience":
        return {
            **base,
            "duration_text": item.text,
            "experience_unlimited": False,
        }
    if item.kind == "certificate":
        return {**base, "certificates": [item.text]}
    if item.kind == "soft_skill":
        return {**base, "skills": [item.text]}
    return {**base, "label": "external_extraction", "value": item.text}


def _bundle_review_flag(flag: dict[str, Any]) -> dict[str, Any]:
    issue_type = str(flag.get("issue_type") or flag.get("code") or "bundle_review")
    severity_value = str(flag.get("severity") or "warning").lower()
    severity = (
        "blocking" if severity_value in {"blocking", "error", "critical", "high"} else "warning"
    )
    source_value = str(
        flag.get("raw_text")
        or flag.get("source_value")
        or flag.get("requirement_id")
        or flag.get("item_id")
        or issue_type
    )
    return {
        "item_type": "job_title" if "title" in issue_type else "skill",
        "source_value": source_value,
        "reason": str(flag.get("issue_description") or flag.get("reason") or issue_type),
        "severity": severity,
        "source": "normalization",
        "code": issue_type,
        "details": flag,
    }


def map_bundle_to_framework_draft(
    bundle: ExtractedJDBundleV1,
    *,
    framework_jd_id: str,
    raw_text: str,
    fallback_title: str | None,
    catalog_skills: tuple[CatalogSkill, ...] = (),
    catalog_aliases: tuple[CatalogAlias, ...] = (),
) -> ExtractionDraftMaterial:
    """Map the shared bundle into the existing JD draft contracts.

    The framework JD id becomes the internal schema document id. The original
    Extraction document id remains in lineage columns and every Evidence
    source_id remains untouched.
    """
    _validate_all_evidence(bundle, raw_text)
    source = bundle.extraction_result
    normalized = bundle.normalized_result
    extraction_payload = {
        "schema_version": "v2",
        "document_id": framework_jd_id,
        "job_title": (
            {
                "value": source.job_title.text,
                "evidence": _evidence(source.job_title.evidence),
            }
            if source.job_title is not None
            else None
        ),
        "responsibilities": [
            {
                "requirement_id": item.requirement_id,
                "kind": "task",
                "action": item.text,
                "evidence": _evidence(item.evidence),
            }
            for item in source.responsibilities
        ],
        "requirements": [_requirement_payload(item) for item in source.requirements],
        "company_facts": [
            {
                "fact_id": item.fact_id,
                "kind": "business",
                "value": item.text,
                "evidence": _evidence(item.evidence),
            }
            for item in source.company_facts
        ],
        "employment_facts": [
            {
                "fact_id": item.fact_id,
                "kind": item.fact_type,
                "value": item.text,
                "evidence": _evidence(item.evidence),
            }
            for item in source.employment_facts
        ],
    }
    normalized_skills = []
    catalog_flags: list[dict[str, Any]] = []
    for requirement in normalized.normalized_requirements:
        for skill in requirement.normalized_skills:
            resolution = resolve_catalog_skill(
                source_name=skill.source_name,
                claimed_skill_id=skill.skill_id,
                claimed_canonical_name=skill.canonical_name,
                skills=catalog_skills,
                aliases=catalog_aliases,
            )
            normalized_skills.append(
                {
                    "source_name": skill.source_name,
                    "requirement_id": requirement.requirement_id,
                    "requirement_kind": requirement.kind,
                    "skill_id": resolution.skill_id,
                    "canonical_name": (resolution.canonical_name or skill.canonical_name),
                    "category_code": resolution.category_code,
                    "subcategory_code": (
                        skill.subcategory_code if resolution.status != "resolved" else None
                    ),
                    "resolution_status": resolution.status,
                    "resolution_source": resolution.resolution_source,
                    "source_skill_id": skill.skill_id,
                    "source_canonical_name": skill.canonical_name,
                    "source_category_code": skill.category_code,
                    "source_subcategory_code": skill.subcategory_code,
                    "source_resolution_status": skill.resolution_status,
                    "source_resolution_source": skill.resolution_source,
                }
            )
            if resolution.error_code is not None:
                catalog_flags.append(
                    {
                        "item_type": "skill",
                        "source_value": skill.source_name,
                        "reason": resolution.error_code,
                        "severity": "blocking",
                        "source": "normalization",
                        "code": resolution.error_code,
                        "details": {
                            "requirement_id": requirement.requirement_id,
                            "source_skill_id": skill.skill_id,
                            "catalog_resolution_status": resolution.status,
                        },
                    }
                )
    unresolved = [
        {
            "item_type": "job_title" if item.item_type == "position" else "skill",
            "source_value": item.source_name,
            "reason": item.reason,
            "severity": "warning",
            "source": "normalization",
            "code": "unresolved_external_item",
        }
        for item in normalized.unresolved_items
    ]
    unresolved.extend(_bundle_review_flag(flag) for flag in bundle.review_flags)
    unresolved.extend(catalog_flags)
    normalization_payload = {
        "schema_version": "v2",
        "document_id": framework_jd_id,
        "job_classification": normalized.job_classification.model_dump(mode="json"),
        "normalized_requirements": normalized_skills,
        "salary": (
            {
                "raw_value": None,
                **normalized.salary.model_dump(mode="json"),
            }
            if normalized.salary is not None
            else None
        ),
        "unresolved_items": unresolved,
    }

    extraction_contract = FrameworkExtraction.model_validate(extraction_payload)
    normalization_contract = FrameworkNormalization.model_validate(normalization_payload)
    normalized_by_name = {
        item.source_name: item for item in normalization_contract.normalized_requirements
    }
    required_skills: list[JsonObject] = []
    bonus_skills: list[JsonObject] = []
    tools: list[str] = []
    education = None
    experience = None
    for requirement in source.requirements:
        if requirement.kind == "skill":
            if requirement.modality == "required":
                target = required_skills
            elif requirement.modality in ("preferred", "bonus"):
                target = bonus_skills
            else:
                # unknown/invalid modality stays out of the legacy buckets and is
                # preserved in the versioned extraction payload for review.
                continue
            for skill in requirement.items:
                resolved = normalized_by_name.get(skill.name)
                target.append(
                    freeze_json_object(
                        {
                            "raw_skill": skill.name,
                            "normalized_skill_id": (
                                resolved.skill_id if resolved is not None else None
                            ),
                            "confidence": 1.0 if resolved and resolved.skill_id else 0.0,
                            "resolution_status": (
                                resolved.resolution_status if resolved is not None else "unresolved"
                            ),
                        }
                    )
                )
                if skill.item_type in {"tool", "platform"}:
                    tools.append(skill.name)
        elif requirement.kind == "education" and education is None:
            education = requirement.text
        elif requirement.kind == "experience" and experience is None:
            experience = requirement.text

    title = source.job_title.text if source.job_title is not None else fallback_title
    title = title or "Untitled JD"
    return ExtractionDraftMaterial(
        title=title,
        extraction_payload=freeze_json_object(extraction_contract.model_dump(mode="json")),
        normalization_payload=freeze_json_object(normalization_contract.model_dump(mode="json")),
        position_title=source.job_title.text if source.job_title is not None else None,
        responsibilities=tuple(item.text for item in source.responsibilities),
        required_skills=tuple(required_skills),
        bonus_skills=tuple(bonus_skills),
        education=education,
        experience=experience,
        industry=None,
        tools=tuple(dict.fromkeys(tools)),
        business_scenarios=(),
    )
