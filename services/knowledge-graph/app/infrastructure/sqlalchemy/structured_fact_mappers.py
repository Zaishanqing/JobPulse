"""Translate framework-free facts to and from persistence Pydantic schemas."""

from __future__ import annotations

from dataclasses import asdict

from app.domain.structured_facts import (
    CandidateRequirementFact,
    CompanyFact,
    EmploymentFact,
    EvidenceFact,
    ExtractionFacts,
    JobClassificationFact,
    NormalizationFacts,
    NormalizedRequirementFact,
    NormalizedSalaryFact,
    NormalizedSkillFact,
    PublishedJDFact,
    SkillItemFact,
    SourcedTextFact,
    TaskRequirementFact,
    UnresolvedNormalizationFact,
)
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import JDNormalizedResult


def extraction_schema(facts: ExtractionFacts) -> JDExtractionResult:
    payload = asdict(facts)
    requirements = []
    for item in facts.requirements:
        value = {
            "requirement_id": item.requirement_id,
            "kind": item.kind,
            "modality": item.modality,
            "evidence": asdict(item.evidence),
        }
        if item.kind == "skill":
            value["items"] = [asdict(skill) for skill in item.items]
            value["proficiency"] = item.proficiency
        elif item.kind == "education":
            value.update(
                {
                    "text": item.text,
                    "minimum_degree": item.minimum_degree,
                    "majors": list(item.majors),
                    "school_constraints": list(item.school_constraints),
                    "admission_type": item.admission_type,
                    "graduation_year": item.graduation_year,
                    "student_cohort": item.student_cohort,
                }
            )
        elif item.kind == "experience":
            value.update(
                {
                    "text": item.text,
                    "minimum_years": item.minimum_years,
                    "maximum_years": item.maximum_years,
                    "domain": item.domain,
                    "role": item.role,
                    "duration_text": item.duration_text,
                    "experience_unlimited": item.experience_unlimited,
                }
            )
        elif item.kind == "certificate":
            value.update(
                {"text": item.text, "certificates": list(item.certificates)}
            )
        elif item.kind == "soft_skill":
            value.update({"text": item.text, "skills": list(item.skills)})
        else:
            value["text"] = item.text or ""
        requirements.append(value)
    payload["requirements"] = requirements
    return JDExtractionResult.model_validate(payload)


def normalization_schema(facts: NormalizationFacts) -> JDNormalizedResult:
    return JDNormalizedResult.model_validate(asdict(facts))


def published_fact_payload(fact: PublishedJDFact) -> dict:
    """Flatten explicit published facts into the external persisted JSON contract."""
    return {
        "contract_version": fact.contract_version,
        "schema_version": fact.schema_version,
        "source_system": fact.source_system,
        "source_jd_id": fact.source_jd_id,
        "source_fact_id": fact.source_fact_id,
        "source_fact_version": fact.source_fact_version,
        "review_status": fact.review_status,
        "published_at": fact.published_at,
        "source_version": fact.source_version,
        "position_fact": {
            "position_id": fact.position_fact.position_id,
            **dict(fact.position_fact.extensions),
        },
        "skill_facts": [
            {"skill_id": item.skill_id, **dict(item.extensions)}
            for item in fact.skill_facts
        ],
        "requirement_facts": [
            {"requirement_id": item.requirement_id, **dict(item.extensions)}
            for item in fact.requirement_facts
        ],
        "education_fact": fact.education_fact,
        "experience_fact": fact.experience_fact,
        "industry_fact": fact.industry_fact,
        "company_facts": [dict(item) for item in fact.company_facts],
        "employment_facts": [dict(item) for item in fact.employment_facts],
        "evidence": [
            {
                "source_id": item.source_id,
                "quote": item.quote,
                "start": item.start,
                "end": item.end,
                "alignment": item.alignment,
                "occurrence_index": item.occurrence_index,
                **dict(item.extensions),
            }
            for item in fact.evidence
        ],
        "extraction_fact": extraction_schema(fact.extraction_fact).model_dump(
            mode="json"
        ),
        "normalized_fact": normalization_schema(fact.normalized_fact).model_dump(
            mode="json"
        ),
        "trace_metadata": dict(fact.trace_metadata),
    }


def _evidence(value) -> EvidenceFact:
    return EvidenceFact(**value.model_dump())


def extraction_facts(value: JDExtractionResult) -> ExtractionFacts:
    return ExtractionFacts(
        document_id=value.document_id,
        schema_version=value.schema_version,
        job_title=(
            SourcedTextFact(value.job_title.text, _evidence(value.job_title.evidence))
            if value.job_title
            else None
        ),
        responsibilities=tuple(
            TaskRequirementFact(item.requirement_id, item.text, _evidence(item.evidence))
            for item in value.responsibilities
        ),
        requirements=tuple(
            CandidateRequirementFact(
                requirement_id=item.requirement_id,
                kind=item.kind,
                modality=item.modality,
                evidence=_evidence(item.evidence),
                text=getattr(item, "text", None),
                items=tuple(
                    SkillItemFact(skill.name, skill.item_type)
                    for skill in getattr(item, "items", ())
                ),
                proficiency=getattr(item, "proficiency", None),
                minimum_degree=getattr(item, "minimum_degree", None),
                majors=tuple(getattr(item, "majors", ())),
                school_constraints=tuple(
                    getattr(item, "school_constraints", ())
                ),
                admission_type=getattr(item, "admission_type", None),
                graduation_year=getattr(item, "graduation_year", None),
                student_cohort=getattr(item, "student_cohort", None),
                minimum_years=getattr(item, "minimum_years", None),
                maximum_years=getattr(item, "maximum_years", None),
                domain=getattr(item, "domain", None),
                role=getattr(item, "role", None),
                duration_text=getattr(item, "duration_text", None),
                experience_unlimited=getattr(
                    item, "experience_unlimited", False
                ),
                certificates=tuple(getattr(item, "certificates", ())),
                skills=tuple(getattr(item, "skills", ())),
            )
            for item in value.requirements
        ),
        company_facts=tuple(
            CompanyFact(item.fact_id, item.text, _evidence(item.evidence))
            for item in value.company_facts
        ),
        employment_facts=tuple(
            EmploymentFact(
                item.fact_id, item.fact_type, item.text, _evidence(item.evidence)
            )
            for item in value.employment_facts
        ),
    )


def normalization_facts(value: JDNormalizedResult) -> NormalizationFacts:
    classification = value.job_classification
    return NormalizationFacts(
        document_id=value.document_id,
        schema_version=value.schema_version,
        job_classification=JobClassificationFact(
            schema_version=classification.schema_version,
            taxonomy_version=classification.taxonomy_version,
            source_title=classification.source_title,
            position_id=classification.position_id,
            position_code=classification.position_code,
            position_name=classification.position_name,
            family_code=classification.family_code,
            family_name=classification.family_name,
            candidate_positions=tuple(classification.candidate_positions),
            career_level=classification.career_level,
            leadership_scope=classification.leadership_scope,
            technology_focus_codes=tuple(classification.technology_focus_codes),
            industry_context_codes=tuple(classification.industry_context_codes),
            observed_skill_domain_codes=tuple(
                classification.observed_skill_domain_codes
            ),
            confidence=classification.confidence,
            classification_status=classification.classification_status,
            review_reason_codes=tuple(classification.review_reason_codes),
            evidence_refs=tuple(classification.evidence_refs),
            classification_policy_version=classification.classification_policy_version,
        ),
        normalized_requirements=tuple(
            NormalizedRequirementFact(
                requirement_id=item.requirement_id,
                kind=item.kind,
                normalized_skills=tuple(
                    NormalizedSkillFact(
                        source_name=skill.source_name,
                        resolution_status=skill.resolution_status,
                        resolution_source=skill.resolution_source,
                        skill_id=skill.skill_id,
                        canonical_name=skill.canonical_name,
                        category_code=skill.category_code,
                        subcategory_code=skill.subcategory_code,
                    )
                    for skill in item.normalized_skills
                ),
            )
            for item in value.normalized_requirements
        ),
        salary=(
            NormalizedSalaryFact(
                value.salary.currency,
                value.salary.minimum,
                value.salary.maximum,
                value.salary.period,
            )
            if value.salary
            else None
        ),
        unresolved_items=tuple(
            UnresolvedNormalizationFact(item.source_name, item.item_type, item.reason)
            for item in value.unresolved_items
        ),
    )
