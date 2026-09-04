"""Framework-free document, extraction, normalization, and published-fact values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.value_types import ExtensionAttributes


@dataclass(frozen=True)
class JDDocumentInput:
    raw_text: str
    document_id: str | None = None
    source_type: str = "manual"
    source_name: str | None = None
    enterprise_name: str | None = None
    published_at: datetime | None = None
    source_credibility: float = 1.0
    is_synthetic: bool = False


@dataclass(frozen=True)
class EvidenceFact:
    source_id: str
    quote: str
    start: int | None = None
    end: int | None = None
    alignment: str = "unresolved"
    occurrence_index: int | None = None


@dataclass(frozen=True)
class SourcedTextFact:
    text: str
    evidence: EvidenceFact


@dataclass(frozen=True)
class TaskRequirementFact:
    requirement_id: str
    text: str
    evidence: EvidenceFact


@dataclass(frozen=True)
class SkillItemFact:
    name: str
    item_type: str = "technology"


@dataclass(frozen=True)
class CandidateRequirementFact:
    requirement_id: str
    kind: str
    modality: str
    evidence: EvidenceFact
    text: str | None = None
    items: tuple[SkillItemFact, ...] = ()
    proficiency: str | None = None
    minimum_degree: str | None = None
    majors: tuple[str, ...] = ()
    school_constraints: tuple[str, ...] = ()
    admission_type: str | None = None
    graduation_year: int | None = None
    student_cohort: str | None = None
    minimum_years: float | None = None
    maximum_years: float | None = None
    domain: str | None = None
    role: str | None = None
    duration_text: str | None = None
    experience_unlimited: bool = False
    certificates: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyFact:
    fact_id: str
    text: str
    evidence: EvidenceFact


@dataclass(frozen=True)
class EmploymentFact:
    fact_id: str
    fact_type: str
    text: str
    evidence: EvidenceFact


@dataclass(frozen=True)
class ExtractionFacts:
    document_id: str
    schema_version: str = "v2"
    job_title: SourcedTextFact | None = None
    responsibilities: tuple[TaskRequirementFact, ...] = ()
    requirements: tuple[CandidateRequirementFact, ...] = ()
    company_facts: tuple[CompanyFact, ...] = ()
    employment_facts: tuple[EmploymentFact, ...] = ()


@dataclass(frozen=True)
class NormalizedSkillFact:
    source_name: str
    resolution_status: str
    resolution_source: str = "unresolved"
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None


@dataclass(frozen=True)
class NormalizedRequirementFact:
    requirement_id: str
    kind: str
    normalized_skills: tuple[NormalizedSkillFact, ...] = ()


@dataclass(frozen=True)
class JobClassificationFact:
    schema_version: str = "job-position-classification.v3"
    taxonomy_version: str = "position-taxonomy.v3.0.0"
    source_title: str | None = None
    position_id: str | None = None
    position_code: str | None = None
    position_name: str | None = None
    family_code: str | None = None
    family_name: str | None = None
    candidate_positions: tuple[dict[str, str | float], ...] = ()
    career_level: str | None = None
    leadership_scope: str | None = None
    technology_focus_codes: tuple[str, ...] = ()
    industry_context_codes: tuple[str, ...] = ()
    observed_skill_domain_codes: tuple[str, ...] = ()
    confidence: float | None = None
    classification_status: str = "catalog_gap"
    review_reason_codes: tuple[str, ...] = (
        "AUTHORITATIVE_POSITION_CLASSIFICATION_REQUIRED",
    )
    evidence_refs: tuple[str, ...] = ()
    classification_policy_version: str = "position-classifier.v3.0"


@dataclass(frozen=True)
class NormalizedSalaryFact:
    currency: str = "CNY"
    minimum: float | None = None
    maximum: float | None = None
    period: str = "unknown"


@dataclass(frozen=True)
class UnresolvedNormalizationFact:
    source_name: str
    item_type: str
    reason: str


@dataclass(frozen=True)
class NormalizationFacts:
    document_id: str
    job_classification: JobClassificationFact
    schema_version: str = "v2"
    normalized_requirements: tuple[NormalizedRequirementFact, ...] = ()
    salary: NormalizedSalaryFact | None = None
    unresolved_items: tuple[UnresolvedNormalizationFact, ...] = ()


@dataclass(frozen=True)
class SavedExtractionFacts:
    record_id: int


@dataclass(frozen=True)
class SavedNormalizationFacts:
    record_id: int
    facts: NormalizationFacts


@dataclass(frozen=True)
class PublishedPositionFact:
    position_id: str
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class PublishedSkillFact:
    skill_id: str
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class PublishedRequirementFact:
    requirement_id: str
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class PublishedEvidenceFact:
    source_id: str
    quote: str
    start: int | None = None
    end: int | None = None
    alignment: str = "unresolved"
    occurrence_index: int | None = None
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class PublishedJDFact:
    contract_version: str
    schema_version: str
    source_system: str
    source_jd_id: str
    source_fact_id: str
    source_fact_version: str
    review_status: str
    published_at: str
    source_version: str
    position_fact: PublishedPositionFact
    skill_facts: tuple[PublishedSkillFact, ...]
    requirement_facts: tuple[PublishedRequirementFact, ...]
    education_fact: str | None
    experience_fact: str | None
    industry_fact: str | None
    company_facts: tuple[ExtensionAttributes, ...]
    employment_facts: tuple[ExtensionAttributes, ...]
    evidence: tuple[PublishedEvidenceFact, ...]
    extraction_fact: ExtractionFacts
    normalized_fact: NormalizationFacts
    trace_metadata: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class PublishedFactImportResult:
    contract_version: str
    document_id: str
    source_fact_id: str
    source_fact_version: str
    source_version: str
    idempotent: bool
    stale: bool
