"""Stable JD domain concepts; version-specific fields stay at adapter boundaries."""

from dataclasses import dataclass, field

from app.domain.json_types import (
    JsonObject,
    JsonValue as JsonValue,
    empty_json_object,
    freeze_json_object,
)


@dataclass(frozen=True)
class Evidence:
    source_id: str
    quote: str
    start: int | None
    end: int | None
    alignment: str
    occurrence_index: int | None


@dataclass(frozen=True)
class Responsibility:
    responsibility_id: str
    modality: str
    evidence: Evidence
    raw_payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_payload", freeze_json_object(
            self.raw_payload, field="Responsibility.raw_payload"
        ))


@dataclass(frozen=True)
class CandidateRequirement:
    requirement_id: str
    kind: str
    modality: str
    evidence: Evidence
    raw_payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_payload", freeze_json_object(
            self.raw_payload, field="CandidateRequirement.raw_payload"
        ))


@dataclass(frozen=True)
class Fact:
    fact_id: str
    scope: str
    kind: str
    value: str
    evidence: Evidence
    raw_payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_payload", freeze_json_object(
            self.raw_payload, field="Fact.raw_payload"
        ))


@dataclass(frozen=True)
class Document:
    document_id: str
    contract_version: str
    title: str | None = None
    title_evidence: Evidence | None = None
    responsibilities: tuple[Responsibility, ...] = ()
    requirements: tuple[CandidateRequirement, ...] = ()
    facts: tuple[Fact, ...] = ()
    payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_json_object(
            self.payload, field="Document.payload"
        ))


@dataclass(frozen=True)
class NormalizedItem:
    source_value: str
    item_type: str
    resolution_status: str
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    raw_payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_payload", freeze_json_object(
            self.raw_payload, field="NormalizedItem.raw_payload"
        ))


@dataclass(frozen=True)
class ReviewFlag:
    flag_type: str
    source_value: str
    reason: str
    severity: str = "warning"
    source: str = "normalization"
    code: str | None = None
    raw_payload: JsonObject = field(default_factory=empty_json_object)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_payload", freeze_json_object(
            self.raw_payload, field="ReviewFlag.raw_payload"
        ))


@dataclass(frozen=True)
class NormalizationResult:
    document_id: str
    contract_version: str
    items: tuple[NormalizedItem, ...] = ()
    review_flags: tuple[ReviewFlag, ...] = ()
    job_classification: JsonObject | None = None
    salary: JsonObject | None = None

    def __post_init__(self) -> None:
        if self.job_classification is not None:
            object.__setattr__(self, "job_classification", freeze_json_object(
                self.job_classification, field="NormalizationResult.job_classification"
            ))
        if self.salary is not None:
            object.__setattr__(self, "salary", freeze_json_object(
                self.salary, field="NormalizationResult.salary"
            ))
