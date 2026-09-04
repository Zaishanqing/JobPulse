from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.evidence import Evidence, EvidenceAlignment
from jobgraph_contracts.requirement_graph import (
    RequirementGraph,
    unknown_requirement_refs,
)


Modality = Literal["required", "preferred", "bonus", "unknown"]
Alignment = EvidenceAlignment


class SourcedText(StrictContract):
    text: str
    evidence: Evidence


class TaskRequirement(StrictContract):
    requirement_id: str
    text: str
    evidence: Evidence


class RequirementBase(StrictContract):
    requirement_id: str
    modality: Modality = "unknown"
    evidence: Evidence


class SkillItem(StrictContract):
    name: str
    item_type: Literal[
        "technology", "tool", "language", "framework", "platform", "method", "other"
    ] = "technology"


class SkillRequirement(RequirementBase):
    kind: Literal["skill"]
    items: list[SkillItem]
    proficiency: str | None = None


class ToolRequirement(RequirementBase):
    kind: Literal["tool"]
    tools: list[str]


class TextRequirement(RequirementBase):
    text: str


class EducationRequirement(RequirementBase):
    kind: Literal["education"]
    text: str | None = None
    minimum_degree: str | None = None
    majors: list[str] = Field(default_factory=list)
    school_constraints: list[str] = Field(default_factory=list)
    admission_type: str | None = None
    graduation_year: int | None = None
    student_cohort: str | None = None


class ExperienceRequirement(RequirementBase):
    kind: Literal["experience"]
    text: str | None = None
    minimum_years: float | None = None
    maximum_years: float | None = None
    domain: str | None = None
    role: str | None = None
    duration_text: str | None = None
    experience_unlimited: bool = False


class CertificateRequirement(RequirementBase):
    kind: Literal["certificate"]
    text: str | None = None
    certificates: list[str] = Field(default_factory=list)


class SoftSkillRequirement(RequirementBase):
    kind: Literal["soft_skill"]
    text: str | None = None
    skills: list[str] = Field(default_factory=list)


class OtherRequirement(TextRequirement):
    kind: Literal["other"]


CandidateRequirement = Annotated[
    Union[
        SkillRequirement,
        ToolRequirement,
        EducationRequirement,
        ExperienceRequirement,
        CertificateRequirement,
        SoftSkillRequirement,
        OtherRequirement,
    ],
    Field(discriminator="kind"),
]


class CompanyFact(StrictContract):
    fact_id: str
    text: str
    evidence: Evidence


class EmploymentFact(StrictContract):
    fact_id: str
    fact_type: Literal["location", "employment_type", "salary", "headcount", "schedule", "other"]
    text: str
    evidence: Evidence


class JDExtractionResult(StrictContract):
    schema_version: Literal["v2"] = "v2"
    document_id: str
    job_title: SourcedText | None = None
    responsibilities: list[TaskRequirement] = Field(default_factory=list)
    requirements: list[CandidateRequirement] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    employment_facts: list[EmploymentFact] = Field(default_factory=list)
    requirement_graph: RequirementGraph | None = None

    @model_validator(mode="after")
    def validate_requirement_graph_references(self) -> "JDExtractionResult":
        if self.requirement_graph is None:
            return self
        known = {
            requirement.requirement_id for requirement in self.requirements
        } | {
            requirement.requirement_id for requirement in self.responsibilities
        }
        missing = unknown_requirement_refs(self.requirement_graph, known)
        if missing:
            raise ValueError(
                "requirement_graph references unknown requirements: "
                + ", ".join(missing)
            )
        return self
