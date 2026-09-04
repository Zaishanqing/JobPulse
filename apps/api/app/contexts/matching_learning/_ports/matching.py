from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.contexts.tasks import TaskRecord
from app.domain.json_types import JsonObject
from app.domain.matching import MatchSkill, ResumeProject, ResumeSkillIdentity


@dataclass(frozen=True)
class ResumeProfile:
    resume_id: str
    owner_id: str
    validated_cv_snapshot_id: str | None
    skills: tuple[ResumeSkillIdentity, ...]
    projects: tuple[ResumeProject, ...]
    source_version: str = ""


@dataclass(frozen=True)
class PositionProfile:
    position_id: str
    skills: tuple[MatchSkill, ...]
    source_version: str = ""


@dataclass(frozen=True)
class EligibleResumeRecord:
    resume_id: str
    validated_cv_snapshot_id: str
    skill_count: int
    project_count: int


@dataclass(frozen=True)
class MatchingPositionCandidate:
    position_id: str
    position_name: str
    taxonomy_family_name: str | None
    status: str
    lifecycle_status: str
    position_code: str | None
    taxonomy_version: str


@dataclass(frozen=True)
class MatchablePositionRecord:
    position_id: str
    position_name: str
    taxonomy_family_name: str | None
    status: str
    lifecycle_status: str
    matchable: bool
    reason: str
    blockers: tuple[str, ...]
    position_graph_version: str | None
    position_profile_version: str | None


@dataclass(frozen=True)
class MatchingServiceReferenceRecord:
    task_id: str
    evaluation_id: str | None
    user_id: str
    tenant_id: str
    resume_id: str
    position_id: str
    provider: str
    target_type: str = "standard_position"
    status: str = "pending"
    idempotency_key: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    schema_version: str = "matching-service-reference.v1"
    access_scope: str = ""
    source_version: str = "legacy-unspecified"
    cv_profile_version: str = ""
    position_profile_version: str = ""
    taxonomy_version: str = "legacy-unspecified"
    graph_version: str = "legacy-unspecified"
    algorithm_version: str = "legacy-unspecified"
    matching_method: str | None = None
    degraded: bool | None = None
    overall_score: float | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class LearningPathRecordData:
    path_id: str
    evaluation_id: str
    user_id: str
    tenant_id: str
    target_position_id: str | None
    time_budget_hours: float | None
    gap_analysis: JsonObject
    status: str
    provider: str
    algorithm_versions: JsonObject
    data_versions: JsonObject
    versions: JsonObject
    resume_id: str | None
    validated_cv_snapshot_id: str | None
    position_id: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ResumeProfilePort(Protocol):
    def get(self, resume_id: str) -> ResumeProfile | None: ...
    def list_for_owner(self, owner_id: str) -> list[ResumeProfile]: ...


class PositionProfilePort(Protocol):
    def get(self, position_id: str) -> PositionProfile | None: ...


class MatchingPositionCatalogPort(Protocol):
    def get(self, position_id: str) -> MatchingPositionCandidate | None: ...
    def list(self) -> list[MatchingPositionCandidate]: ...


class MatchingContractPort(Protocol):
    def cv_profile(
        self, cv_id: str, snapshot_id: str | None = None
    ) -> JsonObject | None: ...
    def position_profile(self, position_id: str) -> JsonObject | None: ...
    def enterprise_job_profile(self, job_id: str) -> JsonObject | None: ...


class MatchingRepository(Protocol):
    def add_learning_path(
        self, record: LearningPathRecordData
    ) -> LearningPathRecordData: ...
    def get_learning_path(self, path_id: str) -> LearningPathRecordData | None: ...
    def list_learning_paths(self, user_id: str | None) -> list[LearningPathRecordData]: ...
    def upsert_service_reference(
        self, record: MatchingServiceReferenceRecord
    ) -> MatchingServiceReferenceRecord: ...
    def get_service_reference(
        self, reference_id: str
    ) -> MatchingServiceReferenceRecord | None: ...
    def list_service_references(
        self,
        user_id: str | None,
        *,
        position_id: str | None = None,
        target_type: str | None = None,
        include_orphan_intents: bool = False,
    ) -> list[MatchingServiceReferenceRecord]: ...
    def save_intent(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        tenant_id: str,
        resume_id: str,
        position_id: str,
        target_type: str,
        cv_profile_version: str,
        position_profile_version: str,
        status: str,
        access_scope: str = "",
        source_version: str = "legacy-unspecified",
        taxonomy_version: str = "legacy-unspecified",
        graph_version: str = "legacy-unspecified",
        algorithm_version: str = "legacy-unspecified",
    ) -> None: ...
    def update_intent_status(
        self, idempotency_key: str, status: str, error_code: str = ""
    ) -> None: ...


class MatchingUnitOfWork(Protocol):
    matching: MatchingRepository
    def add_task(self, record: TaskRecord) -> None: ...
    def __enter__(self) -> "MatchingUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
