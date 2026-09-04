from sqlalchemy.orm import Session, sessionmaker

from app.models.evaluation import EvaluationDataset, EvaluationReport
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.domain.evaluation import EvaluationConfigSnapshot, EvaluationErrorCase, EvaluationMetrics
from app.contexts.evaluation import (
    EvaluationDatasetRecord,
    EvaluationReportDraft,
    EvaluationReportRecord,
)
from app.contexts.tasks import TaskRecord


class SqlAlchemyEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_dataset(self, dataset_type, name, description, payload) -> EvaluationDatasetRecord:
        row = EvaluationDataset(dataset_type=dataset_type, name=name, description=description, payload=dict(payload))
        self._session.add(row)
        self._session.flush()
        return self._dataset(row)

    def get_dataset(self, dataset_id: str) -> EvaluationDatasetRecord | None:
        row = self._session.get(EvaluationDataset, dataset_id)
        return self._dataset(row) if row is not None else None

    def latest_dataset(self, dataset_type: str) -> EvaluationDatasetRecord | None:
        row = self._session.query(EvaluationDataset).filter(EvaluationDataset.dataset_type == dataset_type).order_by(EvaluationDataset.created_at.desc()).first()
        return self._dataset(row) if row is not None else None

    def list_datasets(self) -> list[EvaluationDatasetRecord]:
        rows = self._session.query(EvaluationDataset).order_by(EvaluationDataset.created_at.desc()).all()
        return [self._dataset(row) for row in rows]

    def delete_dataset(self, dataset_id: str) -> None:
        row = self._session.get(EvaluationDataset, dataset_id)
        if row is None:
            raise LookupError(dataset_id)
        self._session.delete(row)

    def add_report(self, draft: EvaluationReportDraft) -> EvaluationReportRecord:
        row = EvaluationReport(
            report_type=draft.report_type, dataset_id=draft.dataset_id,
            metrics=draft.metrics.as_dict(), error_cases=[item.as_dict() for item in draft.error_cases],
            evaluation_status=draft.evaluation_status,
            algorithm_version=draft.algorithm_version,
            config_snapshot=draft.config_snapshot.as_dict(),
            evaluated_count=draft.evaluated_count, error_count=draft.error_count,
        )
        self._session.add(row)
        self._session.flush()
        return self._report(row)

    def get_report(self, report_id: str) -> EvaluationReportRecord | None:
        row = self._session.get(EvaluationReport, report_id)
        return self._report(row) if row is not None else None

    @staticmethod
    def _dataset(row: EvaluationDataset) -> EvaluationDatasetRecord:
        return EvaluationDatasetRecord(row.id, row.dataset_type, row.name, row.description, row.payload or {}, row.created_at, row.updated_at)

    @staticmethod
    def _report(row: EvaluationReport) -> EvaluationReportRecord:
        return EvaluationReportRecord(
            row.id, row.report_type, row.dataset_id, EvaluationMetrics.from_mapping(row.metrics or {}),
            tuple(EvaluationErrorCase.from_mapping(item) for item in (row.error_cases or [])), row.evaluation_status,
            row.algorithm_version, EvaluationConfigSnapshot.from_mapping(row.config_snapshot or {}),
            row.evaluated_count, row.error_count, row.created_at, row.updated_at,
        )


class SqlAlchemyEvaluationUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyEvaluationUnitOfWork":
        self._session = self._session_factory()
        self.evaluations = SqlAlchemyEvaluationRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def add_task(self, record: TaskRecord) -> None:
        self._tasks.add(record)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
