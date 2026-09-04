from typing import Any

from pydantic import BaseModel, Field


class EvaluationDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EvaluationDatasetResponse(BaseModel):
    dataset_id: str
    dataset_type: str
    name: str
    description: str | None = None
    payload: dict[str, Any]


class EvaluationRunRequest(BaseModel):
    dataset_id: str | None = None


class EvaluationReportResponse(BaseModel):
    report_id: str
    report_type: str
    dataset_id: str | None = None
    metrics: dict[str, Any]
    error_cases: list[dict[str, Any]]
    evaluation_status: str
    algorithm_version: str
    config_snapshot: dict[str, Any]
    evaluated_count: int
    error_count: int
