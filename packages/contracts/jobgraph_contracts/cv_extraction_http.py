from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.normalization_v2 import JobClassification
from jobgraph_contracts.skill_taxonomy import SkillTaxonomyProjectionV1


CV_EXTRACTION_HTTP_CONTRACT_VERSION = "cv-extraction-http.v1"
CV_EXTRACTION_HTTP_CONTRACT_VERSION_V2 = "cv-extraction-http.v2"
CV_EXTRACTION_HTTP_CONTRACT_VERSION_V3 = "cv-extraction-http.v3"


class CVExtractionRequest(StrictContract):
    document_id: str = Field(min_length=1, max_length=128)
    raw_text: str = Field(min_length=1)


class CVExecutionStage(StrictContract):
    stage: Literal[
        "extracting",
        "contract_validating",
        "position_classifying",
        "semantic_repairing",
    ]
    started_at: str
    duration_ms: int = Field(ge=0)
    provider: str | None = None
    model: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CVExecutionMetadata(StrictContract):
    mode: Literal["llm", "demo_snapshot"] = "llm"
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    normalization_version: str
    taxonomy_version: str
    latency_ms: int = Field(ge=0)
    is_demo: bool = False
    dataset_version: str | None = None
    stages: list[CVExecutionStage] = Field(default_factory=list)


class CVEvidence(Evidence):
    source_document_id: str


class CVSourcedText(StrictContract):
    value: str
    evidence: CVEvidence


class CVFieldEvidence(StrictContract):
    field_name: str
    evidence: CVEvidence


class CVDateRange(StrictContract):
    start: str | None = None
    end: str | None = None
    duration_text: str | None = None


class CVSkillItem(StrictContract):
    item_id: str
    name: str
    item_type: str
    proficiency: str | None = None
    evidence: CVEvidence


class CVPersonalInfo(StrictContract):
    name: str | None = None
    gender: str | None = None
    birth_year: int | None = None
    phone: str | None = None
    email: str | None = None
    current_location: str | None = None
    expected_location: str | None = None
    expected_position: str | None = None
    expected_salary: str | None = None
    work_status: str | None = None
    available_date: str | None = None
    evidence: CVEvidence
    field_evidence: list[CVFieldEvidence] = Field(default_factory=list)


class CVEducationEntry(StrictContract):
    entry_id: str
    school: str
    college: str | None = None
    major: str
    degree: str
    date: CVDateRange | None = None
    gpa: str | None = None
    gpa_scale: str | None = None
    location: str | None = None
    school_tag: str | None = None
    evidence: CVEvidence
    field_evidence: list[CVFieldEvidence] = Field(default_factory=list)


class CVWorkEntry(StrictContract):
    entry_id: str
    company: str
    position: str | None = None
    date: CVDateRange | None = None
    department: str | None = None
    location: str | None = None
    work_type: str | None = None
    tech_stack: list[CVSkillItem] = Field(default_factory=list)
    responsibilities: list[CVSourcedText] = Field(default_factory=list)
    achievements: list[CVSourcedText] = Field(default_factory=list)
    evidence: CVEvidence
    field_evidence: list[CVFieldEvidence] = Field(default_factory=list)


class CVProjectEntry(StrictContract):
    entry_id: str
    name: str
    date: CVDateRange | None = None
    role: str | None = None
    affiliation: str | None = None
    description: CVSourcedText | None = None
    tech_stack: list[CVSkillItem] = Field(default_factory=list)
    highlights: list[CVSourcedText] = Field(default_factory=list)
    evidence: CVEvidence
    field_evidence: list[CVFieldEvidence] = Field(default_factory=list)


class CVLanguageEntry(StrictContract):
    entry_id: str
    language: str
    proficiency: str
    evidence: CVEvidence


class CVCertificateEntry(StrictContract):
    entry_id: str
    name: str
    kind: str
    issuing_body: str | None = None
    date: str | None = None
    evidence: CVEvidence


class CVAwardEntry(StrictContract):
    entry_id: str
    name: str
    level: str | None = None
    date: str | None = None
    issuing_body: str | None = None
    evidence: CVEvidence


class CVPublicationEntry(StrictContract):
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
    evidence: CVEvidence


class CVPatentEntry(StrictContract):
    entry_id: str
    title: str
    patent_number: str | None = None
    status: Literal["granted", "published", "pending", "unknown"] = "unknown"
    role: str | None = None
    inventor_order: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=1900, le=2200)
    date: str | None = None
    evidence: CVEvidence


class CVResearchOutputEntry(StrictContract):
    entry_id: str
    name: str
    output_type: Literal[
        "research_project", "competition", "open_source", "dataset", "software",
        "standard", "technical_report", "other", "unknown"
    ] = "unknown"
    role: str | None = None
    date: str | None = None
    url: str | None = None
    evidence: CVEvidence


class CVSelfEvaluation(StrictContract):
    entry_id: str
    content: str
    evidence: CVEvidence


class CVExtractionResult(StrictContract):
    document_id: str
    personal_info: CVPersonalInfo | None = None
    education: list[CVEducationEntry] = Field(default_factory=list)
    work_experience: list[CVWorkEntry] = Field(default_factory=list)
    project_experience: list[CVProjectEntry] = Field(default_factory=list)
    skills: list[CVSkillItem] = Field(default_factory=list)
    languages: list[CVLanguageEntry] = Field(default_factory=list)
    certificates: list[CVCertificateEntry] = Field(default_factory=list)
    awards: list[CVAwardEntry] = Field(default_factory=list)
    publications: list[CVPublicationEntry] = Field(default_factory=list)
    patents: list[CVPatentEntry] = Field(default_factory=list)
    research_outputs: list[CVResearchOutputEntry] = Field(default_factory=list)
    self_evaluation: list[CVSelfEvaluation] = Field(default_factory=list)


class CVNormalizedSkill(StrictContract):
    source_item_id: str
    source_scope: str
    source_name: str
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str
    subcategory_code: str | None = None
    resolution_status: str
    normalization_confidence: float | None = Field(default=None, ge=0, le=1)
    resolution_source: Literal[
        "explicit_mapping", "same_id", "canonical_name", "alias", "unresolved"
    ] = "unresolved"

    @model_validator(mode="after")
    def validate_resolution_provenance(self) -> CVNormalizedSkill:
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


class CVNormalizedResult(StrictContract):
    document_id: str
    normalized_skills: list[CVNormalizedSkill] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class CVRolePositionClassification(StrictContract):
    feature_id: str
    source_object_id: str
    source_scope: str
    role_kind: Literal["expected", "historical"]
    job_classification: JobClassification


class CVNormalizedResultV3(CVNormalizedResult):
    position_classifications: list[CVRolePositionClassification] = Field(
        default_factory=list
    )


class CVReviewFlag(StrictContract):
    cv_id: str
    issue_type: str
    severity: str
    rule_scope: str
    description: str
    suggested_action: str
    item_id: str | None = None


class CVExtractionResponse(StrictContract):
    contract_version: Literal["cv-extraction-http.v1"]
    document_id: str
    execution: CVExecutionMetadata
    extraction_result: CVExtractionResult
    normalized_result: CVNormalizedResult
    review_flags: list[CVReviewFlag] = Field(default_factory=list)


class CVExtractionResponseV2(CVExtractionResponse):
    contract_version: Literal["cv-extraction-http.v2"] = "cv-extraction-http.v2"
    skill_taxonomy: SkillTaxonomyProjectionV1


class CVExtractionResponseV3(StrictContract):
    contract_version: Literal["cv-extraction-http.v3"] = "cv-extraction-http.v3"
    document_id: str
    execution: CVExecutionMetadata
    extraction_result: CVExtractionResult
    normalized_result: CVNormalizedResultV3
    review_flags: list[CVReviewFlag] = Field(default_factory=list)
    skill_taxonomy: SkillTaxonomyProjectionV1


def parse_cv_extraction_response(
    payload: Any,
) -> CVExtractionResponse | CVExtractionResponseV2 | CVExtractionResponseV3:
    if isinstance(
        payload,
        (CVExtractionResponse, CVExtractionResponseV2, CVExtractionResponseV3),
    ):
        return payload
    if not isinstance(payload, dict):
        raise TypeError("CV extraction response must be an object")
    version = payload.get("contract_version")
    if version == "cv-extraction-http.v1":
        return CVExtractionResponse.model_validate(payload)
    if version == "cv-extraction-http.v2":
        return CVExtractionResponseV2.model_validate(payload)
    if version == "cv-extraction-http.v3":
        return CVExtractionResponseV3.model_validate(payload)
    raise ValueError(f"unsupported CV extraction contract_version: {version!r}")
