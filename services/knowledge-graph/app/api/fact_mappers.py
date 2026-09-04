"""Map validated HTTP contracts to framework-free application/domain values."""

from __future__ import annotations

import hashlib
import json

from app.api.contracts import JDCreate
from jobgraph_contracts.published_jd import PublishedJDFactV3
from app.domain.structured_facts import (
    CandidateRequirementFact,
    CompanyFact,
    EmploymentFact,
    EvidenceFact,
    ExtractionFacts,
    JDDocumentInput,
    JobClassificationFact,
    NormalizationFacts,
    NormalizedRequirementFact,
    NormalizedSalaryFact,
    NormalizedSkillFact,
    PublishedEvidenceFact,
    PublishedJDFact,
    PublishedPositionFact,
    PublishedRequirementFact,
    PublishedSkillFact,
    SkillItemFact,
    SourcedTextFact,
    TaskRequirementFact,
    UnresolvedNormalizationFact,
)
from app.schemas.extraction import Evidence, JDExtractionResult
from app.schemas.normalization import JDNormalizedResult


def jd_document_input(body: JDCreate, *, document_id: str | None = None) -> JDDocumentInput:
    return JDDocumentInput(
        document_id=document_id if document_id is not None else body.document_id,
        raw_text=body.raw_text,
        source_type=body.source_type,
        source_name=body.source_name,
        enterprise_name=body.enterprise_name,
        published_at=body.published_at,
        source_credibility=body.source_credibility,
        is_synthetic=body.is_synthetic,
    )


def _evidence(value: Evidence) -> EvidenceFact:
    return EvidenceFact(
        value.source_id,
        value.quote,
        value.start,
        value.end,
        value.alignment,
        value.occurrence_index,
    )


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
                item.requirement_id,
                item.kind,
                tuple(
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


def published_jd_fact(body: PublishedJDFactV3) -> PublishedJDFact:
    raw = body.model_dump(mode="json")
    content_version = hashlib.sha256(
        json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    extraction = JDExtractionResult.model_validate(raw["extraction_fact"])
    normalization = JDNormalizedResult.model_validate(raw["normalized_fact"])

    def split(value, key):
        return value[key], {name: item for name, item in value.items() if name != key}

    position_code, position_extensions = split(
        raw["position_fact"], "position_code"
    )
    skills = []
    for value in raw["skill_facts"]:
        skill_id, extensions = split(value, "skill_id")
        skills.append(PublishedSkillFact(skill_id, extensions))
    requirements = []
    for value in raw["requirement_facts"]:
        requirement_id, extensions = split(value, "requirement_id")
        requirements.append(PublishedRequirementFact(requirement_id, extensions))
    evidence = []
    evidence_fields = {
        "source_id", "quote", "start", "end", "alignment", "occurrence_index"
    }
    for value in raw["evidence"]:
        evidence.append(
            PublishedEvidenceFact(
                source_id=value["source_id"],
                quote=value["quote"],
                start=value.get("start"),
                end=value.get("end"),
                alignment=value.get("alignment", "unresolved"),
                occurrence_index=value.get("occurrence_index"),
                extensions={
                    key: item for key, item in value.items() if key not in evidence_fields
                },
            )
        )
    return PublishedJDFact(
        contract_version=raw["contract_version"],
        schema_version=raw["schema_version"],
        source_system=raw["source_system"],
        source_jd_id=raw["source_jd_id"],
        source_fact_id=raw["source_fact_id"],
        source_fact_version=raw["source_fact_version"],
        review_status=raw["review_status"],
        published_at=raw["published_at"],
        source_version=content_version,
        position_fact=PublishedPositionFact(position_code, position_extensions),
        skill_facts=tuple(skills),
        requirement_facts=tuple(requirements),
        education_fact=raw["education_fact"],
        experience_fact=raw["experience_fact"],
        industry_fact=raw["industry_fact"],
        company_facts=tuple(raw["company_facts"]),
        employment_facts=tuple(raw["employment_facts"]),
        evidence=tuple(evidence),
        extraction_fact=extraction_facts(extraction),
        normalized_fact=normalization_facts(normalization),
        trace_metadata=raw["trace_metadata"],
    )
