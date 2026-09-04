from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.requirement_graph import (
    RequirementGraph,
    RequirementGraphChild,
    RequirementGraphGroup,
    unknown_requirement_refs,
)
from .field_contract import (
    AdmissionType,
    CompanyFactKind,
    DegreeLevel,
    EmploymentFactKind,
    Modality,
    Proficiency,
    RequirementKind,
    ResolutionStatus,
    SalaryCurrency,
    SalaryPeriod,
    SalaryStatus,
    SkillItemType,
)


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @model_validator(mode="after")
    def reject_empty_strings(self) -> "StrictBaseModel":
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{field_name} must not be an empty string")
        return self


class SourcedText(StrictBaseModel):
    value: str
    evidence: Evidence


class SkillItem(StrictBaseModel):
    name: str
    item_type: SkillItemType


class RequirementBase(StrictBaseModel):
    requirement_id: str
    modality: Modality
    evidence: Evidence


class TaskRequirement(RequirementBase):
    kind: Literal["task"]
    action: str


class SkillRequirement(RequirementBase):
    kind: Literal["skill"]
    items: list[SkillItem]
    proficiency: Proficiency | None = None

    @model_validator(mode="after")
    def require_items(self) -> "SkillRequirement":
        if not self.items:
            raise ValueError("skill requirement items must not be empty")
        return self


class ToolRequirement(RequirementBase):
    kind: Literal["tool"]
    tools: list[str]

    @model_validator(mode="after")
    def require_tools(self) -> "ToolRequirement":
        if not self.tools:
            raise ValueError("tools must not be empty")
        return self


class EducationRequirement(RequirementBase):
    kind: Literal["education"]
    minimum_degree: DegreeLevel | None = None
    majors: list[str] = Field(default_factory=list)
    school_constraints: list[str] = Field(default_factory=list)
    admission_type: AdmissionType | None = None
    graduation_year: int | None = None
    student_cohort: str | None = None


class ExperienceRequirement(RequirementBase):
    kind: Literal["experience"]
    minimum_years: float | None = None
    maximum_years: float | None = None
    domain: str | None = None
    role: str | None = None
    duration_text: str | None = None
    experience_unlimited: bool = False

    @model_validator(mode="after")
    def validate_experience_range(self) -> "ExperienceRequirement":
        if (
            self.minimum_years is not None
            and self.maximum_years is not None
            and self.minimum_years > self.maximum_years
        ):
            raise ValueError("experience minimum_years must not exceed maximum_years")
        if self.experience_unlimited and (
            self.minimum_years is not None or self.maximum_years is not None
        ):
            raise ValueError("experience_unlimited cannot coexist with year bounds")
        return self


class CertificateRequirement(RequirementBase):
    kind: Literal["certificate"]
    certificates: list[str]

    @model_validator(mode="after")
    def require_certificates(self) -> "CertificateRequirement":
        if not self.certificates:
            raise ValueError("certificates must not be empty")
        return self


class SoftSkillRequirement(RequirementBase):
    kind: Literal["soft_skill"]
    skills: list[str]

    @model_validator(mode="after")
    def require_skills(self) -> "SoftSkillRequirement":
        if not self.skills:
            raise ValueError("soft skills must not be empty")
        return self


class OtherRequirement(RequirementBase):
    kind: Literal["other"]
    label: str
    value: str | None = None


CandidateRequirement = Annotated[
    SkillRequirement
    | ToolRequirement
    | EducationRequirement
    | ExperienceRequirement
    | CertificateRequirement
    | SoftSkillRequirement
    | OtherRequirement,
    Field(discriminator="kind"),
]


class CompanyFact(StrictBaseModel):
    fact_id: str
    kind: CompanyFactKind
    value: str
    evidence: Evidence


class EmploymentFact(StrictBaseModel):
    fact_id: str
    kind: EmploymentFactKind
    value: str
    evidence: Evidence


RequirementGroupChild = RequirementGraphChild
RequirementGroup = RequirementGraphGroup


class JDExtractionResult(StrictBaseModel):
    document_id: str
    job_title: SourcedText | None = None
    responsibilities: list[TaskRequirement] = Field(default_factory=list)
    requirements: list[CandidateRequirement] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    employment_facts: list[EmploymentFact] = Field(default_factory=list)
    requirement_graph: RequirementGraph | None = None

    @model_validator(mode="after")
    def validate_generated_ids(self) -> "JDExtractionResult":
        ids = [requirement.requirement_id for requirement in self.responsibilities]
        ids.extend(requirement.requirement_id for requirement in self.requirements)
        fact_ids = [fact.fact_id for fact in self.company_facts]
        fact_ids.extend(fact.fact_id for fact in self.employment_facts)
        if len(ids) != len(set(ids)):
            raise ValueError("requirement_id must be unique within a document")
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id must be unique within a document")
        if self.requirement_graph is not None:
            missing = unknown_requirement_refs(self.requirement_graph, ids)
            if missing:
                raise ValueError(
                    "requirement_graph references unknown requirements: "
                    + ", ".join(missing)
                )
        return self


class NormalizedSkill(StrictBaseModel):
    source_name: str
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: SkillItemType
    subcategory_code: str | None = None
    resolution_status: ResolutionStatus


class NormalizedRequirement(StrictBaseModel):
    requirement_id: str
    kind: RequirementKind
    modality: Modality
    skills: list[NormalizedSkill] = Field(default_factory=list)


class JobClassification(StrictBaseModel):
    schema_version: Literal["job-position-classification.v3"] = (
        "job-position-classification.v3"
    )
    taxonomy_version: str
    source_title: str | None = None
    position_code: str | None = None
    position_name: str | None = None
    family_code: str | None = None
    family_name: str | None = None
    candidate_positions: list[dict[str, str | float]] = Field(default_factory=list)
    career_level: str | None = None
    leadership_scope: str | None = None
    technology_focus_codes: list[str] = Field(default_factory=list)
    industry_context_codes: list[str] = Field(default_factory=list)
    observed_skill_domain_codes: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    classification_status: Literal[
        "resolved",
        "manually_confirmed",
        "ambiguous",
        "out_of_scope",
        "catalog_gap",
    ]
    review_reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    classification_policy_version: str


class Salary(StrictBaseModel):
    raw_text: str
    status: SalaryStatus = "specified"
    minimum: float | int | None = Field(default=None, ge=0)
    maximum: float | int | None = Field(default=None, ge=0)
    currency: SalaryCurrency | None = None
    period: SalaryPeriod | None = None
    salary_months: float | int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> "Salary":
        if self.status == "specified":
            if (
                self.minimum is None
                or self.maximum is None
                or self.currency is None
                or self.period is None
            ):
                raise ValueError(
                    "specified salary requires minimum, maximum, currency and period"
                )
            if self.minimum > self.maximum:
                raise ValueError("salary minimum must not exceed maximum")
            if self.salary_months is not None and self.salary_months < 1:
                raise ValueError("salary_months must be at least 1")
            return self
        if any(
            value is not None
            for value in (
                self.minimum,
                self.maximum,
                self.currency,
                self.period,
                self.salary_months,
            )
        ):
            raise ValueError(
                f"{self.status} salary must not contain numeric or period fields"
            )
        return self


class JDNormalizedResult(StrictBaseModel):
    document_id: str
    job_classification: JobClassification | None = None
    normalized_requirements: list[NormalizedRequirement] = Field(default_factory=list)
    salary: Salary | None = None
    unresolved_items: list[str] = Field(default_factory=list)
