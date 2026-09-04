from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Mapping
from uuid import uuid4

from app.domain.json_types import (
    FrozenJsonObject,
    JsonInputValue,
    MutableJsonObject,
    freeze_json_object,
    thaw_json_object,
)


class DataValidationError(ValueError):
    pass


class DataValidationTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ValidationConclusion(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class FindingSeverity(str, Enum):
    WARN = "warn"
    BLOCK = "block"


VALIDATION_RULESET_VERSION = "validation-policy-v1"
VALIDATION_POLICY_BINDING_PREFIX = "vpb1:"


@dataclass(frozen=True)
class Finding:
    """A deterministic, JSON-safe validation observation."""

    code: str
    severity: FindingSeverity
    path: str
    message: str
    validator: str
    evidence: FrozenJsonObject | None = None
    details: FrozenJsonObject | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", FindingSeverity(self.severity))
        for field_name in ("code", "path", "message", "validator"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DataValidationError(f"Finding {field_name} is required")
        if self.evidence is not None:
            object.__setattr__(
                self,
                "evidence",
                freeze_json_object(self.evidence, field="finding.evidence"),
            )
        if self.details is not None:
            object.__setattr__(
                self,
                "details",
                freeze_json_object(self.details, field="finding.details"),
            )

    def as_dict(self) -> MutableJsonObject:
        payload: MutableJsonObject = {
            "code": self.code,
            "severity": self.severity.value,
            "path": self.path,
            "message": self.message,
            "validator": self.validator,
        }
        if self.evidence is not None:
            payload["evidence"] = thaw_json_object(self.evidence)
        if self.details is not None:
            payload["details"] = thaw_json_object(self.details)
        return payload


@dataclass(frozen=True)
class ValidationResult:
    """In-memory report returned by the synchronous validation use case."""

    conclusion: ValidationConclusion
    findings: tuple[Finding, ...]

    @property
    def report_payload(self) -> FrozenJsonObject:
        return freeze_json_object(
            {
                "conclusion": self.conclusion.value,
                "findings": [finding.as_dict() for finding in self.findings],
            },
            field="validation_result.report_payload",
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _terminal_timestamp(
    started_at: datetime | None,
    now: datetime | None,
) -> datetime:
    """Keep terminal transitions monotonic across small host clock regressions."""
    timestamp = now or _utc_now()
    if started_at is None:
        return timestamp
    try:
        same_timezone_semantics = (
            started_at.utcoffset() is None
        ) == (timestamp.utcoffset() is None)
        if same_timezone_semantics and timestamp < started_at:
            return started_at
    except (TypeError, ValueError, OverflowError):
        pass
    return timestamp


def validation_task_idempotency_key(
    extraction_task_id: str, bundle_id: str, policy_version: str
) -> str:
    return f"validation-task:{extraction_task_id}:{bundle_id}:{policy_version}"


def bundle_identity(bundle_payload: JsonInputValue) -> str:
    if not isinstance(bundle_payload, Mapping):
        raise DataValidationError("bundle payload must be an object")
    value = bundle_payload.get("bundle_id") or bundle_payload.get("source_version")
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError("bundle_id or source_version is required")
    return value.strip()


def compute_validation_policy_binding_version(
    ruleset_version: str,
    catalog_snapshot_version: str,
) -> str:
    if not ruleset_version or not catalog_snapshot_version:
        raise DataValidationError(
            "Validation policy binding components are required"
        )
    return f"{VALIDATION_POLICY_BINDING_PREFIX}{ruleset_version}:{catalog_snapshot_version}"


def is_validation_policy_binding_version(value: str) -> bool:
    if not value.startswith(VALIDATION_POLICY_BINDING_PREFIX):
        return False
    binding = value.removeprefix(VALIDATION_POLICY_BINDING_PREFIX)
    return bool(binding) and ":" in binding


def validation_report_idempotency_key(task_id: str) -> str:
    return f"validation-report:{task_id}"


def validated_snapshot_idempotency_key(report_id: str) -> str:
    return f"validated-bundle:{report_id}"


@dataclass(frozen=True)
class DataValidationTask:
    id: str
    extraction_task_id: str
    source_jd_version_id: str
    bundle_id: str
    policy_version: str
    idempotency_key: str
    status: DataValidationTaskStatus = DataValidationTaskStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    retryable: bool = False
    lock_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", DataValidationTaskStatus(self.status))
        if not all(
            (
                self.id,
                self.extraction_task_id,
                self.source_jd_version_id,
                self.bundle_id,
                self.policy_version,
                self.idempotency_key,
            )
        ):
            raise DataValidationError("DataValidationTask identity is required")
        if self.attempt_count < 0 or self.max_attempts < 1:
            raise DataValidationError("Invalid validation attempt limits")
        if self.attempt_count > self.max_attempts:
            raise DataValidationError("Validation attempts exceed max_attempts")
        if self.lock_version < 1:
            raise DataValidationError("DataValidationTask lock_version must be positive")
        expected_idempotency_key = validation_task_idempotency_key(
            self.extraction_task_id,
            self.bundle_id,
            self.policy_version,
        )
        if self.idempotency_key != expected_idempotency_key:
            raise DataValidationError(
                "DataValidationTask idempotency_key does not match its natural key"
            )
        has_error_code = bool(self.last_error_code)
        has_error_message = bool(self.last_error_message)
        if has_error_code != has_error_message:
            raise DataValidationError(
                "Validation failure code and message must be provided together"
            )
        if self.status is DataValidationTaskStatus.PENDING:
            if (
                self.attempt_count != 0
                or self.started_at is not None
                or self.finished_at is not None
                or has_error_code
                or self.retryable
            ):
                raise DataValidationError("Invalid pending validation task state")
        elif self.status is DataValidationTaskStatus.RUNNING:
            if (
                self.attempt_count < 1
                or self.started_at is None
                or self.finished_at is not None
                or has_error_code
                or self.retryable
            ):
                raise DataValidationError("Invalid running validation task state")
        elif self.status is DataValidationTaskStatus.SUCCEEDED:
            if (
                self.attempt_count < 1
                or self.started_at is None
                or self.finished_at is None
                or has_error_code
                or self.retryable
            ):
                raise DataValidationError("Invalid succeeded validation task state")
        elif (
            self.attempt_count < 1
            or self.started_at is None
            or self.finished_at is None
            or not has_error_code
            or (self.retryable and self.attempt_count >= self.max_attempts)
        ):
            raise DataValidationError("Invalid failed validation task state")
        if self.started_at is not None and self.finished_at is not None:
            try:
                started_is_aware = self.started_at.utcoffset() is not None
                finished_is_aware = self.finished_at.utcoffset() is not None
            except (TypeError, ValueError, OverflowError) as exc:
                raise DataValidationError(
                    "Validation task timestamps have invalid timezone semantics"
                ) from exc
            if started_is_aware != finished_is_aware:
                raise DataValidationError(
                    "Validation task timestamps must both be timezone-aware "
                    "or both be naive"
                )
            try:
                finished_before_started = self.finished_at < self.started_at
            except (TypeError, ValueError, OverflowError) as exc:
                raise DataValidationError(
                    "Validation task timestamps cannot be compared"
                ) from exc
            if finished_before_started:
                raise DataValidationError(
                    "Validation task finished_at cannot precede started_at"
                )

    @classmethod
    def create(
        cls,
        *,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_id: str,
        policy_version: str,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DataValidationTask:
        timestamp = now or _utc_now()
        return cls(
            id=str(uuid4()),
            extraction_task_id=extraction_task_id,
            source_jd_version_id=source_jd_version_id,
            bundle_id=bundle_id,
            policy_version=policy_version,
            idempotency_key=validation_task_idempotency_key(
                extraction_task_id, bundle_id, policy_version
            ),
            max_attempts=max_attempts,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def mark_running(self, now: datetime | None = None) -> DataValidationTask:
        if self.status is DataValidationTaskStatus.FAILED and not self.retryable:
            raise DataValidationError("Failed validation task is not retryable")
        if self.status not in {
            DataValidationTaskStatus.PENDING,
            DataValidationTaskStatus.FAILED,
        }:
            raise DataValidationError("Validation task cannot enter running")
        if self.attempt_count >= self.max_attempts:
            raise DataValidationError("Validation task exhausted max_attempts")
        timestamp = now or _utc_now()
        return replace(
            self,
            status=DataValidationTaskStatus.RUNNING,
            attempt_count=self.attempt_count + 1,
            started_at=timestamp,
            finished_at=None,
            last_error_code=None,
            last_error_message=None,
            retryable=False,
            updated_at=timestamp,
        )

    def mark_succeeded(self, now: datetime | None = None) -> DataValidationTask:
        if self.status is not DataValidationTaskStatus.RUNNING:
            raise DataValidationError("Only a running validation task can succeed")
        timestamp = _terminal_timestamp(self.started_at, now)
        return replace(
            self,
            status=DataValidationTaskStatus.SUCCEEDED,
            finished_at=timestamp,
            retryable=False,
            updated_at=timestamp,
        )

    def mark_failed(
        self,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> DataValidationTask:
        if self.status is not DataValidationTaskStatus.RUNNING:
            raise DataValidationError("Only a running validation task can fail")
        if not error_code or not error_message:
            raise DataValidationError("Validation failure details are required")
        timestamp = _terminal_timestamp(self.started_at, now)
        return replace(
            self,
            status=DataValidationTaskStatus.FAILED,
            finished_at=timestamp,
            last_error_code=error_code,
            last_error_message=error_message,
            retryable=retryable and self.attempt_count < self.max_attempts,
            updated_at=timestamp,
        )


@dataclass(frozen=True)
class ValidationReport:
    id: str
    data_validation_task_id: str
    conclusion: ValidationConclusion
    idempotency_key: str
    policy_version: str
    report_payload: FrozenJsonObject
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "conclusion", ValidationConclusion(self.conclusion))
        object.__setattr__(
            self,
            "report_payload",
            freeze_json_object(self.report_payload, field="report_payload"),
        )
        if not all(
            (
                self.id,
                self.data_validation_task_id,
                self.idempotency_key,
                self.policy_version,
            )
        ):
            raise DataValidationError("ValidationReport identity is required")
        if self.idempotency_key != validation_report_idempotency_key(
            self.data_validation_task_id
        ):
            raise DataValidationError(
                "ValidationReport idempotency_key does not match its task"
            )

    @classmethod
    def create(
        cls,
        *,
        task: DataValidationTask,
        conclusion: ValidationConclusion,
        report_payload: JsonInputValue,
        now: datetime | None = None,
    ) -> ValidationReport:
        if task.status is not DataValidationTaskStatus.SUCCEEDED:
            raise DataValidationError(
                "ValidationReport requires a succeeded DataValidationTask"
            )
        report_id = str(uuid4())
        return cls(
            id=report_id,
            data_validation_task_id=task.id,
            conclusion=conclusion,
            idempotency_key=validation_report_idempotency_key(task.id),
            policy_version=task.policy_version,
            report_payload=freeze_json_object(
                report_payload, field="report_payload"
            ),
            created_at=now or _utc_now(),
        )


@dataclass(frozen=True)
class ValidatedBundleSnapshot:
    id: str
    validation_report_id: str
    data_validation_task_id: str
    extraction_task_id: str
    source_jd_version_id: str
    validation_conclusion: ValidationConclusion
    bundle_id: str
    idempotency_key: str
    bundle_payload: FrozenJsonObject
    report_payload: FrozenJsonObject
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validation_conclusion",
            ValidationConclusion(self.validation_conclusion),
        )
        if self.validation_conclusion is ValidationConclusion.BLOCK:
            raise DataValidationError(
                "Blocked validation cannot produce a downstream snapshot"
            )
        object.__setattr__(
            self,
            "bundle_payload",
            freeze_json_object(self.bundle_payload, field="bundle_payload"),
        )
        object.__setattr__(
            self,
            "report_payload",
            freeze_json_object(self.report_payload, field="report_payload"),
        )
        if not all(
            (
                self.id,
                self.validation_report_id,
                self.data_validation_task_id,
                self.extraction_task_id,
                self.source_jd_version_id,
                self.bundle_id,
                self.idempotency_key,
            )
        ):
            raise DataValidationError("ValidatedBundleSnapshot lineage is required")
        if self.idempotency_key != validated_snapshot_idempotency_key(
            self.validation_report_id
        ):
            raise DataValidationError(
                "ValidatedBundleSnapshot idempotency_key does not match its report"
            )

    @classmethod
    def create(
        cls,
        *,
        task: DataValidationTask,
        report: ValidationReport,
        bundle_payload: JsonInputValue,
        now: datetime | None = None,
    ) -> ValidatedBundleSnapshot:
        if task.status is not DataValidationTaskStatus.SUCCEEDED:
            raise DataValidationError(
                "Snapshot requires a succeeded DataValidationTask"
            )
        if report.data_validation_task_id != task.id:
            raise DataValidationError("Report does not belong to validation task")
        if report.conclusion is ValidationConclusion.BLOCK:
            raise DataValidationError(
                "Blocked validation cannot produce a downstream snapshot"
            )
        return cls(
            id=str(uuid4()),
            validation_report_id=report.id,
            data_validation_task_id=task.id,
            extraction_task_id=task.extraction_task_id,
            source_jd_version_id=task.source_jd_version_id,
            validation_conclusion=report.conclusion,
            bundle_id=task.bundle_id,
            idempotency_key=validated_snapshot_idempotency_key(report.id),
            bundle_payload=freeze_json_object(
                bundle_payload, field="bundle_payload"
            ),
            report_payload=report.report_payload,
            created_at=now or _utc_now(),
        )
