from typing import Literal

from pydantic import Field, model_validator

from app.contracts.jd.evidence import StrictModel
from app.contracts.position_taxonomy_v3 import (
    CandidatePosition,
    CareerLevel,
    IndustryContextCode,
    LeadershipScope,
    ObservedSkillDomainCode,
    TechnologyFocusCode,
)


class JobClassification(StrictModel):
    schema_version: Literal["job-position-classification.v3"] = "job-position-classification.v3"
    taxonomy_version: Literal["position-taxonomy.v3.0.0"] = "position-taxonomy.v3.0.0"
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
    observed_skill_domain_codes: list[ObservedSkillDomainCode] = Field(default_factory=list)
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

        resolved = self.classification_status in {
            "resolved",
            "manually_confirmed",
        }
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
                raise ValueError(f"{self.classification_status} classification requires a reason")
        return self


class NormalizedSkill(StrictModel):
    source_name: str
    requirement_id: str | None = None
    requirement_kind: str | None = None
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    resolution_status: Literal[
        "resolved", "manually_confirmed", "unresolved", "conflict", "rejected"
    ]
    resolution_source: str | None = None
    source_skill_id: str | None = None
    source_canonical_name: str | None = None
    source_category_code: str | None = None
    source_subcategory_code: str | None = None
    source_resolution_status: str | None = None
    source_resolution_source: str | None = None


class SalaryNormalization(StrictModel):
    raw_value: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None


class UnresolvedItem(StrictModel):
    item_type: Literal["skill", "job_title"]
    source_value: str
    reason: str
    severity: Literal["warning", "blocking"] = "warning"
    source: Literal["normalization", "manual_review", "edit_conflict"] = "normalization"
    code: str | None = None
    details: dict[str, object] | None = None


class JDNormalizedResult(StrictModel):
    schema_version: Literal["v2"] = "v2"
    document_id: str
    job_classification: JobClassification | None = None
    normalized_requirements: list[NormalizedSkill] = Field(default_factory=list)
    salary: SalaryNormalization | None = None
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
