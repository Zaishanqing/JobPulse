from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.contexts.data_validation.domain import (
    DataValidationError,
    DataValidationTask,
    DataValidationTaskStatus,
    ValidatedBundleSnapshot,
    ValidationConclusion,
    ValidationReport,
    ValidationResult,
    validated_snapshot_idempotency_key,
)
from app.contexts.data_validation.ports import (
    DataValidationUoWFactory,
    ValidationInputReaderPort,
    ValidationPortFactory,
)
from app.contexts.data_validation.validators import ValidationContext, ValidatorSet


@dataclass(frozen=True)
class ValidateBundleUseCase:
    validators: ValidatorSet

    def execute(self, context: ValidationContext) -> ValidationResult:
        findings = self.validators.validate(context)
        return ValidationResult(
            conclusion=self.validators.conclusion(findings),
            findings=findings,
        )


class ValidationExecutionError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class ValidationTaskExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ALREADY_FINISHED = "already_finished"


@dataclass(frozen=True)
class ValidationTaskExecutionResult:
    status: ValidationTaskExecutionStatus
    task_id: str
    conclusion: ValidationConclusion | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ExecuteValidationTaskUseCase:
    uow_factory: DataValidationUoWFactory
    input_reader: ValidationInputReaderPort
    port_factory: ValidationPortFactory
    validator: ValidateBundleUseCase

    def execute(self, claimed_task: DataValidationTask) -> ValidationTaskExecutionResult:
        if claimed_task.status is not DataValidationTaskStatus.RUNNING:
            return self._existing_result(claimed_task.id)
        try:
            validation_input = self.input_reader.load(claimed_task)
            if (
                validation_input.extraction_task_id
                != claimed_task.extraction_task_id
            ):
                raise ValidationExecutionError(
                    "extraction_task_mismatch",
                    "Validation input extraction lineage is inconsistent.",
                )
            if (
                validation_input.source_jd_version_id
                != claimed_task.source_jd_version_id
            ):
                raise ValidationExecutionError(
                    "source_jd_version_mismatch",
                    "Validation input source lineage is inconsistent.",
                )
            if (
                validation_input.policy_binding_version
                != claimed_task.policy_version
            ):
                raise ValidationExecutionError(
                    "validation_policy_binding_mismatch",
                    "Validation policy binding does not match the claimed task.",
                )
            context = ValidationContext(
                bundle=validation_input.bundle,
                raw_text=validation_input.raw_text,
                cleaned_text=validation_input.cleaned_text,
                source_jd_id=validation_input.source_jd_id,
                source_jd_version_id=validation_input.source_jd_version_id,
                extraction_task_id=validation_input.extraction_task_id,
                source_platform=validation_input.source_platform,
                source_record_id=validation_input.source_record_id,
                bundle_id=validation_input.bundle_id,
                declared_source_jd_id=validation_input.source_jd_id,
                declared_source_jd_version_id=claimed_task.source_jd_version_id,
                declared_extraction_task_id=claimed_task.extraction_task_id,
                catalog=self.port_factory.catalog(
                    validation_input.catalog_snapshot_version
                ),
                cross_source_duplicates=(
                    self.port_factory.cross_source_duplicates(validation_input)
                ),
            )
            validation_result = self.validator.execute(context)
            return self._persist_success(
                claimed_task.id, validation_input, validation_result
            )
        except Exception as exc:
            error_code, safe_message = self._safe_failure(exc)
            if self._mark_failed(claimed_task.id, error_code, safe_message):
                return ValidationTaskExecutionResult(
                    ValidationTaskExecutionStatus.FAILED,
                    claimed_task.id,
                    error_code=error_code,
                )
            return self._existing_result(claimed_task.id)

    def _persist_success(
        self, task_id: str, validation_input, result: ValidationResult
    ) -> ValidationTaskExecutionResult:
        with self.uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise ValidationExecutionError(
                    "validation_task_missing", "Validation task no longer exists."
                )
            if task.status is not DataValidationTaskStatus.RUNNING:
                return self._result_from_persisted(uow, task)
            if uow.reports.get_by_task(task.id) is not None:
                raise ValidationExecutionError(
                    "validation_result_conflict",
                    "Validation result already exists for a running task.",
                )
            succeeded = uow.tasks.save(task.mark_succeeded())
            report = uow.reports.add(
                ValidationReport.create(
                    task=succeeded,
                    conclusion=result.conclusion,
                    report_payload={
                        "conclusion": result.conclusion.value,
                        "ruleset_version": validation_input.ruleset_version,
                        "catalog_snapshot_version": (
                            validation_input.catalog_snapshot_version
                        ),
                        "policy_binding_version": (
                            validation_input.policy_binding_version
                        ),
                        "findings": [
                            finding.as_dict() for finding in result.findings
                        ],
                        "lineage": {
                            "data_validation_task_id": succeeded.id,
                            "extraction_task_id": (
                                validation_input.extraction_task_id
                            ),
                            "source_jd_version_id": (
                                validation_input.source_jd_version_id
                            ),
                            "source_jd_id": validation_input.source_jd_id,
                            "bundle_id": (
                                validation_input.bundle_id
                            ),
                            "catalog_snapshot_version": (
                                validation_input.catalog_snapshot_version
                            ),
                        },
                    },
                )
            )
            if result.conclusion is not ValidationConclusion.BLOCK:
                uow.snapshots.add(
                    ValidatedBundleSnapshot.create(
                        task=succeeded,
                        report=report,
                        bundle_payload=validation_input.bundle,
                    )
                )
            if result.conclusion in {
                ValidationConclusion.WARN,
                ValidationConclusion.BLOCK,
            }:
                uow.governance.ensure_for_report(
                    validation_report_id=report.id,
                    data_validation_task_id=succeeded.id,
                    extraction_task_id=validation_input.extraction_task_id,
                    source_jd_version_id=(
                        validation_input.source_jd_version_id
                    ),
                    conclusion=result.conclusion.value,
                )
            uow.commit()
            return ValidationTaskExecutionResult(
                ValidationTaskExecutionStatus.SUCCEEDED,
                task.id,
                conclusion=result.conclusion,
            )

    def _mark_failed(self, task_id: str, code: str, message: str) -> bool:
        try:
            with self.uow_factory() as uow:
                task = uow.tasks.get(task_id)
                if task is None or task.status is not DataValidationTaskStatus.RUNNING:
                    return False
                uow.tasks.save(
                    task.mark_failed(
                        error_code=code,
                        error_message=message,
                        retryable=False,
                    )
                )
                uow.commit()
                return True
        except Exception:
            return False

    def _existing_result(self, task_id: str) -> ValidationTaskExecutionResult:
        with self.uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise ValidationExecutionError(
                    "validation_task_missing", "Validation task no longer exists."
                )
            return self._result_from_persisted(uow, task)

    @staticmethod
    def _result_from_persisted(uow, task) -> ValidationTaskExecutionResult:
        if task.status is DataValidationTaskStatus.SUCCEEDED:
            report = uow.reports.get_by_task(task.id)
            if report is None:
                raise ValidationExecutionError(
                    "validation_result_incomplete",
                    "Succeeded validation task has no report.",
                )
            snapshot = uow.snapshots.get_by_idempotency_key(
                validated_snapshot_idempotency_key(report.id)
            )
            if (
                report.conclusion is ValidationConclusion.BLOCK
                and snapshot is not None
            ) or (
                report.conclusion is not ValidationConclusion.BLOCK
                and snapshot is None
            ):
                raise ValidationExecutionError(
                    "validation_result_incomplete",
                    "Succeeded validation task has incomplete persisted results.",
                )
            return ValidationTaskExecutionResult(
                ValidationTaskExecutionStatus.ALREADY_FINISHED,
                task.id,
                conclusion=report.conclusion,
            )
        if task.status is DataValidationTaskStatus.FAILED:
            return ValidationTaskExecutionResult(
                ValidationTaskExecutionStatus.ALREADY_FINISHED,
                task.id,
                error_code=task.last_error_code,
            )
        raise DataValidationError("Validation task has not finished")

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, ValidationExecutionError):
            return exc.code[:80], exc.safe_message[:500]
        if isinstance(exc, (DataValidationError, ValueError, LookupError)):
            return (
                "validation_input_invalid",
                "Authoritative validation input is missing or inconsistent.",
            )
        return (
            "validation_execution_error",
            "Validation task execution failed.",
        )
