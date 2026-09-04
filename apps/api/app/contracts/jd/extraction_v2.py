"""Immutable V2 extraction contract. Future versions live beside this module."""

from typing import Annotated, Literal

from pydantic import Field

from app.contracts.jd.evidence import Evidence, StrictModel

Modality = Literal["required", "preferred", "bonus", "unknown"]
SkillItemType = Literal[
    "programming_language",
    "framework",
    "library",
    "database",
    "tool",
    "platform",
    "methodology",
    "domain_knowledge",
    "other",
]


class SourcedText(StrictModel):
    value: str
    evidence: Evidence


class SkillItem(StrictModel):
    name: str
    item_type: SkillItemType = "other"


class RequirementBase(StrictModel):
    requirement_id: str
    modality: Modality = "unknown"
    evidence: Evidence


class TaskRequirement(RequirementBase):
    kind: Literal["task"] = "task"
    action: str


class SkillRequirement(RequirementBase):
    kind: Literal["skill"] = "skill"
    items: list[SkillItem]
    proficiency: str | None = None


class ToolRequirement(RequirementBase):
    kind: Literal["tool"] = "tool"
    tools: list[str]


class EducationRequirement(RequirementBase):
    kind: Literal["education"] = "education"
    text: str | None = None
    minimum_degree: str | None = None
    majors: list[str] = Field(default_factory=list)
    school_constraints: list[str] = Field(default_factory=list)
    admission_type: str | None = None
    graduation_year: int | None = None
    student_cohort: str | None = None


class ExperienceRequirement(RequirementBase):
    kind: Literal["experience"] = "experience"
    text: str | None = None
    minimum_years: float | None = None
    maximum_years: float | None = None
    domain: str | None = None
    role: str | None = None
    duration_text: str | None = None
    experience_unlimited: bool = False


class CertificateRequirement(RequirementBase):
    kind: Literal["certificate"] = "certificate"
    text: str | None = None
    certificates: list[str] = Field(default_factory=list)


class SoftSkillRequirement(RequirementBase):
    kind: Literal["soft_skill"] = "soft_skill"
    text: str | None = None
    skills: list[str] = Field(default_factory=list)


class OtherRequirement(RequirementBase):
    kind: Literal["other"] = "other"
    label: str
    value: str


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
CompanyFactKind = Literal[
    "company_name", "industry", "company_size", "ownership", "location", "business", "other"
]
EmploymentFactKind = Literal[
    "salary",
    "work_location",
    "location",
    "employment_type",
    "work_schedule",
    "schedule",
    "benefit",
    "training",
    "headcount",
    "other",
]


class CompanyFact(StrictModel):
    fact_id: str
    kind: CompanyFactKind
    value: str
    evidence: Evidence


class EmploymentFact(StrictModel):
    fact_id: str
    kind: EmploymentFactKind
    value: str
    evidence: Evidence


class JDExtractionResult(StrictModel):
    schema_version: Literal["v2"] = "v2"
    document_id: str
    job_title: SourcedText | None = None
    responsibilities: list[TaskRequirement] = Field(default_factory=list)
    requirements: list[CandidateRequirement] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    employment_facts: list[EmploymentFact] = Field(default_factory=list)
