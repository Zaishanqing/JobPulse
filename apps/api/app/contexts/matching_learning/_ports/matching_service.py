from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.accounts import AccountActor
from app.domain.json_types import JsonObject


@dataclass(frozen=True)
class MatchingIdentity:
    subject_id: str
    tenant_id: str
    roles: tuple[str, ...]
    access_scope: str


@dataclass(frozen=True)
class RemoteTask:
    task_id: str
    status: str
    evaluation_id: str | None
    created: bool
    error_code: str | None
    error_message: str | None
    attempt: int
    created_at: str | None
    updated_at: str | None
    raw: JsonObject
    target_type: str = "standard_position"


@dataclass(frozen=True)
class RemoteEvaluation:
    evaluation_id: str
    task_id: str
    stale: bool
    evaluation: JsonObject
    gap_analysis: JsonObject
    versions: JsonObject
    created_at: str | None
    updated_at: str | None
    user_id: str | None = None
    resume_id: str | None = None
    validated_cv_snapshot_id: str | None = None
    position_id: str | None = None
    provider: str = ""
    method: str = ""
    matching_method: str = ""
    degraded: bool = False
    rule_based: bool | None = None
    target_type: str = "standard_position"
    use_enterprise_weights: bool = False
    generate_learning_path: bool = False
    stale_reason_codes: tuple[str, ...] = ()
    algorithm_versions: JsonObject | None = None
    data_versions: JsonObject | None = None
    radar_dimensions: tuple[JsonObject, ...] = ()


@dataclass(frozen=True)
class RemoteLearningPath:
    path_id: str
    evaluation_id: str
    target_position_id: str | None
    gap_analysis: JsonObject
    status: str
    created_at: str | None
    updated_at: str | None
    provider: str = ""
    algorithm_versions: JsonObject | None = None
    data_versions: JsonObject | None = None
    versions: JsonObject | None = None
    resume_id: str | None = None
    validated_cv_snapshot_id: str | None = None
    position_id: str | None = None
    time_budget_hours: float | None = None


class MatchingServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class MatchingServicePort(Protocol):
    def create_task(
        self,
        identity: MatchingIdentity,
        *,
        cv_id: str,
        position_id: str,
        idempotency_key: str,
        correlation_id: str,
        cv_profile: JsonObject | None = None,
        position_profile: JsonObject | None = None,
        target_type: str = "standard_position",
        use_enterprise_weights: bool = False,
        generate_learning_path: bool = False,
    ) -> RemoteTask: ...

    def get_task(
        self, identity: MatchingIdentity, task_id: str, *, correlation_id: str
    ) -> RemoteTask: ...

    def abandon_task(
        self, identity: MatchingIdentity, task_id: str, *, correlation_id: str
    ) -> RemoteTask: ...

    def get_evaluation(
        self, identity: MatchingIdentity, evaluation_id: str, *, correlation_id: str
    ) -> RemoteEvaluation: ...

    def generate_learning_path(
        self,
        identity: MatchingIdentity,
        evaluation: JsonObject,
        *,
        correlation_id: str,
        time_budget_hours: float | None = None,
        cv_profile: JsonObject | None = None,
        position_profile: JsonObject | None = None,
        target_type: str = "standard_position",
        use_enterprise_weights: bool = False,
    ) -> JsonObject: ...

    def evaluate_what_if(
        self,
        identity: MatchingIdentity,
        *,
        baseline_evaluation: JsonObject,
        cv_profile: JsonObject,
        position_profile: JsonObject,
        actions: tuple[JsonObject, ...],
        target_type: str,
        use_enterprise_weights: bool,
        correlation_id: str,
    ) -> JsonObject: ...

    def evaluate_explanation_deletion(
        self,
        identity: MatchingIdentity,
        *,
        baseline_evaluation: JsonObject,
        cv_profile: JsonObject,
        position_profile: JsonObject,
        deletion_kind: str,
        evidence_source_ids: tuple[str, ...],
        target_type: str,
        use_enterprise_weights: bool,
        correlation_id: str,
    ) -> JsonObject: ...


class MatchingIdentityPort(Protocol):
    def resolve(self, actor: AccountActor) -> MatchingIdentity: ...

    def authorize_request(
        self,
        actor: AccountActor,
        *,
        cv_id: str,
        position_id: str,
        target_type: str = "standard_position",
    ) -> None: ...
