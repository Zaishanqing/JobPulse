from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.evaluation import EvaluationConfigSnapshot, EvaluationErrorCase, EvaluationMetrics
from app.contexts.tasks import TaskRecord


@dataclass(frozen=True)
class EvaluationDatasetRecord:
    dataset_id: str
    dataset_type: str
    name: str
    description: str | None
    payload: FrozenJsonObject
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class EvaluationReportRecord:
    report_id: str
    report_type: str
    dataset_id: str | None
    metrics: EvaluationMetrics
    error_cases: tuple[EvaluationErrorCase, ...]
    evaluation_status: str
    algorithm_version: str
    config_snapshot: EvaluationConfigSnapshot
    evaluated_count: int
    error_count: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class EvaluationReportDraft:
    report_type: str
    dataset_id: str | None
    metrics: EvaluationMetrics
    error_cases: tuple[EvaluationErrorCase, ...]
    evaluation_status: str
    algorithm_version: str
    config_snapshot: EvaluationConfigSnapshot
    evaluated_count: int
    error_count: int


class EvaluationRepository(Protocol):
    def add_dataset(self, dataset_type: str, name: str, description: str | None, payload: FrozenJsonObject) -> EvaluationDatasetRecord: ...
    def get_dataset(self, dataset_id: str) -> EvaluationDatasetRecord | None: ...
    def latest_dataset(self, dataset_type: str) -> EvaluationDatasetRecord | None: ...
    def list_datasets(self) -> list[EvaluationDatasetRecord]: ...
    def delete_dataset(self, dataset_id: str) -> None: ...
    def add_report(self, draft: EvaluationReportDraft) -> EvaluationReportRecord: ...
    def get_report(self, report_id: str) -> EvaluationReportRecord | None: ...


class EvaluationUnitOfWork(Protocol):
    evaluations: EvaluationRepository
    def add_task(self, record: TaskRecord) -> None: ...
    def __enter__(self) -> "EvaluationUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
