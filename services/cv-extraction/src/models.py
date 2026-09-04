from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from jobgraph_contracts.evidence import Evidence

from .field_contract import (
    AwardLevel,
    CertificateKind,
    DegreeLevel,
    EducationFieldEvidenceName,
    Gender,
    LanguageProficiency,
    Proficiency,
    PersonalFieldEvidenceName,
    ProjectFieldEvidenceName,
    ResolutionStatus,
    SkillItemType,
    WorkStatus,
    WorkFieldEvidenceName,
    WorkType,
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


class PersonalFieldEvidence(StrictBaseModel):
    field_name: PersonalFieldEvidenceName
    evidence: Evidence


class EducationFieldEvidence(StrictBaseModel):
    field_name: EducationFieldEvidenceName
    evidence: Evidence


class WorkFieldEvidence(StrictBaseModel):
    field_name: WorkFieldEvidenceName
    evidence: Evidence


class ProjectFieldEvidence(StrictBaseModel):
    field_name: ProjectFieldEvidenceName
    evidence: Evidence


def _validate_field_evidence_shape(
    *,
    object_type: str,
    values: dict[str, Any],
    bindings: list[Any],
) -> None:
    expected = {
        field_name
        for field_name, value in values.items()
        if value is not None
        and not (field_name in {"degree", "work_type", "work_status"} and value == "unknown")
    }
    actual = [binding.field_name for binding in bindings]
    duplicates = sorted(name for name, count in Counter(actual).items() if count > 1)
    missing = sorted(expected.difference(actual))
    unexpected = sorted(set(actual).difference(expected))
    if missing or duplicates or unexpected:
        raise ValueError(
            f"{object_type} field_evidence mismatch: missing={missing}, "
            f"duplicate={duplicates}, unexpected={unexpected}"
        )


class DateRange(StrictBaseModel):
    start: str | None = None
    end: str | None = None
    duration_text: str | None = None


class SkillItem(StrictBaseModel):
    item_id: str
    name: str
    item_type: SkillItemType
    proficiency: Proficiency | None = None
    evidence: Evidence


class PersonalInfo(StrictBaseModel):
    name: str | None = None
    gender: Gender | None = None
    birth_year: int | None = None
    phone: str | None = None
    email: str | None = None
    current_location: str | None = None
    expected_location: str | None = None
    expected_position: str | None = None
    expected_salary: str | None = None
    work_status: WorkStatus | None = None
    available_date: str | None = None
    evidence: Evidence
    field_evidence: list[PersonalFieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_evidence_shape(self) -> "PersonalInfo":
        _validate_field_evidence_shape(
            object_type="personal_info",
            values={
                name: getattr(self, name)
                for name in (
                    "current_location", "expected_location", "expected_position",
                    "expected_salary", "work_status", "available_date",
                )
            },
            bindings=self.field_evidence,
        )
        return self


class EducationEntry(StrictBaseModel):
    entry_id: str
    school: str
    college: str | None = None
    major: str
    degree: DegreeLevel
    date: DateRange | None = None
    gpa: str | None = None
    gpa_scale: str | None = None
    location: str | None = None
    school_tag: str | None = None
    evidence: Evidence
    field_evidence: list[EducationFieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_evidence_shape(self) -> "EducationEntry":
        _validate_field_evidence_shape(
            object_type="education",
            values={
                "school": self.school, "college": self.college, "major": self.major,
                "degree": self.degree, "date": self.date, "gpa": self.gpa,
                "gpa_scale": self.gpa_scale, "location": self.location,
                "school_tag": self.school_tag,
            },
            bindings=self.field_evidence,
        )
        return self


class WorkEntry(StrictBaseModel):
    entry_id: str
    company: str
    position: str | None = None
    date: DateRange | None = None
    department: str | None = None
    location: str | None = None
    work_type: WorkType | None = None
    tech_stack: list[SkillItem] = Field(default_factory=list)
    responsibilities: list[SourcedText] = Field(default_factory=list)
    achievements: list[SourcedText] = Field(default_factory=list)
    evidence: Evidence
    field_evidence: list[WorkFieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_evidence_shape(self) -> "WorkEntry":
        _validate_field_evidence_shape(
            object_type="work_experience",
            values={
                "company": self.company, "position": self.position, "date": self.date,
                "department": self.department, "location": self.location,
                "work_type": self.work_type,
            },
            bindings=self.field_evidence,
        )
        return self


class ProjectEntry(StrictBaseModel):
    entry_id: str
    name: str
    date: DateRange | None = None
    role: str | None = None
    affiliation: str | None = None
    description: SourcedText | None = None
    tech_stack: list[SkillItem] = Field(default_factory=list)
    highlights: list[SourcedText] = Field(default_factory=list)
    evidence: Evidence
    field_evidence: list[ProjectFieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_evidence_shape(self) -> "ProjectEntry":
        _validate_field_evidence_shape(
            object_type="project_experience",
            values={
                "name": self.name, "date": self.date, "role": self.role,
                "affiliation": self.affiliation,
            },
            bindings=self.field_evidence,
        )
        return self


class LanguageSkill(StrictBaseModel):
    entry_id: str
    language: str
    proficiency: LanguageProficiency
    evidence: Evidence


class CertificateEntry(StrictBaseModel):
    entry_id: str
    name: str
    kind: CertificateKind
    issuing_body: str | None = None
    date: str | None = None
    evidence: Evidence


class AwardEntry(StrictBaseModel):
    entry_id: str
    name: str
    level: AwardLevel | None = None
    date: str | None = None
    issuing_body: str | None = None
    evidence: Evidence


class PublicationEntry(StrictBaseModel):
    entry_id: str
    title: str
    venue: str | None = None
    author_role: str | None = None
    author_order: int | None = Field(default=None, ge=1)
    status: Literal["published", "accepted", "submitted", "unknown"] = "unknown"
    year: int | None = Field(default=None, ge=1900, le=2200)
    date: str | None = None
    doi: str | None = None
    url: str | None = None
    evidence: Evidence


class PatentEntry(StrictBaseModel):
    entry_id: str
    title: str
    patent_number: str | None = None
    status: Literal["granted", "published", "pending", "unknown"] = "unknown"
    role: str | None = None
    inventor_order: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=1900, le=2200)
    date: str | None = None
    evidence: Evidence


class ResearchOutputEntry(StrictBaseModel):
    entry_id: str
    name: str
    output_type: Literal[
        "research_project", "competition", "open_source", "dataset", "software",
        "standard", "technical_report", "other", "unknown"
    ] = "unknown"
    role: str | None = None
    date: str | None = None
    url: str | None = None
    evidence: Evidence


class SelfEvaluation(StrictBaseModel):
    entry_id: str
    content: str
    evidence: Evidence


class CVExtractionResult(StrictBaseModel):
    document_id: str
    personal_info: PersonalInfo | None = None
    education: list[EducationEntry] = Field(default_factory=list)
    work_experience: list[WorkEntry] = Field(default_factory=list)
    project_experience: list[ProjectEntry] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    languages: list[LanguageSkill] = Field(default_factory=list)
    certificates: list[CertificateEntry] = Field(default_factory=list)
    awards: list[AwardEntry] = Field(default_factory=list)
    publications: list[PublicationEntry] = Field(default_factory=list)
    patents: list[PatentEntry] = Field(default_factory=list)
    research_outputs: list[ResearchOutputEntry] = Field(default_factory=list)
    self_evaluation: list[SelfEvaluation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_generated_ids(self) -> "CVExtractionResult":
        ids = [entry.entry_id for entry in self.education]
        ids.extend(entry.entry_id for entry in self.work_experience)
        ids.extend(entry.entry_id for entry in self.project_experience)
        ids.extend(entry.entry_id for entry in self.languages)
        ids.extend(entry.entry_id for entry in self.certificates)
        ids.extend(entry.entry_id for entry in self.awards)
        ids.extend(entry.entry_id for entry in self.publications)
        ids.extend(entry.entry_id for entry in self.patents)
        ids.extend(entry.entry_id for entry in self.research_outputs)
        ids.extend(entry.entry_id for entry in self.self_evaluation)
        if len(ids) != len(set(ids)):
            raise ValueError("entry_id must be unique within a document")

        skill_ids = [item.item_id for item in self.skills]
        skill_ids.extend(
            item.item_id
            for work in self.work_experience
            for item in work.tech_stack
        )
        skill_ids.extend(
            item.item_id
            for project in self.project_experience
            for item in project.tech_stack
        )
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("item_id must be unique within a document")
        return self


class NormalizedSkill(StrictBaseModel):
    source_item_id: str
    source_scope: str
    source_name: str
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: SkillItemType
    subcategory_code: str | None = None
    resolution_status: ResolutionStatus
    normalization_confidence: float | None = Field(default=None, ge=0, le=1)
    resolution_source: Literal[
        "explicit_mapping", "same_id", "canonical_name", "alias", "unresolved"
    ] = "unresolved"

    @model_validator(mode="after")
    def validate_resolution_provenance(self) -> NormalizedSkill:
        if self.resolution_status == "resolved":
            if self.normalization_confidence is None:
                raise ValueError("resolved skill requires normalization_confidence")
            if self.resolution_source == "unresolved":
                raise ValueError("resolved skill requires a resolved resolution_source")
        elif (
            self.normalization_confidence is not None
            or self.resolution_source != "unresolved"
        ):
            raise ValueError("unresolved skill cannot retain resolved normalization provenance")
        return self


class CVNormalizedResult(StrictBaseModel):
    document_id: str
    normalized_skills: list[NormalizedSkill] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


MatchFeatureType = Literal[
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
MatchResolutionStatus = Literal["resolved", "ambiguous", "unresolved"]
# structured_values may carry explicit nulls (e.g. position_code for an
# unresolved/ambiguous position classification), matching the V3 contract where
# non-resolved classifications must not bind a position identity.
MatchScalar = (
    str
    | int
    | float
    | bool
    | None
    | list[str]
    | list[dict[str, str | float]]
)


class MatchFeature(StrictBaseModel):
    feature_id: str
    document_id: str
    side: Literal["cv", "jd"]
    feature_type: MatchFeatureType
    source_object_id: str
    source_scope: str
    canonical_id: str | None = None
    canonical_name: str | None = None
    raw_text: str
    vector_text: str | None = None
    requirement_modality: Literal["required", "preferred", "bonus", "unknown"] | None = None
    candidate_level: str | None = None
    structured_values: dict[str, MatchScalar] = Field(default_factory=dict)
    resolution_status: MatchResolutionStatus
    evidence_refs: list[Evidence] = Field(default_factory=list)
    taxonomy_version: str
    derivation_version: str


class CVMatchFeatureResult(StrictBaseModel):
    document_id: str
    as_of_date: str
    taxonomy_version: str
    derivation_version: str
    features: list[MatchFeature] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_feature_identity(self) -> "CVMatchFeatureResult":
        feature_ids = [feature.feature_id for feature in self.features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("feature_id must be unique within a document")
        if any(feature.document_id != self.document_id for feature in self.features):
            raise ValueError("all MatchFeature document_id values must match the profile")
        if any(feature.side != "cv" for feature in self.features):
            raise ValueError("CVMatchFeatureResult only accepts side='cv'")
        return self


DemonstratedLevel = Literal["unknown", "basic", "working", "proficient", "advanced", "expert"]
CapabilityVerificationStatus = Literal[
    "supported", "partially_supported", "not_observed", "experience_only", "unresolved"
]
ConfidenceBand = Literal["none", "low", "medium", "high"]


class CapabilityEvidenceLink(StrictBaseModel):
    link_id: str
    document_id: str
    aggregation_key: str
    skill_id: str | None = None
    canonical_name: str | None = None
    declared_feature_ids: list[str] = Field(default_factory=list)
    experience_skill_feature_id: str
    experience_feature_id: str
    supporting_task_feature_ids: list[str] = Field(default_factory=list)
    support_signals: list[str] = Field(default_factory=list)
    support_score: int = Field(ge=0)
    demonstrated_level: DemonstratedLevel
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: ConfidenceBand
    evidence_refs: list[Evidence] = Field(default_factory=list)
    taxonomy_version: str
    derivation_version: str


class CapabilityProfile(StrictBaseModel):
    profile_id: str
    document_id: str
    aggregation_key: str
    skill_id: str | None = None
    canonical_name: str | None = None
    declared_feature_ids: list[str] = Field(default_factory=list)
    experience_skill_feature_ids: list[str] = Field(default_factory=list)
    evidence_link_ids: list[str] = Field(default_factory=list)
    declared_level: str | None = None
    demonstrated_level: DemonstratedLevel
    demonstrated_level_label: str
    verification_status: CapabilityVerificationStatus
    support_confidence: float = Field(ge=0, le=1)
    confidence_band: ConfidenceBand
    independent_experience_count: int = Field(ge=0)
    aggregate_support_score: int = Field(ge=0)
    evidence_bonus: float = Field(ge=0, le=1)
    resolution_status: MatchResolutionStatus


class CVCapabilityVerificationResult(StrictBaseModel):
    document_id: str
    taxonomy_version: str
    derivation_version: str
    profiles: list[CapabilityProfile] = Field(default_factory=list)
    evidence_links: list[CapabilityEvidenceLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self) -> "CVCapabilityVerificationResult":
        profile_ids = [profile.profile_id for profile in self.profiles]
        link_ids = [link.link_id for link in self.evidence_links]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("capability profile_id values must be unique")
        if len(link_ids) != len(set(link_ids)):
            raise ValueError("capability link_id values must be unique")
        if any(profile.document_id != self.document_id for profile in self.profiles):
            raise ValueError("capability profiles must match document_id")
        if any(link.document_id != self.document_id for link in self.evidence_links):
            raise ValueError("capability evidence links must match document_id")
        known_link_ids = set(link_ids)
        if any(
            link_id not in known_link_ids
            for profile in self.profiles
            for link_id in profile.evidence_link_ids
        ):
            raise ValueError("capability profile references an unknown evidence link")
        return self


CapabilityEvidenceLevel = Literal[
    "declared_only",
    "course_used",
    "project_used",
    "work_used",
    "owned_component",
    "designed_system",
    "measured_result",
]
CapabilityOwnership = Literal[
    "participated", "implemented", "owned", "designed", "led", "unknown"
]
CapabilityDepth = Literal["declared", "used", "implemented", "designed", "led", "unknown"]
CapabilityRecency = Literal["recent", "moderate", "old", "unknown"]


class CapabilityEvidenceItem(StrictBaseModel):
    evidence_item_id: str
    document_id: str
    skill_id: str | None = None
    skill_name: str
    evidence_level: CapabilityEvidenceLevel
    context: list[str] = Field(default_factory=list)
    ownership: CapabilityOwnership
    depth: CapabilityDepth
    recency: CapabilityRecency
    source_scope: str
    source_experience_id: str | None = None
    source_project_id: str | None = None
    source_evidence: Evidence
    evidence_lineage: list[Evidence] = Field(default_factory=list)
    source_text: str


class CapabilityEvidenceProfile(StrictBaseModel):
    capability_id: str
    document_id: str
    skill_id: str | None = None
    skill_name: str
    evidence_count: int = Field(ge=1)
    strongest_evidence: CapabilityEvidenceItem
    evidence_items: list[CapabilityEvidenceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_profile_shape(self) -> "CapabilityEvidenceProfile":
        if not self.evidence_items:
            raise ValueError("capability evidence profile must not be empty")
        if self.evidence_count != len(self.evidence_items):
            raise ValueError("evidence_count must equal evidence_items length")
        if any(
            item.document_id != self.document_id
            or item.skill_name != self.skill_name
            for item in self.evidence_items
        ):
            raise ValueError("capability evidence items must match profile identity")
        return self


class CVCapabilityEvidenceProfileResult(StrictBaseModel):
    document_id: str
    taxonomy_version: str
    derivation_version: str
    created_from_snapshot: str = "cv-confirmed-snapshot.v1"
    as_of_date: str
    profiles: list[CapabilityEvidenceProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self) -> "CVCapabilityEvidenceProfileResult":
        profile_ids = [profile.capability_id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("capability evidence profile_id values must be unique")
        if any(profile.document_id != self.document_id for profile in self.profiles):
            raise ValueError("capability evidence profiles must match document_id")
        return self
