"""Immutable matching-profile contracts.

The capability contracts deliberately retain the field names and level semantics
published by CV Extraction.  This service does not derive a second capability
level and does not import another service's implementation package.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from jobgraph_contracts.evidence import Evidence as _SharedEvidence
from jobgraph_contracts.requirement_graph import (
    RequirementGraph as _SharedRequirementGraph,
)
from jobgraph_contracts.requirement_graph import (
    RequirementGraphChild,
    unknown_requirement_refs,
)
from jobgraph_contracts.requirement_graph import (
    RequirementGraphGroup as _SharedRequirementGraphGroup,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.privacy import find_pii

ResolutionStatus = Literal["resolved", "ambiguous", "unresolved"]
ReviewStatus = Literal[
    "pending",
    "reviewed",
    "approved",
    "rejected",
    "needs_human_review",
    "not_applicable",
]
DemonstratedLevel = Literal[
    "unknown", "basic", "working", "proficient", "advanced", "expert"
]
ConfidenceBand = Literal["none", "low", "medium", "high"]
CapabilityVerificationStatus = Literal[
    "supported",
    "partially_supported",
    "not_observed",
    "experience_only",
    "unresolved",
]


class ImmutableDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class Evidence(_SharedEvidence):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


class RequirementGraphGroup(_SharedRequirementGraphGroup):
    children: tuple[RequirementGraphChild, ...] = ()
    evidence: Evidence


class RequirementGraph(_SharedRequirementGraph):
    groups: tuple[RequirementGraphGroup, ...] = ()
    unresolved_items: tuple[str, ...] = ()


class UnresolvedItem(ImmutableDTO):
    item_id: str = Field(min_length=1)
    item_type: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_refs: tuple[Evidence, ...] = ()


class MatchFeature(ImmutableDTO):
    """Wire-compatible representation of CV Extraction's MatchFeature."""

    feature_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    side: Literal["cv"]
    feature_type: Literal[
        "skill",
        "task",
        "experience",
        "role",
        "education",
        "certificate",
        "language",
        "location",
        "salary",
        "soft_skill",
        "award",
        "self_evaluation",
        "work_status",
        "availability",
    ]
    source_object_id: str = Field(min_length=1)
    source_scope: str = Field(min_length=1)
    canonical_id: str | None = None
    canonical_name: str | None = None
    raw_text: str = Field(min_length=1)
    vector_text: str | None = None
    requirement_modality: Literal["required", "preferred", "bonus", "unknown"] | None = None
    candidate_level: str | None = None
    structured_values: dict[str, Any] = Field(default_factory=dict)
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)


class CapabilityEvidenceLink(ImmutableDTO):
    """Wire-compatible representation of CV Extraction's capability evidence link."""

    link_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    aggregation_key: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    declared_feature_ids: tuple[str, ...] = ()
    experience_skill_feature_id: str = Field(min_length=1)
    experience_feature_id: str = Field(min_length=1)
    supporting_task_feature_ids: tuple[str, ...] = ()
    support_signals: tuple[str, ...] = ()
    support_score: int = Field(ge=0)
    demonstrated_level: DemonstratedLevel
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: ConfidenceBand
    evidence_refs: tuple[Evidence, ...] = ()
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)


class CapabilityProfile(ImmutableDTO):
    """Wire-compatible representation of CV Extraction's capability profile."""

    profile_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    aggregation_key: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    declared_feature_ids: tuple[str, ...] = ()
    experience_skill_feature_ids: tuple[str, ...] = ()
    evidence_link_ids: tuple[str, ...] = ()
    declared_level: str | None = None
    demonstrated_level: DemonstratedLevel
    demonstrated_level_label: str = Field(min_length=1)
    verification_status: CapabilityVerificationStatus
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: ConfidenceBand
    independent_experience_count: int = Field(ge=0)
    aggregate_support_score: int = Field(ge=0)
    evidence_bonus: float = Field(ge=0, le=1)
    resolution_status: ResolutionStatus


class CVSkill(ImmutableDTO):
    aggregation_key: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    normalization_confidence: float = Field(ge=0, le=1)
    resolution_source: Literal[
        "explicit_mapping",
        "same_id",
        "canonical_name",
        "alias",
        "legacy_unspecified",
        "unresolved",
        "what_if_action",
    ]
    declared_level: str | None = None
    demonstrated_level: DemonstratedLevel
    verification_status: CapabilityVerificationStatus
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def require_resolved_identity(self) -> CVSkill:
        if self.resolution_status == "resolved" and not (
            self.skill_id and self.canonical_name
        ):
            raise ValueError("resolved skill requires skill_id and canonical_name")
        if self.resolution_status == "resolved" and self.resolution_source == "unresolved":
            raise ValueError("resolved skill requires normalization provenance")
        if self.resolution_status != "resolved" and self.resolution_source != "unresolved":
            raise ValueError("unresolved skill cannot retain resolved normalization provenance")
        return self

    @model_validator(mode="before")
    @classmethod
    def normalize_unresolved_provenance(cls, value: object) -> object:
        # A skill moved to human review no longer claims its previous automatic
        # resolution provenance. Normalize that transition before validation.
        if isinstance(value, dict) and value.get("resolution_status") != "resolved":
            return {**value, "resolution_source": "unresolved"}
        return value


class ExperienceFeature(ImmutableDTO):
    experience_id: str = Field(min_length=1)
    kind: Literal["project", "work"]
    role: str | None = None
    responsibilities: tuple[str, ...]
    business_scenarios: tuple[str, ...] = ()
    tool_skill_ids: tuple[str, ...] = ()
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    date_text: str | None = None
    evidence_refs: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def validate_dates(self) -> ExperienceFeature:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("experience end_date must not precede start_date")
        return self


class EducationFeature(ImmutableDTO):
    education_id: str = Field(min_length=1)
    degree_level: str | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    expected_graduation_date: date | None = None
    degree_status: Literal[
        "obtained", "enrolled", "expected", "future", "unknown"
    ] = "unknown"
    date_text: str | None = None
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def validate_dates(self) -> EducationFeature:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("education end_date must not precede start_date")
        if (
            self.expected_graduation_date is not None
            and self.start_date is not None
            and self.expected_graduation_date < self.start_date
        ):
            raise ValueError(
                "education expected_graduation_date must not precede start_date"
            )
        return self


class CredentialFeature(ImmutableDTO):
    credential_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    level: str | None = None
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()


class LanguageFeature(ImmutableDTO):
    language_code: str = Field(min_length=1)
    proficiency: str | None = None
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()


class ResearchOutputFeature(ImmutableDTO):
    output_id: str = Field(min_length=1)
    output_type: Literal["publication", "patent", "research_output"]
    title: str = Field(min_length=1)
    status: str | None = None
    role: str | None = None
    order: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=1900, le=2200)
    date: str | None = None
    url: str | None = None
    evidence_refs: tuple[Evidence, ...] = ()


class PositionClassificationRef(ImmutableDTO):
    taxonomy_version: str = Field(min_length=1)
    position_code: str | None = None
    classification_status: Literal[
        "resolved",
        "manually_confirmed",
        "ambiguous",
        "out_of_scope",
        "catalog_gap",
    ]
    career_level: str | None = None
    leadership_scope: str | None = None


class CVMatchProfile(ImmutableDTO):
    schema_version: Literal["cv-match-profile.v1"] = "cv-match-profile.v1"
    contract_version: Literal["cv-match-profile.v1"]
    source_version: str = Field(default="legacy-unspecified", min_length=1)
    created_at: datetime = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    cv_id: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    profile_version: str | None = Field(default=None, min_length=1)
    user_id: str = Field(min_length=1, description="Opaque non-PII user identifier")
    verification_snapshot_id: str = Field(min_length=1)
    as_of_date: date
    skills: tuple[CVSkill, ...]
    match_features: tuple[MatchFeature, ...]
    capability_profiles: tuple[CapabilityProfile, ...]
    capability_evidence_links: tuple[CapabilityEvidenceLink, ...]
    projects: tuple[ExperienceFeature, ...]
    work_experiences: tuple[ExperienceFeature, ...]
    education: tuple[EducationFeature, ...]
    certificates: tuple[CredentialFeature, ...]
    languages: tuple[LanguageFeature, ...]
    evidence_refs: tuple[Evidence, ...]
    unresolved_items: tuple[UnresolvedItem, ...]
    review_status: ReviewStatus
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)
    position_classifications: tuple[PositionClassificationRef, ...] = ()
    research_outputs: tuple[ResearchOutputFeature, ...] = ()

    @model_validator(mode="after")
    def validate_linkage(self) -> CVMatchProfile:
        if self.profile_id is None:
            object.__setattr__(self, "profile_id", self.cv_id)
        if self.profile_version is None:
            object.__setattr__(self, "profile_version", self.source_version)
        document_ids = {
            *(feature.document_id for feature in self.match_features),
            *(profile.document_id for profile in self.capability_profiles),
            *(link.document_id for link in self.capability_evidence_links),
        }
        if document_ids - {self.cv_id}:
            raise ValueError("CV contract document_id values must match cv_id")
        if any(item.kind != "project" for item in self.projects):
            raise ValueError("projects only accept kind='project'")
        if any(item.kind != "work" for item in self.work_experiences):
            raise ValueError("work_experiences only accept kind='work'")
        violations = find_pii(self.model_dump(mode="python"))
        if violations:
            raise ValueError(
                "PII is forbidden in matching profiles: "
                + ", ".join(item.path for item in violations)
            )
        return self


class PositionSkillRequirement(ImmutableDTO):
    requirement_id: str | None = Field(default=None, min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    required_level: str | None = None
    importance: float = Field(ge=0, le=1)
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def require_resolved_identity(self) -> PositionSkillRequirement:
        if self.resolution_status == "resolved" and not (
            self.skill_id and self.canonical_name
        ):
            raise ValueError("resolved requirement requires skill_id and canonical_name")
        return self


class HardCondition(ImmutableDTO):
    condition_id: str = Field(min_length=1)
    condition_type: Literal[
        "education",
        "experience",
        "certificate",
        "language",
        "location",
        "availability",
    ]
    operator: Literal["equals", "at_least", "one_of"]
    value: str = Field(min_length=1)
    resolution_status: ResolutionStatus
    evidence_refs: tuple[Evidence, ...] = ()


class PositionResponsibilityRequirement(ImmutableDTO):
    requirement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    skill_ids: tuple[str, ...] = ()
    resolution_status: ResolutionStatus = "resolved"
    evidence_refs: tuple[Evidence, ...] = ()


class PositionContext(ImmutableDTO):
    values: tuple[str, ...]
    evidence_refs: tuple[Evidence, ...] = ()
    availability: Literal["available", "unavailable"] = "available"

    @model_validator(mode="after")
    def unavailable_context_has_no_claimed_values(self) -> PositionContext:
        if self.availability == "unavailable" and (self.values or self.evidence_refs):
            raise ValueError("unavailable position context cannot contain values or evidence")
        return self


class QualityContext(ImmutableDTO):
    snapshot_id: str = Field(min_length=1)
    status: Literal["trusted", "review_required", "insufficient", "not_applicable"]
    completeness: float = Field(ge=0, le=1)
    assessed_at: date
    evidence_refs: tuple[Evidence, ...] = ()


class TrendContext(ImmutableDTO):
    snapshot_id: str = Field(min_length=1)
    window_start: date
    window_end: date
    trend_version: str = Field(min_length=1)
    signals: tuple[str, ...]
    evidence_refs: tuple[Evidence, ...] = ()


class PositionMatchProfile(ImmutableDTO):
    schema_version: Literal["position-match-profile.v1"] = "position-match-profile.v1"
    contract_version: Literal["position-match-profile.v1"]
    source_version: str = Field(default="legacy-unspecified", min_length=1)
    created_at: datetime = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    position_id: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    profile_version: str | None = Field(default=None, min_length=1)
    canonical_position_id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=1)
    core_responsibilities: tuple[str, ...]
    responsibility_requirements: tuple[PositionResponsibilityRequirement, ...] = ()
    required_skills: tuple[PositionSkillRequirement, ...]
    preferred_skills: tuple[PositionSkillRequirement, ...]
    hard_conditions: tuple[HardCondition, ...]
    tools: PositionContext
    industries: PositionContext
    business_scenarios: PositionContext
    evidence_refs: tuple[Evidence, ...]
    quality_context: QualityContext
    trend_context: TrendContext | None
    unresolved_items: tuple[UnresolvedItem, ...]
    requirement_graph: RequirementGraph | None = None
    graph_mode: Literal["enabled", "disabled"] = "enabled"
    review_status: ReviewStatus
    taxonomy_version: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    position_code: str = Field(min_length=1)
    classification_status: Literal["resolved", "manually_confirmed"]
    career_level: str | None = None
    leadership_scope: str | None = None
    sample_support_status: Literal["sufficient"]

    @model_validator(mode="after")
    def reject_pii(self) -> PositionMatchProfile:
        if self.profile_id is None:
            object.__setattr__(self, "profile_id", self.position_id)
        if self.profile_version is None:
            object.__setattr__(self, "profile_version", self.source_version)
        if self.requirement_graph is not None:
            known_requirement_ids = {
                requirement.requirement_id
                for requirement in (*self.required_skills, *self.preferred_skills)
                if requirement.requirement_id is not None
            }
            known_requirement_ids.update(
                condition.condition_id for condition in self.hard_conditions
            )
            known_requirement_ids.update(
                item.requirement_id for item in self.responsibility_requirements
            )
            known_requirement_ids.update(
                f"responsibility:{index + 1}"
                for index in range(len(self.core_responsibilities))
            )
            missing = unknown_requirement_refs(
                self.requirement_graph, known_requirement_ids
            )
            if missing:
                raise ValueError(
                    "requirement_graph references unknown requirements: "
                    + ", ".join(missing)
                )
        violations = find_pii(
            self.model_dump(mode="python"),
            technical_context_allowed=bool(self.responsibility_requirements),
        )
        if violations:
            raise ValueError(
                "PII is forbidden in matching profiles: "
                + ", ".join(item.path for item in violations)
            )
        return self
