from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.contexts.tasks import TaskRecord


@dataclass(frozen=True)
class TrendSourceRecord:
    source_id: str
    source_type: str
    title: str
    source_name: str | None
    url: str | None
    raw_text: str
    publish_date: date | None
    credibility_score: float
    parsed_keywords: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    provider_run_id: str | None = None
    external_source_id: str | None = None
    source_version: str | None = None
    captured_at: datetime | None = None
    snapshot_reference: str | None = None
    extraction_version: str | None = None
    source_metadata: FrozenJsonObject | None = None


@dataclass(frozen=True)
class TrendSourceDraft:
    source_type: str
    title: str
    source_name: str | None
    url: str | None
    raw_text: str
    publish_date: date | None
    credibility_score: float
    parsed_keywords: tuple[str, ...]


@dataclass(frozen=True)
class PredictedPositionRecord:
    predicted_id: str
    position_name: str
    prediction_basis: tuple[dict, ...]
    related_source_ids: tuple[str, ...]
    potential_responsibilities: tuple[str, ...]
    potential_skills: tuple[str, ...]
    industry_scenarios: tuple[str, ...]
    confidence_score: float
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    provider_run_id: str | None = None
    candidate_key: str | None = None
    industry_domain: str | None = None
    emergence_score: float | None = None
    score_components: FrozenJsonObject | None = None
    algorithm_version: str | None = None
    formula_version: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    source_coverage: float | None = None
    missing_sources: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    published_definition_version_id: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class PositionComparisonProfile:
    target_type: str
    target_id: str
    name: str
    skill_ids: tuple[str, ...]
    skill_names: tuple[str, ...]
    responsibilities: tuple[str, ...]
    industry_scenarios: tuple[str, ...]
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class PredictionMatchRecord:
    match_id: str
    predicted_position_id: str
    version: int
    target_type: str
    target_id: str
    similarity_score: float
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    overlap_evidence: FrozenJsonObject
    recommendation: str
    created_at: datetime | None
    cache_key: str = "legacy"


@dataclass(frozen=True)
class PredictionDefinitionRecord:
    definition_id: str
    predicted_position_id: str
    version: int
    status: str
    payload: FrozenJsonObject
    review_task_id: str | None
    created_at: datetime | None
    cache_key: str = "legacy"


@dataclass(frozen=True)
class PredictionRelationRecord:
    relation_id: str
    predicted_position_id: str
    version: int
    relation_type: str
    target_id: str | None
    status: str
    reason: str | None
    created_at: datetime | None
    relation_identity_id: str = ""
    supersedes_relation_id: str | None = None


class TrendSourceRepository(Protocol):
    def add(self, draft: TrendSourceDraft) -> TrendSourceRecord: ...
    def list(self) -> list[TrendSourceRecord]: ...
    def get(self, source_id: str) -> TrendSourceRecord | None: ...
    def update(self, source_id: str, changes: FrozenJsonObject) -> TrendSourceRecord: ...
    def delete(self, source_id: str) -> None: ...
    def add_projection(self, values: FrozenJsonObject) -> TrendSourceRecord: ...
    def get_by_provider_snapshot(self, provider_run_id: str, snapshot_reference: str) -> TrendSourceRecord | None: ...


class PredictedPositionRepository(Protocol):
    def add(self, values: FrozenJsonObject) -> PredictedPositionRecord: ...
    def get(self, predicted_id: str) -> PredictedPositionRecord | None: ...
    def list(self) -> list[PredictedPositionRecord]: ...
    def update(self, predicted_id: str, changes: FrozenJsonObject) -> PredictedPositionRecord: ...
    def get_by_provider_candidate(self, provider_run_id: str, candidate_key: str) -> PredictedPositionRecord | None: ...
    def comparison_profiles(self, predicted_id: str) -> tuple[PositionComparisonProfile, tuple[PositionComparisonProfile, ...]]: ...
    def skill_catalog_version(self) -> str: ...
    def normalize_skills(self, names: tuple[str, ...], *, context: str) -> tuple[FrozenJsonObject, ...]: ...
    def save_matches(self, predicted_id: str, values: tuple[FrozenJsonObject, ...], actor_id: str, *, cache_key: str = "legacy") -> tuple[PredictionMatchRecord, ...]: ...
    def list_matches(self, predicted_id: str) -> tuple[PredictionMatchRecord, ...]: ...
    def save_definition(self, predicted_id: str, payload: FrozenJsonObject, actor_id: str, *, cache_key: str = "legacy") -> PredictionDefinitionRecord: ...
    def list_definitions(self, predicted_id: str) -> tuple[PredictionDefinitionRecord, ...]: ...
    def get_definition(self, definition_id: str) -> PredictionDefinitionRecord | None: ...
    def attach_review(self, definition_id: str, review_task_id: str) -> PredictionDefinitionRecord: ...
    def create_definition_review(self, definition_id: str, actor_id: str, reason: str | None) -> PredictionDefinitionRecord: ...
    def review_status(self, review_task_id: str) -> FrozenJsonObject | None: ...
    def publication_facts(self, predicted_id: str, definition_id: str) -> FrozenJsonObject: ...
    def publish_definition(self, predicted_id: str, definition_id: str, published_at: datetime) -> PredictedPositionRecord: ...
    def reject_definition(self, definition_id: str) -> PredictionDefinitionRecord: ...
    def save_relation(self, predicted_id: str, relation_type: str, target_id: str | None, reason: str | None, actor_id: str, *, deleted: bool = False, relation_identity_id: str | None = None, supersedes_relation_id: str | None = None) -> PredictionRelationRecord: ...
    def list_relations(self, predicted_id: str) -> tuple[PredictionRelationRecord, ...]: ...
    def list_relation_history(self, predicted_id: str) -> tuple[PredictionRelationRecord, ...]: ...
    def get_relation(self, relation_id: str) -> PredictionRelationRecord | None: ...


class TrendUnitOfWork(Protocol):
    sources: TrendSourceRepository
    predictions: PredictedPositionRepository
    def add_task(self, record: TaskRecord) -> None: ...
    def get_task(self, task_id: str) -> TaskRecord | None: ...
    def save_task(self, record: TaskRecord) -> None: ...
    def __enter__(self) -> "TrendUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
