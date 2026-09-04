from __future__ import annotations

from typing import Any

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_v2 import (
    JDExtractionResult as ContractExtractionResult,
)
from jobgraph_contracts.normalization_v2 import (
    JDNormalizedResult as ContractNormalizedResult,
)

from ..models import (
    JDExtractionResult,
    JDNormalizedResult,
    SkillRequirement,
    ToolRequirement,
)
from ..preprocess import build_source_blocks, normalize_jd_text
from ..text_cleaning import clean_jd_text


_SKILL_TYPE_MAP = {
    "programming_language": "language",
    "framework": "framework",
    "tool": "tool",
    "platform": "platform",
    "methodology": "method",
    "library": "technology",
    "database": "technology",
    "domain_knowledge": "technology",
    "other": "other",
}
_EMPLOYMENT_TYPE_MAP = {
    "location": "location",
    "employment_type": "employment_type",
    "work_schedule": "schedule",
}
_SALARY_PERIOD_MAP = {"时": "hour", "日": "day", "月": "month", "年": "year"}


def envelope_to_pipeline_input(
    envelope: CrawlerJDEnvelopeV1,
    document_id: str,
) -> dict[str, Any]:
    """Preserve raw text while reproducing the frozen Pipeline's NFKC input."""
    raw_text = envelope.raw_text
    cleaned_text = clean_jd_text(raw_text)
    model_text = normalize_jd_text(cleaned_text)
    return {
        "jd_id": document_id,
        "job_title_raw": envelope.job_title_raw or "未提及",
        "jd_text": model_text,
        "jd_text_original": raw_text,
        "cleaned_text": cleaned_text,
        "source_blocks": build_source_blocks(model_text),
        "company": envelope.company_name_raw or "未提及",
        "region": envelope.region_raw or "未提及",
        "salary": "未提及",
        "source_row": envelope.raw_payload,
    }


def _evidence(value: Any) -> dict[str, Any]:
    return value.model_dump()


def _requirement_graph(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "graph_version": value.graph_version,
        "status": value.status,
        "groups": [
            {
                "requirement_group_id": group.requirement_group_id,
                "group_type": group.group_type,
                "priority": group.priority,
                "children": [
                    {
                        "node_type": child.node_type,
                        "ref_id": child.ref_id,
                        "aspect": child.aspect,
                    }
                    for child in group.children
                ],
                "min_count": group.min_count,
                "evidence": _evidence(group.evidence),
                "confidence": group.confidence,
                "note": group.note,
            }
            for group in value.groups
        ],
        "unresolved_items": list(value.unresolved_items),
    }


def to_contract_extraction(result: JDExtractionResult) -> ContractExtractionResult:
    job_title = None
    if result.job_title is not None:
        job_title = {
            "text": result.job_title.value,
            "evidence": _evidence(result.job_title.evidence),
        }

    responsibilities = [
        {
            "requirement_id": item.requirement_id,
            "text": item.action,
            "evidence": _evidence(item.evidence),
        }
        for item in result.responsibilities
    ]
    requirements: list[dict[str, Any]] = []
    for item in result.requirements:
        base = {
            "requirement_id": item.requirement_id,
            "kind": item.kind,
            "modality": item.modality,
            "evidence": _evidence(item.evidence),
        }
        if isinstance(item, SkillRequirement):
            base["items"] = [
                {"name": skill.name, "item_type": _SKILL_TYPE_MAP[skill.item_type]}
                for skill in item.items
            ]
            base["proficiency"] = item.proficiency
        elif isinstance(item, ToolRequirement):
            base["tools"] = list(item.tools)
        elif item.kind == "education":
            base.update(
                {
                    "text": item.evidence.quote,
                    "minimum_degree": item.minimum_degree,
                    "majors": list(item.majors or []),
                    "school_constraints": list(item.school_constraints or []),
                    "admission_type": item.admission_type,
                    "graduation_year": item.graduation_year,
                    "student_cohort": item.student_cohort,
                }
            )
        elif item.kind == "experience":
            base.update(
                {
                    "text": item.evidence.quote,
                    "minimum_years": item.minimum_years,
                    "maximum_years": item.maximum_years,
                    "domain": item.domain,
                    "role": item.role,
                    "duration_text": item.duration_text,
                    "experience_unlimited": item.experience_unlimited,
                }
            )
        elif item.kind == "certificate":
            base.update(
                {
                    "text": item.evidence.quote,
                    "certificates": list(item.certificates),
                }
            )
        elif item.kind == "soft_skill":
            base.update(
                {
                    "text": item.evidence.quote,
                    "skills": list(item.skills),
                }
            )
        else:
            base["text"] = item.evidence.quote
        requirements.append(base)

    return ContractExtractionResult(
        document_id=result.document_id,
        job_title=job_title,
        responsibilities=responsibilities,
        requirements=requirements,
        company_facts=[
            {
                "fact_id": fact.fact_id,
                "text": fact.value,
                "evidence": _evidence(fact.evidence),
            }
            for fact in result.company_facts
        ],
        employment_facts=[
            {
                "fact_id": fact.fact_id,
                "fact_type": _EMPLOYMENT_TYPE_MAP.get(fact.kind, "other"),
                "text": fact.value,
                "evidence": _evidence(fact.evidence),
            }
            for fact in result.employment_facts
        ],
        requirement_graph=_requirement_graph(result.requirement_graph),
    )


def _resolution_status(status: str) -> str:
    return status if status in {"resolved", "unresolved"} else "unresolved"


def to_contract_normalized(
    result: JDNormalizedResult,
    extraction: JDExtractionResult,
) -> ContractNormalizedResult:
    classification = result.job_classification
    title = extraction.job_title.value if extraction.job_title is not None else None
    normalized_requirements = []
    for requirement in result.normalized_requirements:
        normalized_requirements.append(
            {
                "requirement_id": requirement.requirement_id,
                "kind": requirement.kind,
                "normalized_skills": [
                    {
                        "source_name": skill.source_name,
                        "skill_id": skill.skill_id,
                        "canonical_name": skill.canonical_name,
                        "category_code": skill.category_code,
                        "subcategory_code": skill.subcategory_code,
                        "resolution_status": _resolution_status(
                            skill.resolution_status
                        ),
                        "resolution_source": (
                            "explicit_mapping"
                            if skill.resolution_status == "resolved"
                            else "unresolved"
                        ),
                    }
                    for skill in requirement.skills
                ],
            }
        )
    salary = None
    if result.salary is not None:
        salary = {
            "currency": result.salary.currency or "CNY",
            "minimum": result.salary.minimum,
            "maximum": result.salary.maximum,
            "period": _SALARY_PERIOD_MAP.get(result.salary.period, "unknown"),
        }
    return ContractNormalizedResult(
        document_id=result.document_id,
        job_classification={
            "schema_version": (
                classification.schema_version
                if classification is not None
                else "job-position-classification.v3"
            ),
            "taxonomy_version": (
                classification.taxonomy_version
                if classification is not None
                else "position-taxonomy.v3.0.0"
            ),
            "source_title": title,
            "position_code": classification.position_code
            if classification is not None
            else None,
            "position_name": classification.position_name if classification is not None else None,
            "family_code": classification.family_code if classification is not None else None,
            "family_name": classification.family_name if classification is not None else None,
            "candidate_positions": classification.candidate_positions if classification is not None else [],
            "career_level": classification.career_level if classification is not None else None,
            "leadership_scope": classification.leadership_scope if classification is not None else None,
            "technology_focus_codes": classification.technology_focus_codes if classification is not None else [],
            "industry_context_codes": classification.industry_context_codes if classification is not None else [],
            "observed_skill_domain_codes": classification.observed_skill_domain_codes if classification is not None else [],
            "confidence": classification.confidence if classification is not None else None,
            "classification_status": (
                classification.classification_status
                if classification is not None
                else "catalog_gap"
            ),
            "review_reason_codes": classification.review_reason_codes if classification is not None else ["CLASSIFICATION_NOT_RUN"],
            "evidence_refs": classification.evidence_refs if classification is not None else [],
            "classification_policy_version": classification.classification_policy_version if classification is not None else "position-classifier.v3.0",
        },
        normalized_requirements=normalized_requirements,
        salary=salary,
        unresolved_items=[
            {"source_name": name, "item_type": "skill", "reason": "unresolved"}
            for name in result.unresolved_items
        ],
    )
