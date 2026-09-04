"""Strict external integration DTOs; upstream JSON is parsed here before mapping."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExternalDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExternalEvidence(ExternalDTO):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    alignment: Literal["exact", "normalized_exact", "unresolved"] = "unresolved"
    occurrence_index: int | None = Field(default=None, ge=0)


class ExternalUnresolved(ExternalDTO):
    item_id: str = Field(min_length=1)
    item_type: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalSourcedText(ExternalDTO):
    value: str = Field(min_length=1)
    evidence: ExternalEvidence


class ExternalExperience(ExternalDTO):
    experience_id: str = Field(min_length=1)
    kind: Literal["work", "project"]
    role: str | None = None
    responsibilities: tuple[ExternalSourcedText, ...] = ()
    business_scenarios: tuple[ExternalSourcedText, ...] = ()
    tool_source_item_ids: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalEducation(ExternalDTO):
    education_id: str = Field(min_length=1)
    degree_level: str | None = None
    field_of_study: str | None = None
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalCredential(ExternalDTO):
    credential_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    level: str | None = None
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalLanguage(ExternalDTO):
    language_code: str = Field(min_length=1)
    proficiency: str | None = None
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalResearchOutput(ExternalDTO):
    output_id: str = Field(min_length=1)
    output_type: Literal["publication", "patent", "research_output"]
    title: str = Field(min_length=1)
    status: str | None = None
    role: str | None = None
    order: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=1900, le=2200)
    date: str | None = None
    url: str | None = None
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalCVStructure(ExternalDTO):
    document_id: str = Field(min_length=1)
    education: tuple[ExternalEducation, ...] = ()
    work_experiences: tuple[ExternalExperience, ...] = ()
    projects: tuple[ExternalExperience, ...] = ()
    certificates: tuple[ExternalCredential, ...] = ()
    languages: tuple[ExternalLanguage, ...] = ()
    research_outputs: tuple[ExternalResearchOutput, ...] = ()
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalNormalizedSkill(ExternalDTO):
    source_item_id: str = Field(min_length=1)
    source_scope: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    normalization_confidence: float | None = Field(default=None, ge=0, le=1)
    resolution_source: Literal[
        "explicit_mapping", "same_id", "canonical_name", "alias",
        "legacy_unspecified", "unresolved"
    ] | None = None
    declared_level: str | None = None
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalCVNormalization(ExternalDTO):
    document_id: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)
    skills: tuple[ExternalNormalizedSkill, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def adapt_frozen_semantic_shadow_fixture(cls, value):
        """Add provenance only to the immutable pre-provenance v1 corpus."""
        if not isinstance(value, dict) or value.get("derivation_version") != (
            "cv-normalization.semantic-shadow.v1"
        ):
            return value
        adapted = dict(value)
        adapted_skills = []
        for skill in value.get("skills", ()):
            if not isinstance(skill, dict):
                adapted_skills.append(skill)
                continue
            item = dict(skill)
            if item.get("resolution_status") == "resolved":
                item.setdefault("normalization_confidence", 1.0)
                item.setdefault("resolution_source", "legacy_unspecified")
            adapted_skills.append(item)
        adapted["skills"] = adapted_skills
        return adapted


class ExternalMatchFeature(ExternalDTO):
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
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]
    evidence_refs: tuple[ExternalEvidence, ...] = ()
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)


class ExternalMatchFeatureResult(ExternalDTO):
    document_id: str = Field(min_length=1)
    as_of_date: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)
    features: tuple[ExternalMatchFeature, ...] = ()


class ExternalCapabilityProfile(ExternalDTO):
    profile_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    aggregation_key: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    declared_feature_ids: tuple[str, ...] = ()
    experience_skill_feature_ids: tuple[str, ...] = ()
    evidence_link_ids: tuple[str, ...] = ()
    declared_level: str | None = None
    demonstrated_level: Literal["unknown", "basic", "working", "proficient", "advanced", "expert"]
    demonstrated_level_label: str = Field(min_length=1)
    verification_status: Literal[
        "supported", "partially_supported", "not_observed", "experience_only", "unresolved"
    ]
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: Literal["none", "low", "medium", "high"]
    independent_experience_count: int = Field(ge=0)
    aggregate_support_score: int = Field(ge=0)
    evidence_bonus: float = Field(ge=0, le=1)
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]


class ExternalCapabilityLink(ExternalDTO):
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
    demonstrated_level: Literal["unknown", "basic", "working", "proficient", "advanced", "expert"]
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: Literal["none", "low", "medium", "high"]
    evidence_refs: tuple[ExternalEvidence, ...] = ()
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)


class ExternalCapabilityResult(ExternalDTO):
    document_id: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)
    profiles: tuple[ExternalCapabilityProfile, ...] = ()
    evidence_links: tuple[ExternalCapabilityLink, ...] = ()


class ExternalCVBundle(ExternalDTO):
    schema_version: Literal["cv-matching-input-bundle.v1"] = "cv-matching-input-bundle.v1"
    contract_version: Literal["cv-matching-input-bundle.v1"]
    source_system: str = Field(min_length=1)
    source_version: str = Field(default="legacy-unspecified", min_length=1)
    created_at: str = Field(default="1970-01-01T00:00:00+00:00", min_length=1)
    user_ref: str = Field(min_length=1)
    verification_snapshot_id: str = Field(min_length=1)
    review_status: Literal["pending", "reviewed", "approved", "rejected"]
    structure_derivation_version: str = Field(min_length=1)
    structure: ExternalCVStructure
    normalization: ExternalCVNormalization
    match_features: ExternalMatchFeatureResult
    capabilities: ExternalCapabilityResult
    unresolved_items: tuple[ExternalUnresolved, ...] = ()


class ExternalJDRequirement(ExternalDTO):
    requirement_id: str = Field(min_length=1)
    kind: Literal["skill", "tool", "education", "experience", "certificate", "soft_skill", "other"]
    modality: Literal["required", "preferred", "bonus", "unknown"]
    proficiency: Literal["know", "familiar", "proficient", "expert", "unknown"] | None = None
    minimum_degree: str | None = None
    minimum_years: float | None = Field(default=None, ge=0)
    certificates: tuple[str, ...] = ()
    evidence: ExternalEvidence


class ExternalJDResponsibility(ExternalDTO):
    requirement_id: str = Field(min_length=1)
    text: ExternalSourcedText


class ExternalJDExtraction(ExternalDTO):
    schema_version: Literal["v2"]
    document_id: str = Field(min_length=1)
    title: ExternalSourcedText
    responsibilities: tuple[ExternalJDResponsibility, ...] = ()
    requirements: tuple[ExternalJDRequirement, ...] = ()
    industries: tuple[ExternalSourcedText, ...] = ()
    locations: tuple[ExternalSourcedText, ...] = ()


class ExternalJDNormalizedSkill(ExternalDTO):
    source_name: str = Field(min_length=1)
    skill_id: str | None = None
    canonical_name: str | None = None
    resolution_status: Literal["resolved", "ambiguous", "unresolved"]


class ExternalJDNormalizedRequirement(ExternalDTO):
    requirement_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    modality: Literal["required", "preferred", "bonus", "unknown"]
    skills: tuple[ExternalJDNormalizedSkill, ...] = ()


class ExternalJDNormalization(ExternalDTO):
    schema_version: Literal["v2"]
    document_id: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    derivation_version: str = Field(min_length=1)
    position_code: str | None = None
    canonical_title: str | None = None
    classification_status: Literal[
        "resolved",
        "manually_confirmed",
        "ambiguous",
        "out_of_scope",
        "catalog_gap",
    ]
    career_level: str | None = None
    leadership_scope: str | None = None
    requirements: tuple[ExternalJDNormalizedRequirement, ...] = ()
    unresolved_items: tuple[ExternalUnresolved, ...] = ()


class ExternalPositionSkill(ExternalDTO):
    skill_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    importance_level: Literal["core", "required", "preferred", "bonus"]


class ExternalStandardPosition(ExternalDTO):
    snapshot_id: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    position_name: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    position_code: str = Field(min_length=1)
    sample_support_status: Literal["none", "sparse", "sufficient"]
    review_status: Literal["pending", "reviewed", "approved", "rejected"]
    required_skills: tuple[ExternalPositionSkill, ...] = ()
    bonus_skills: tuple[ExternalPositionSkill, ...] = ()
    business_scenarios: tuple[str, ...] = ()


class ExternalGraphSnapshot(ExternalDTO):
    snapshot_id: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    source_system: str = Field(min_length=1)


class ExternalQualitySnapshot(ExternalDTO):
    snapshot_id: str = Field(min_length=1)
    status: Literal["trusted", "review_required", "insufficient"]
    completeness: float = Field(ge=0, le=1)
    assessed_at: str = Field(min_length=1)
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalTrendSnapshot(ExternalDTO):
    snapshot_id: str = Field(min_length=1)
    window_start: str = Field(min_length=1)
    window_end: str = Field(min_length=1)
    trend_version: str = Field(min_length=1)
    signals: tuple[str, ...] = ()
    evidence: tuple[ExternalEvidence, ...] = ()


class ExternalPositionBundle(ExternalDTO):
    schema_version: Literal["position-matching-input-bundle.v1"] = (
        "position-matching-input-bundle.v1"
    )
    contract_version: Literal["position-matching-input-bundle.v1"]
    source_system: str = Field(min_length=1)
    source_version: str = Field(default="legacy-unspecified", min_length=1)
    created_at: str = Field(default="1970-01-01T00:00:00+00:00", min_length=1)
    jd_extraction: ExternalJDExtraction
    jd_normalization: ExternalJDNormalization
    standard_position: ExternalStandardPosition
    graph: ExternalGraphSnapshot
    quality: ExternalQualitySnapshot
    trend: ExternalTrendSnapshot | None = None


class UpstreamTimeoutError(RuntimeError):
    pass


class UpstreamResponseError(RuntimeError):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
