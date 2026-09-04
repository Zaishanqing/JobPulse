from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.evaluation import (
    EvaluationOutcome,
    EvaluationRuleViolation,
    evaluate_cases,
    evaluate_clusters,
    require_evaluation_admin,
)
from app.contexts.evaluation._ports.evaluation import (
    EvaluationDatasetRecord,
    EvaluationReportDraft,
    EvaluationReportRecord,
    EvaluationUnitOfWork,
)
from app.contexts.tasks import TaskPayload, TaskRecord, TaskWorkflowPort


class EvaluationDatasetNotFound(LookupError):
    pass


class EvaluationReportNotFound(LookupError):
    pass


RUN_CONFIG = {
    "jd_parse": ("jd", "jd_parse_accuracy", "jd-rule-eval-v1", 0.0),
    "resume_parse": ("resume", "resume_parse_accuracy", "resume-rule-eval-v1", 0.0),
    "match": ("match", "match_accuracy", "match-rule-eval-v1", 0.05),
}


@dataclass(frozen=True)
class ManageEvaluation:
    uow_factory: Callable[[], EvaluationUnitOfWork]
    tasks: TaskWorkflowPort

    def create_dataset(self, actor: AccountActor, dataset_type: str, name: str, description: str | None, payload: FrozenJsonObject) -> EvaluationDatasetRecord:
        require_evaluation_admin(actor.role)
        with self.uow_factory() as uow:
            record = uow.evaluations.add_dataset(dataset_type, name, description, payload)
            uow.commit()
            return record

    def list_datasets(self, actor: AccountActor) -> list[EvaluationDatasetRecord]:
        require_evaluation_admin(actor.role)
        with self.uow_factory() as uow:
            return uow.evaluations.list_datasets()

    def get_dataset(self, actor: AccountActor, dataset_id: str) -> EvaluationDatasetRecord:
        require_evaluation_admin(actor.role)
        with self.uow_factory() as uow:
            record = uow.evaluations.get_dataset(dataset_id)
        if record is None:
            raise EvaluationDatasetNotFound("Evaluation dataset not found")
        return record

    def delete_dataset(self, actor: AccountActor, dataset_id: str) -> None:
        self.get_dataset(actor, dataset_id)
        with self.uow_factory() as uow:
            uow.evaluations.delete_dataset(dataset_id)
            uow.commit()

    def run(self, actor: AccountActor, report_type: str, dataset_id: str | None) -> EvaluationReportRecord:
        require_evaluation_admin(actor.role)
        if report_type == "skill_normalization":
            dataset = self._resolve_skill_dataset(dataset_id)
            metric, version, tolerance = "skill_normalization_accuracy", "skill-normalization-rule-eval-v1", 0.0
        else:
            dataset_type, metric, version, tolerance = RUN_CONFIG[report_type]
            dataset = self._resolve_dataset(dataset_id, dataset_type)
        items = dataset.payload.get("items", []) if dataset else []
        return self._store(report_type, dataset, evaluate_cases(items, metric, version, tolerance))

    def run_cluster(self, actor: AccountActor, payload: FrozenJsonObject) -> TaskRecord:
        require_evaluation_admin(actor.role)
        outcome = evaluate_clusters(payload.get("items", []))
        draft = self._report_draft("cluster", None, outcome)
        with self.uow_factory() as uow:
            report = uow.evaluations.add_report(draft)
            report_data = _evaluation_task_payload(report)
            task = self.tasks.prepare_succeeded(
                actor, "evaluation_cluster", input_payload=TaskPayload.from_mapping(payload),
                result_payload=TaskPayload.from_mapping({"report_id": report.report_id, **report_data}),
                result_reference=f"evaluation_report:{report.report_id}",
            )
            uow.add_task(task)
            uow.commit()
        return task

    def task(self, actor: AccountActor, task_id: str) -> TaskRecord:
        require_evaluation_admin(actor.role)
        return self.tasks.get(actor, task_id, {"evaluation_cluster"})

    def get_report(self, actor: AccountActor, report_id: str) -> EvaluationReportRecord:
        require_evaluation_admin(actor.role)
        with self.uow_factory() as uow:
            record = uow.evaluations.get_report(report_id)
        if record is None:
            raise EvaluationReportNotFound("Evaluation report not found")
        return record

    def _resolve_dataset(self, dataset_id: str | None, dataset_type: str) -> EvaluationDatasetRecord | None:
        with self.uow_factory() as uow:
            dataset = uow.evaluations.get_dataset(dataset_id) if dataset_id else uow.evaluations.latest_dataset(dataset_type)
        if dataset_id and dataset is None:
            raise EvaluationDatasetNotFound("Evaluation dataset not found")
        if dataset and dataset.dataset_type != dataset_type:
            raise EvaluationRuleViolation(f"Dataset type must be {dataset_type}")
        return dataset

    def _resolve_skill_dataset(self, dataset_id: str | None) -> EvaluationDatasetRecord | None:
        with self.uow_factory() as uow:
            if dataset_id:
                dataset = uow.evaluations.get_dataset(dataset_id)
            else:
                dataset = uow.evaluations.latest_dataset("jd") or uow.evaluations.latest_dataset("resume")
        if dataset_id and dataset is None:
            raise EvaluationDatasetNotFound("Evaluation dataset not found")
        return dataset

    def _store(self, report_type: str, dataset: EvaluationDatasetRecord | None, outcome: EvaluationOutcome) -> EvaluationReportRecord:
        draft = self._report_draft(report_type, dataset, outcome)
        with self.uow_factory() as uow:
            record = uow.evaluations.add_report(draft)
            uow.commit()
            return record

    @staticmethod
    def _report_draft(
        report_type: str,
        dataset: EvaluationDatasetRecord | None,
        outcome: EvaluationOutcome,
    ) -> EvaluationReportDraft:
        return EvaluationReportDraft(
            report_type, dataset.dataset_id if dataset else None, outcome.metrics,
            outcome.error_cases, outcome.status, outcome.algorithm_version,
            outcome.config_snapshot, outcome.evaluated_count, outcome.error_count,
        )


def _evaluation_task_payload(report: EvaluationReportRecord) -> FrozenJsonObject:
    return {
        "report_id": report.report_id, "report_type": report.report_type,
        "dataset_id": report.dataset_id, "metrics": report.metrics.as_dict(),
        "error_cases": [item.as_dict() for item in report.error_cases],
        "evaluation_status": report.evaluation_status,
        "algorithm_version": report.algorithm_version,
        "config_snapshot": report.config_snapshot.as_dict(),
        "evaluated_count": report.evaluated_count, "error_count": report.error_count,
        "implementation_status": "data_driven_rule_evaluation",
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }
