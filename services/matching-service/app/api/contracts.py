from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.evaluation import MatchEvaluation
from app.domain.explanation_deletion import EvidenceDeletionResult
from app.domain.gaps import GapAnalysis
from app.domain.integration import ContractIntegrationResult
from app.domain.observability import ReadinessReport
from app.domain.tasks import EvaluationQueryResult, TaskQueryResult, TaskSubmissionResult
from app.domain.what_if import WhatIfResult


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationErrorResponse(ApiModel):
    path: str
    message: str
    error_type: str


class ValidationData(ApiModel):
    profile_status: Literal["ready", "review_required", "invalid"]
    profile_id: str | None
    profile_version: str | None
    unresolved_items: list[dict[str, object]]
    validation_errors: list[ValidationErrorResponse]


class ValidationEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: ValidationData


class HealthData(ApiModel):
    status: Literal["ok"]
    service: Literal["matching-service"]
    version: str


class HealthEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: HealthData


class LivenessData(ApiModel):
    status: Literal["alive"]
    service: Literal["matching-service"]
    version: str


class LivenessEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: LivenessData


class ReadinessEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: ReadinessReport


class EvaluationEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: MatchEvaluation


class GapAnalysisEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: GapAnalysis


class WhatIfEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: WhatIfResult


class EvidenceDeletionEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: EvidenceDeletionResult


class ContractIntegrationEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: ContractIntegrationResult


class TaskSubmissionEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: TaskSubmissionResult


class TaskQueryEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: TaskQueryResult


class PersistedEvaluationEnvelope(ApiModel):
    code: Literal[0]
    message: Literal["success"]
    data: EvaluationQueryResult
