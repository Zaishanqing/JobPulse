from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.position_taxonomy_v3 import (
    CandidatePosition,
    CareerLevel,
    IndustryContextCode,
    LeadershipScope,
    ObservedSkillDomainCode,
    TechnologyFocusCode,
)


ResolutionStatus = Literal["resolved", "manually_confirmed", "unresolved", "rejected"]
ResolutionSource = Literal[
    "explicit_mapping", "same_id", "canonical_name", "alias", "unresolved"
]


class NormalizedSkill(StrictContract):
    source_name: str
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    resolution_status: ResolutionStatus
    resolution_source: ResolutionSource = "unresolved"


class NormalizedRequirement(StrictContract):
    requirement_id: str
    kind: str
    normalized_skills: list[NormalizedSkill] = Field(default_factory=list)


class JobClassification(StrictContract):
    schema_version: Literal["job-position-classification.v3"] = (
        "job-position-classification.v3"
    )
    taxonomy_version: Literal["position-taxonomy.v3.0.0"] = (
        "position-taxonomy.v3.0.0"
    )
    source_title: str | None = None
    position_id: str | None = None
    position_code: str | None = None
    position_name: str | None = None
    family_code: str | None = None
    family_name: str | None = None
    candidate_positions: list[CandidatePosition] = Field(default_factory=list)
    career_level: CareerLevel | None = None
    leadership_scope: LeadershipScope | None = None
    technology_focus_codes: list[TechnologyFocusCode] = Field(default_factory=list)
    industry_context_codes: list[IndustryContextCode] = Field(default_factory=list)
    observed_skill_domain_codes: list[ObservedSkillDomainCode] = Field(
        default_factory=list
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    classification_status: Literal[
        "resolved",
        "manually_confirmed",
        "ambiguous",
        "out_of_scope",
        "catalog_gap",
    ] = "ambiguous"
    review_reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    classification_policy_version: str = "position-classifier.v3.0"

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "JobClassification":
        candidates = self.candidate_positions
        codes = [candidate.position_code for candidate in candidates]
        if len(codes) != len(set(codes)):
            raise ValueError("candidate position codes must be unique")
        for values, label in (
            (self.technology_focus_codes, "technology focus"),
            (self.industry_context_codes, "industry context"),
            (self.observed_skill_domain_codes, "observed skill domain"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} codes must be unique")

        resolved = self.classification_status in {"resolved", "manually_confirmed"}
        identity_fields = (
            self.position_code,
            self.position_name,
            self.family_code,
            self.family_name,
        )
        if resolved:
            if any(not value for value in identity_fields):
                raise ValueError("publishable classification identity is incomplete")
            if self.confidence is None:
                raise ValueError("publishable classification confidence is required")
            if not self.evidence_refs:
                raise ValueError("publishable classification evidence is required")
        elif self.position_id is not None or any(identity_fields):
            raise ValueError("unresolved classification must not bind a position")

        if self.classification_status == "resolved":
            if not candidates:
                raise ValueError("resolved classification requires candidates")
            top = candidates[0]
            if top.position_code != self.position_code:
                raise ValueError("resolved position must equal the top candidate")
            if top.score < 0.75 or self.confidence < 0.75:
                raise ValueError("resolved classification score is below policy")
            if len(candidates) > 1 and top.score - candidates[1].score < 0.08:
                raise ValueError("resolved candidate margin is below policy")
        elif self.classification_status == "ambiguous":
            if len(candidates) < 2:
                raise ValueError("ambiguous classification requires two candidates")
            if not self.review_reason_codes:
                raise ValueError("ambiguous classification requires a review reason")
        elif self.classification_status in {"out_of_scope", "catalog_gap"}:
            if not self.review_reason_codes:
                raise ValueError(
                    f"{self.classification_status} classification requires a reason"
                )
        return self


class NormalizedSalary(StrictContract):
    currency: str = "CNY"
    minimum: float | None = None
    maximum: float | None = None
    period: Literal["hour", "day", "month", "year", "unknown"] = "unknown"


class UnresolvedItem(StrictContract):
    source_name: str
    item_type: Literal["skill", "position"]
    reason: str


class JDNormalizedResult(StrictContract):
    schema_version: Literal["v2"] = "v2"
    document_id: str
    job_classification: JobClassification
    normalized_requirements: list[NormalizedRequirement] = Field(default_factory=list)
    salary: NormalizedSalary | None = None
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
