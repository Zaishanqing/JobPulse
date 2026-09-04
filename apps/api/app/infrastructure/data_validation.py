from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from types import TracebackType
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.data_validation.domain import (
    DataValidationError,
    DataValidationTask,
    DataValidationTaskStatus,
    ValidatedBundleSnapshot,
    ValidationConclusion,
    ValidationReport,
    VALIDATION_RULESET_VERSION,
    bundle_identity,
    compute_validation_policy_binding_version,
    is_validation_policy_binding_version,
    validated_snapshot_idempotency_key,
    validation_report_idempotency_key,
    validation_task_idempotency_key,
)
from app.contexts.data_validation.application import ValidationExecutionError
from app.contexts.data_validation.ports import (
    SkillCatalogReference,
    ValidationGovernanceTaskReference,
    ValidationInput,
)
from app.contexts.data_validation.validators import canonical_fact_key
from app.contexts.jd_lifecycle.ports import (
    ValidationPublicationGateDecision,
)
from app.contexts.extraction_tasks.drafts import map_bundle_to_framework_draft
from app.contexts.extraction_tasks.ports import (
    DataValidationMode,
    ValidationDraftGateState,
    ValidationTaskReference,
)
from app.domain.jd_skill_catalog import (
    CatalogClassification,
    CatalogAlias,
    CatalogSkill,
    resolve_catalog_skill,
)
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.models.data_validation import (
    DataValidationTask as DataValidationTaskRow,
)
from app.models.data_validation import ValidatedFactHash as ValidatedFactHashRow
from app.models.data_validation import (
    ValidatedBundleSnapshot as ValidatedBundleSnapshotRow,
)
from app.models.data_validation import ValidationReport as ValidationReportRow
from app.models.extraction_task import ExtractionTask as ExtractionTaskRow
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_catalog_version import SkillCatalogVersion
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.models.source_jd import SourceJD, SourceJDVersion
from jobgraph_contracts.extraction_bundle import (
    parse_extracted_jd_bundle,
)
class DataValidationPersistenceConflict(RuntimeError):
    pass


class StaleDataValidationTask(DataValidationPersistenceConflict):
    pass


VALIDATION_GOVERNANCE_OBJECT_TYPE = "data_validation_report"
VALIDATED_FACT_COLLECTIONS = {
    "responsibilities": "responsibility",
    "requirements": "requirement",
    "company_facts": "company_fact",
    "employment_facts": "employment_fact",
}


def _canonical_fact_keys(bundle_payload: Mapping) -> set[str]:
    """Canonical fact keys for cross-source duplicate lookup.

    The identity is the explicit fact value (occurrence metadata ignored),
    serialised deterministically; no business hash is involved.
    """
    extraction = thaw_json_object(bundle_payload).get("extraction_result", {})
    keys: set[str] = set()
    for collection, fact_type in VALIDATED_FACT_COLLECTIONS.items():
        for item in extraction.get(collection, []) or []:
            keys.add(canonical_fact_key(fact_type, item))
    return keys


def validation_governance_task_id(validation_report_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"validation-governance:{validation_report_id}",
        )
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _task(row: DataValidationTaskRow) -> DataValidationTask:
    return DataValidationTask(
        id=row.id,
        extraction_task_id=row.extraction_task_id,
        source_jd_version_id=row.source_jd_version_id,
        bundle_id=row.bundle_id,
        policy_version=row.policy_version,
        idempotency_key=row.idempotency_key,
        status=DataValidationTaskStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        retryable=row.retryable,
        lock_version=row.lock_version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _report(row: ValidationReportRow) -> ValidationReport:
    return ValidationReport(
        id=row.id,
        data_validation_task_id=row.data_validation_task_id,
        conclusion=ValidationConclusion(row.conclusion),
        idempotency_key=row.idempotency_key,
        policy_version=row.policy_version,
        report_payload=freeze_json_object(
            row.report_payload, field="report_payload"
        ),
        created_at=_aware(row.created_at),
    )


def _snapshot(row: ValidatedBundleSnapshotRow) -> ValidatedBundleSnapshot:
    return ValidatedBundleSnapshot(
        id=row.id,
        validation_report_id=row.validation_report_id,
        data_validation_task_id=row.data_validation_task_id,
        extraction_task_id=row.extraction_task_id,
        source_jd_version_id=row.source_jd_version_id,
        validation_conclusion=ValidationConclusion(row.validation_conclusion),
        bundle_id=row.bundle_id,
        idempotency_key=row.idempotency_key,
        bundle_payload=freeze_json_object(
            row.bundle_payload, field="bundle_payload"
        ),
        report_payload=freeze_json_object(
            row.report_payload, field="report_payload"
        ),
        created_at=_aware(row.created_at),
    )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise DataValidationPersistenceConflict(
            "Data Validation persistence conflicted"
        ) from exc


def _validate_task_idempotency(task: DataValidationTask) -> None:
    task.__post_init__()
    expected = validation_task_idempotency_key(
        task.extraction_task_id,
        task.bundle_id,
        task.policy_version,
    )
    if task.idempotency_key != expected:
        raise DataValidationError(
            "DataValidationTask idempotency_key does not match its natural key"
        )


class SqlAlchemyDataValidationTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: str) -> DataValidationTask | None:
        row = self._session.get(DataValidationTaskRow, task_id)
        return _task(row) if row is not None else None

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> DataValidationTask | None:
        row = (
            self._session.query(DataValidationTaskRow)
            .filter(DataValidationTaskRow.idempotency_key == idempotency_key)
            .one_or_none()
        )
        return _task(row) if row is not None else None

    def list_by_extraction_and_policy(
        self,
        extraction_task_id: str,
        source_jd_version_id: str,
        policy_version: str,
    ) -> tuple[DataValidationTask, ...]:
        rows = (
            self._session.query(DataValidationTaskRow)
            .filter(
                DataValidationTaskRow.extraction_task_id == extraction_task_id,
                DataValidationTaskRow.source_jd_version_id
                == source_jd_version_id,
                DataValidationTaskRow.policy_version == policy_version,
            )
            .order_by(
                DataValidationTaskRow.created_at.asc(),
                DataValidationTaskRow.id.asc(),
            )
            .all()
        )
        return tuple(_task(row) for row in rows)

    def add(self, task: DataValidationTask) -> DataValidationTask:
        _validate_task_idempotency(task)
        extraction = self._session.get(
            ExtractionTaskRow, task.extraction_task_id
        )
        if extraction is None:
            raise DataValidationError("ExtractionTask lineage does not exist")
        if extraction.source_jd_version_id != task.source_jd_version_id:
            raise DataValidationError(
                "ExtractionTask and SourceJDVersion lineage do not match"
            )
        if extraction.status != "succeeded" or extraction.bundle_payload is None:
            raise DataValidationError(
                "DataValidationTask requires a succeeded ExtractionTask Bundle"
            )
        if (
            bundle_identity(extraction.bundle_payload)
            != task.bundle_id
        ):
            raise DataValidationError(
                "DataValidationTask bundle bundle_id does not match ExtractionTask"
            )
        row = DataValidationTaskRow(
            id=task.id,
            extraction_task_id=task.extraction_task_id,
            source_jd_version_id=task.source_jd_version_id,
            bundle_id=task.bundle_id,
            policy_version=task.policy_version,
            idempotency_key=task.idempotency_key,
            status=task.status.value,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            started_at=task.started_at,
            finished_at=task.finished_at,
            last_error_code=task.last_error_code,
            last_error_message=task.last_error_message,
            retryable=task.retryable,
            lock_version=task.lock_version,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        self._session.add(row)
        _flush(self._session)
        return _task(row)

    def claim_next_pending(self) -> DataValidationTask | None:
        now = datetime.now(timezone.utc)
        candidate_id = (
            select(DataValidationTaskRow.id)
            .where(DataValidationTaskRow.status == DataValidationTaskStatus.PENDING.value)
            .order_by(
                DataValidationTaskRow.created_at.asc(),
                DataValidationTaskRow.id.asc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = self._session.execute(
            update(DataValidationTaskRow)
            .where(
                DataValidationTaskRow.id == candidate_id,
                DataValidationTaskRow.status == DataValidationTaskStatus.PENDING.value,
                DataValidationTaskRow.attempt_count
                < DataValidationTaskRow.max_attempts,
            )
            .values(
                status=DataValidationTaskStatus.RUNNING.value,
                attempt_count=DataValidationTaskRow.attempt_count + 1,
                started_at=now,
                finished_at=None,
                last_error_code=None,
                last_error_message=None,
                retryable=False,
                updated_at=now,
                lock_version=DataValidationTaskRow.lock_version + 1,
            )
            .returning(DataValidationTaskRow.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed_id is None:
            return None
        self._session.expire_all()
        row = self._session.get(DataValidationTaskRow, claimed_id)
        return _task(row)

    def save(self, task: DataValidationTask) -> DataValidationTask:
        row = self._session.execute(
            select(DataValidationTaskRow)
            .where(DataValidationTaskRow.id == task.id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(task.id)
        if row.lock_version != task.lock_version:
            raise StaleDataValidationTask(
                f"Stale DataValidationTask {task.id}: expected lock_version "
                f"{task.lock_version}, found {row.lock_version}"
            )
        persisted_identity = (
            row.extraction_task_id,
            row.source_jd_version_id,
            row.bundle_id,
            row.policy_version,
            row.idempotency_key,
            row.max_attempts,
        )
        supplied_identity = (
            task.extraction_task_id,
            task.source_jd_version_id,
            task.bundle_id,
            task.policy_version,
            task.idempotency_key,
            task.max_attempts,
        )
        if persisted_identity != supplied_identity:
            raise DataValidationError(
                "DataValidationTask lineage and idempotency are immutable"
            )
        _validate_task_idempotency(task)
        old_status = DataValidationTaskStatus(row.status)
        allowed_transition = (
            (
                old_status is DataValidationTaskStatus.PENDING
                and task.status is DataValidationTaskStatus.RUNNING
            )
            or (
                old_status is DataValidationTaskStatus.RUNNING
                and task.status
                in {
                    DataValidationTaskStatus.SUCCEEDED,
                    DataValidationTaskStatus.FAILED,
                }
            )
            or (
                old_status is DataValidationTaskStatus.FAILED
                and row.retryable
                and task.status is DataValidationTaskStatus.RUNNING
            )
        )
        if not allowed_transition:
            raise DataValidationError(
                f"Invalid validation task transition: "
                f"{old_status.value} -> {task.status.value}"
            )
        expected_attempt_count = (
            row.attempt_count + 1
            if task.status is DataValidationTaskStatus.RUNNING
            else row.attempt_count
        )
        if task.attempt_count != expected_attempt_count:
            raise DataValidationError(
                "Validation task attempt_count does not match its transition"
            )
        result = self._session.execute(
            update(DataValidationTaskRow)
            .where(
                DataValidationTaskRow.id == task.id,
                DataValidationTaskRow.lock_version == task.lock_version,
                DataValidationTaskRow.status == old_status.value,
                DataValidationTaskRow.attempt_count == row.attempt_count,
            )
            .values(
                status=task.status.value,
                attempt_count=task.attempt_count,
                started_at=task.started_at,
                finished_at=task.finished_at,
                last_error_code=task.last_error_code,
                last_error_message=task.last_error_message,
                retryable=task.retryable,
                updated_at=task.updated_at,
                lock_version=DataValidationTaskRow.lock_version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self._session.expire_all()
            raise StaleDataValidationTask(
                f"Concurrent DataValidationTask transition for {task.id}"
            )
        self._session.expire_all()
        updated_row = self._session.execute(
            select(DataValidationTaskRow).where(
                DataValidationTaskRow.id == task.id
            )
        ).scalar_one()
        return _task(updated_row)


class SqlAlchemyValidationReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, report_id: str) -> ValidationReport | None:
        row = self._session.get(ValidationReportRow, report_id)
        return _report(row) if row is not None else None

    def get_by_task(self, task_id: str) -> ValidationReport | None:
        row = (
            self._session.query(ValidationReportRow)
            .filter(ValidationReportRow.data_validation_task_id == task_id)
            .one_or_none()
        )
        return _report(row) if row is not None else None

    def add(self, report: ValidationReport) -> ValidationReport:
        if report.idempotency_key != validation_report_idempotency_key(
            report.data_validation_task_id
        ):
            raise DataValidationError(
                "ValidationReport idempotency_key does not match its task"
            )
        task = self._session.get(
            DataValidationTaskRow, report.data_validation_task_id
        )
        if task is None or task.status != DataValidationTaskStatus.SUCCEEDED.value:
            raise DataValidationError(
                "ValidationReport requires a succeeded DataValidationTask"
            )
        if task.policy_version != report.policy_version:
            raise DataValidationError(
                "ValidationReport policy lineage does not match its task"
            )
        row = ValidationReportRow(
            id=report.id,
            data_validation_task_id=report.data_validation_task_id,
            conclusion=report.conclusion.value,
            idempotency_key=report.idempotency_key,
            policy_version=report.policy_version,
            report_payload=thaw_json_object(report.report_payload),
            created_at=report.created_at,
        )
        self._session.add(row)
        _flush(self._session)
        return _report(row)


class SqlAlchemyValidatedBundleSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, snapshot_id: str) -> ValidatedBundleSnapshot | None:
        row = self._session.get(ValidatedBundleSnapshotRow, snapshot_id)
        return _snapshot(row) if row is not None else None

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ValidatedBundleSnapshot | None:
        row = (
            self._session.query(ValidatedBundleSnapshotRow)
            .filter(
                ValidatedBundleSnapshotRow.idempotency_key == idempotency_key
            )
            .one_or_none()
        )
        return _snapshot(row) if row is not None else None

    def add(
        self, snapshot: ValidatedBundleSnapshot
    ) -> ValidatedBundleSnapshot:
        if snapshot.idempotency_key != validated_snapshot_idempotency_key(
            snapshot.validation_report_id
        ):
            raise DataValidationError(
                "ValidatedBundleSnapshot idempotency_key does not match its report"
            )
        if snapshot.validation_conclusion is ValidationConclusion.BLOCK:
            raise DataValidationError(
                "Blocked validation cannot produce a downstream snapshot"
            )
        report = self._session.get(
            ValidationReportRow, snapshot.validation_report_id
        )
        task = self._session.get(
            DataValidationTaskRow, snapshot.data_validation_task_id
        )
        extraction = self._session.get(
            ExtractionTaskRow, snapshot.extraction_task_id
        )
        if report is None or task is None or extraction is None:
            raise DataValidationError("Snapshot task/report lineage does not exist")
        if (
            task.status != DataValidationTaskStatus.SUCCEEDED.value
            or report.data_validation_task_id != task.id
            or report.conclusion != snapshot.validation_conclusion.value
            or task.extraction_task_id != snapshot.extraction_task_id
            or task.source_jd_version_id != snapshot.source_jd_version_id
            or task.bundle_id != snapshot.bundle_id
            or freeze_json_object(
                report.report_payload, field="report_payload"
            )
            != snapshot.report_payload
            or freeze_json_object(
                extraction.bundle_payload, field="bundle_payload"
            )
            != snapshot.bundle_payload
        ):
            raise DataValidationError("Snapshot lineage does not match task/report")
        row = ValidatedBundleSnapshotRow(
            id=snapshot.id,
            validation_report_id=snapshot.validation_report_id,
            data_validation_task_id=snapshot.data_validation_task_id,
            extraction_task_id=snapshot.extraction_task_id,
            source_jd_version_id=snapshot.source_jd_version_id,
            validation_conclusion=snapshot.validation_conclusion.value,
            bundle_id=snapshot.bundle_id,
            idempotency_key=snapshot.idempotency_key,
            bundle_payload=thaw_json_object(snapshot.bundle_payload),
            report_payload=thaw_json_object(snapshot.report_payload),
            created_at=snapshot.created_at,
        )
        self._session.add(row)
        _flush(self._session)
        _flush(self._session)
        return _snapshot(row)


class SqlAlchemyValidationGovernanceAdapter:
    """Persist Validation governance in the existing ReviewTask system."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_for_report(
        self,
        *,
        validation_report_id: str,
        data_validation_task_id: str,
        extraction_task_id: str,
        source_jd_version_id: str,
        conclusion: str,
    ) -> ValidationGovernanceTaskReference:
        if conclusion not in {
            ValidationConclusion.WARN.value,
            ValidationConclusion.BLOCK.value,
        }:
            raise DataValidationError(
                "Only warn or block reports require governance"
            )
        task_id = validation_governance_task_id(validation_report_id)
        existing = self._session.get(ReviewTask, task_id)
        if existing is not None:
            return self._reference(
                existing,
                validation_report_id=validation_report_id,
                conclusion=conclusion,
                created=False,
            )
        reason = (
            "Data Validation warning requires human review."
            if conclusion == ValidationConclusion.WARN.value
            else "Data Validation blocked downstream publication."
        )
        review = ReviewTask(
            id=task_id,
            object_type=VALIDATION_GOVERNANCE_OBJECT_TYPE,
            object_id=validation_report_id,
            priority=(
                "high"
                if conclusion == ValidationConclusion.WARN.value
                else "urgent"
            ),
            reason=reason,
            status="pending",
        )
        event = ReviewTaskEvent(
            id=str(uuid4()),
            task_id=task_id,
            actor_user_id="system:data-validation",
            action="create",
            before_status=None,
            after_status="pending",
            comment=reason,
            payload_snapshot={
                "object_type": VALIDATION_GOVERNANCE_OBJECT_TYPE,
                "validation_report_id": validation_report_id,
                "data_validation_task_id": data_validation_task_id,
                "extraction_task_id": extraction_task_id,
                "source_jd_version_id": source_jd_version_id,
                "conclusion": conclusion,
            },
        )
        try:
            with self._session.begin_nested():
                self._session.add_all((review, event))
                self._session.flush()
        except IntegrityError:
            self._session.expire_all()
            existing = self._session.get(ReviewTask, task_id)
            if existing is None:
                raise
            return self._reference(
                existing,
                validation_report_id=validation_report_id,
                conclusion=conclusion,
                created=False,
            )
        return self._reference(
            review,
            validation_report_id=validation_report_id,
            conclusion=conclusion,
            created=True,
        )

    @staticmethod
    def _reference(
        review: ReviewTask,
        *,
        validation_report_id: str,
        conclusion: str,
        created: bool,
    ) -> ValidationGovernanceTaskReference:
        expected_reason = (
            "Data Validation warning requires human review."
            if conclusion == ValidationConclusion.WARN.value
            else "Data Validation blocked downstream publication."
        )
        if (
            review.object_type != VALIDATION_GOVERNANCE_OBJECT_TYPE
            or review.object_id != validation_report_id
            or review.reason != expected_reason
        ):
            raise DataValidationPersistenceConflict(
                "Validation governance task identity is inconsistent"
            )
        return ValidationGovernanceTaskReference(
            task_id=review.id,
            validation_report_id=validation_report_id,
            conclusion=conclusion,
            status=review.status,
            created=created,
        )


class SqlAlchemyDataValidationUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyDataValidationUnitOfWork:
        self._session = self._session_factory()
        self.tasks = SqlAlchemyDataValidationTaskRepository(self._session)
        self.reports = SqlAlchemyValidationReportRepository(self._session)
        self.snapshots = SqlAlchemyValidatedBundleSnapshotRepository(self._session)
        self.governance = SqlAlchemyValidationGovernanceAdapter(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DataValidationPersistenceConflict(
                "Data Validation persistence conflicted"
            ) from exc

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


class SqlAlchemyValidationInputReader:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, task: DataValidationTask) -> ValidationInput:
        with self._session_factory() as session:
            if not is_validation_policy_binding_version(task.policy_version):
                raise ValidationExecutionError(
                    "validation_policy_binding_invalid",
                    "Validation task policy binding has an unsupported format.",
                )
            skills, aliases = load_catalog_entries(session)
            current_catalog_version = catalog_snapshot_version(skills, aliases)
            expected_binding = compute_validation_policy_binding_version(
                VALIDATION_RULESET_VERSION,
                current_catalog_version,
            )
            if task.policy_version != expected_binding:
                raise ValidationExecutionError(
                    "validation_policy_binding_mismatch",
                    "Validation task policy binding is not currently available.",
                )
            extraction = session.get(ExtractionTaskRow, task.extraction_task_id)
            if extraction is None:
                raise ValidationExecutionError(
                    "extraction_task_missing", "Extraction task does not exist."
                )
            if extraction.status != "succeeded" or extraction.bundle_payload is None:
                raise ValidationExecutionError(
                    "extraction_task_not_validatable",
                    "Extraction task is not in a validatable state.",
                )
            if extraction.source_jd_version_id != task.source_jd_version_id:
                raise ValidationExecutionError(
                    "source_jd_version_mismatch",
                    "Validation task and extraction source version differ.",
                )
            version = session.get(SourceJDVersion, task.source_jd_version_id)
            if version is None:
                raise ValidationExecutionError(
                    "source_jd_version_missing", "Source JD version does not exist."
                )
            source = session.get(SourceJD, version.source_jd_id)
            if source is None:
                raise ValidationExecutionError(
                    "source_jd_missing", "Source JD identity does not exist."
                )
            try:
                bundle = parse_extracted_jd_bundle(
                    extraction.bundle_payload
                )
            except (TypeError, ValueError) as exc:
                raise ValidationExecutionError(
                    "extraction_bundle_invalid",
                    "Extraction bundle does not satisfy its contract.",
                ) from exc
            payload = bundle.model_dump(mode="json")
            bundle_id = bundle_identity(payload)
            if bundle_id != task.bundle_id:
                raise ValidationExecutionError(
                    "bundle_id_mismatch",
                    "Validation task bundle bundle_id is inconsistent.",
                )
            if bundle.source_version != version.source_version:
                raise ValidationExecutionError(
                    "source_version_mismatch",
                    "Bundle and source version identifiers differ.",
                )
            return ValidationInput(
                extraction_task_id=extraction.id,
                source_jd_version_id=version.id,
                source_jd_id=source.id,
                source_platform=source.source_platform,
                source_record_id=source.source_record_id,
                raw_text=version.raw_text,
                cleaned_text=payload["cleaned_text"],
                source_version=version.source_version,
                bundle_id=bundle_id,
                bundle=freeze_json_object(payload, field="validation_input.bundle"),
                ruleset_version=VALIDATION_RULESET_VERSION,
                catalog_snapshot_version=current_catalog_version,
                policy_binding_version=task.policy_version,
            )


def catalog_snapshot_version(
    skills: tuple[CatalogSkill, ...], aliases: tuple[CatalogAlias, ...]
) -> str:
    return "catalog-current"


def _content_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entries_from_snapshot(
    snapshot: Mapping[str, object],
) -> tuple[tuple[CatalogSkill, ...], tuple[CatalogAlias, ...]]:
    classifications_by_skill: dict[str, list[CatalogClassification]] = {}
    for row in snapshot.get("classifications", []):
        if not isinstance(row, Mapping):
            continue
        skill_id = str(row.get("skill_id") or "")
        if not skill_id:
            continue
        classifications_by_skill.setdefault(skill_id, []).append(
            CatalogClassification(
                str(row.get("facet") or ""),
                str(row.get("code") or ""),
                bool(row.get("is_primary")),
            )
        )
    skills = tuple(
        CatalogSkill(
            str(item["skill_id"]),
            str(item["skill_name"]),
            item.get("category"),
            item.get("catalog_code"),
            tuple(classifications_by_skill.get(str(item["skill_id"]), ())),
        )
        for item in snapshot.get("skills", [])
        if isinstance(item, Mapping) and item.get("skill_id") and item.get("skill_name")
    )
    aliases = tuple(
        CatalogAlias(str(item["skill_id"]), str(item["alias"]))
        for item in snapshot.get("aliases", [])
        if isinstance(item, Mapping)
        and item.get("skill_id")
        and item.get("alias")
    )
    return skills, aliases


def frozen_catalog_identity(session: Session) -> dict[str, str]:
    latest = (
        session.query(SkillCatalogVersion)
        .order_by(SkillCatalogVersion.version_number.desc())
        .first()
    )
    if latest is not None:
        return {
            "catalog_version": latest.catalog_version,
            "content_hash": _content_hash({"snapshot": dict(latest.snapshot)}),
        }
    classification_rows = session.execute(
        select(SkillClassification, SkillTaxonomyNode)
        .join(
            SkillTaxonomyNode,
            SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
        )
        .order_by(
            SkillClassification.skill_id,
            SkillClassification.facet,
            SkillTaxonomyNode.code,
        )
    ).all()
    classifications: dict[str, list[dict[str, object]]] = {}
    for relation, node in classification_rows:
        classifications.setdefault(relation.skill_id, []).append(
            {
                "facet": relation.facet,
                "code": node.code,
                "is_primary": relation.is_primary,
            }
        )
    skills = [
        {
            "skill_id": row.id,
            "catalog_code": row.catalog_code,
            "canonical_name": row.skill_name,
            "category_code": row.category,
            "classifications": classifications.get(row.id, []),
        }
        for row in session.execute(select(Skill).order_by(Skill.id.asc())).scalars()
    ]
    aliases = [
        {"skill_id": row.skill_id, "alias": row.alias}
        for row in session.execute(
            select(SkillAlias).order_by(
                SkillAlias.skill_id.asc(), SkillAlias.alias.asc()
            )
        ).scalars()
    ]
    content = {
        "classifications": {
            skill_id: sorted(
                classifications.get(skill_id, []),
                key=lambda item: (item["facet"], item["code"]),
            )
            for skill_id in sorted(classifications)
        },
        "skills": sorted(skills, key=lambda item: item["skill_id"]),
        "aliases": sorted(
            aliases, key=lambda item: (item["skill_id"], item["alias"])
        ),
    }
    content_hash = _content_hash(content)
    return {
        "catalog_version": "skill-catalog-content:" + content_hash[:16],
        "content_hash": content_hash,
    }


def frozen_position_catalog_identity(session: Session) -> dict[str, str]:
    from app.models.standard_position import StandardPosition

    positions = [
        {
            "position_code": row.position_code,
            "position_name": row.position_name,
            "taxonomy_version": row.taxonomy_version,
            "taxonomy_family_code": row.taxonomy_family_code,
            "lifecycle_status": row.lifecycle_status,
            "sample_support_status": row.sample_support_status,
        }
        for row in session.execute(
            select(StandardPosition).order_by(StandardPosition.position_code.asc())
        ).scalars()
    ]
    return {
        "catalog_version": "position-taxonomy.v3.0.0",
        "content_hash": _content_hash({"positions": positions}),
    }


def load_catalog_entries(
    session: Session,
) -> tuple[tuple[CatalogSkill, ...], tuple[CatalogAlias, ...]]:
    latest = (
        session.query(SkillCatalogVersion)
        .order_by(SkillCatalogVersion.version_number.desc())
        .first()
    )
    if latest is not None:
        return _entries_from_snapshot(latest.snapshot)
    skill_rows = session.execute(select(Skill).order_by(Skill.id.asc())).scalars()

    classification_rows = session.execute(
        select(SkillClassification, SkillTaxonomyNode)
        .join(
            SkillTaxonomyNode,
            SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
        )
        .order_by(
            SkillClassification.skill_id,
            SkillClassification.facet,
            SkillTaxonomyNode.code,
        )
    ).all()
    by_skill: dict[str, list[CatalogClassification]] = {}
    for relation, node in classification_rows:
        by_skill.setdefault(relation.skill_id, []).append(
            CatalogClassification(
                relation.facet,
                node.code,
                relation.is_primary,
            )
        )
    skills = tuple(
        CatalogSkill(
            row.id,
            row.skill_name,
            row.category,
            row.catalog_code,
            tuple(by_skill.get(row.id, ())),
        )
        for row in skill_rows
    )
    alias_rows = session.execute(
        select(SkillAlias).order_by(
            SkillAlias.skill_id.asc(), SkillAlias.alias.asc()
        )
    ).scalars()
    aliases = tuple(
        CatalogAlias(row.skill_id, row.alias) for row in alias_rows
    )
    return skills, aliases


class SqlAlchemyValidationTaskScheduler:
    """Extraction-facing adapter that schedules on the caller's Session."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = SqlAlchemyDataValidationTaskRepository(session)

    def ensure_for_extraction(
        self,
        *,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_payload,
    ) -> ValidationTaskReference:
        bundle_id = bundle_identity(bundle_payload)
        skills, aliases = load_catalog_entries(self._session)
        binding = compute_validation_policy_binding_version(
            VALIDATION_RULESET_VERSION,
            catalog_snapshot_version(skills, aliases),
        )
        key = validation_task_idempotency_key(
            extraction_task_id, bundle_id, binding
        )
        existing = self._tasks.get_by_idempotency_key(key)
        if existing is not None:
            return self._reference(
                existing,
                created=False,
                extraction_task_id=extraction_task_id,
                source_jd_version_id=source_jd_version_id,
                bundle_id=bundle_id,
                binding=binding,
            )
        candidate = DataValidationTask.create(
            extraction_task_id=extraction_task_id,
            source_jd_version_id=source_jd_version_id,
            bundle_id=bundle_id,
            policy_version=binding,
        )
        try:
            with self._session.begin_nested():
                created = self._tasks.add(candidate)
        except DataValidationPersistenceConflict as exc:
            if not isinstance(exc.__cause__, IntegrityError):
                raise
            self._session.expire_all()
            existing = self._tasks.get_by_idempotency_key(key)
            if existing is None:
                raise
            return self._reference(
                existing,
                created=False,
                extraction_task_id=extraction_task_id,
                source_jd_version_id=source_jd_version_id,
                bundle_id=bundle_id,
                binding=binding,
            )
        return self._reference(
            created,
            created=True,
            extraction_task_id=extraction_task_id,
            source_jd_version_id=source_jd_version_id,
            bundle_id=bundle_id,
            binding=binding,
        )

    @staticmethod
    def _reference(
        task: DataValidationTask,
        *,
        created: bool,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_id: str,
        binding: str,
    ) -> ValidationTaskReference:
        if (
            task.extraction_task_id != extraction_task_id
            or task.source_jd_version_id != source_jd_version_id
            or task.bundle_id != bundle_id
            or task.policy_version != binding
        ):
            raise DataValidationPersistenceConflict(
                "Authoritative validation task does not match its natural key"
            )
        return ValidationTaskReference(
            task_id=task.id,
            status=task.status.value,
            extraction_task_id=task.extraction_task_id,
            source_jd_version_id=task.source_jd_version_id,
            bundle_id=task.bundle_id,
            policy_binding_version=task.policy_version,
            created=created,
        )


class SqlAlchemyValidationDraftGate:
    """Read-only exact-policy gate for Extraction draft import."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = SqlAlchemyDataValidationTaskRepository(session)
        self._reports = SqlAlchemyValidationReportRepository(session)
        self._snapshots = SqlAlchemyValidatedBundleSnapshotRepository(session)

    def read_for_extraction(
        self,
        *,
        mode: DataValidationMode,
        extraction_task_id: str,
        source_jd_version_id: str,
        bundle_payload,
    ) -> ValidationDraftGateState:
        skills, aliases = load_catalog_entries(self._session)
        binding = compute_validation_policy_binding_version(
            VALIDATION_RULESET_VERSION,
            catalog_snapshot_version(skills, aliases),
        )
        candidates = self._tasks.list_by_extraction_and_policy(
            extraction_task_id,
            source_jd_version_id,
            binding,
        )
        for task in candidates:
            authoritative = self._authoritative_snapshot(
                task,
                extraction_task_id=extraction_task_id,
                source_jd_version_id=source_jd_version_id,
                binding=binding,
            )
            if authoritative is None:
                continue
            report, snapshot, bundle_id = authoritative
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                task.bundle_id,
                task=task,
                report=report,
                snapshot=snapshot,
                decision="allow",
            )

        bundle_id = (
            bundle_identity(bundle_payload)
            if bundle_payload is not None
            else ""
        )
        task = next(
            (
                candidate
                for candidate in candidates
                if candidate.bundle_id == bundle_id
            ),
            None,
        )
        if task is None:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                decision="validation_task_missing",
            )
        if task.status in {
            DataValidationTaskStatus.PENDING,
            DataValidationTaskStatus.RUNNING,
        }:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                decision="validation_pending",
            )
        if task.status is DataValidationTaskStatus.FAILED:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                decision="validation_failed",
            )
        report = self._reports.get_by_task(task.id)
        if report is None:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                decision="validation_result_inconsistent",
            )
        if report.policy_version != binding:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                report=report,
                decision="validation_result_inconsistent",
            )
        if report.conclusion is ValidationConclusion.BLOCK:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                report=report,
                decision="validation_blocked",
            )
        snapshot = self._snapshots.get_by_idempotency_key(
            validated_snapshot_idempotency_key(report.id)
        )
        if snapshot is None:
            return self._state(
                mode,
                extraction_task_id,
                source_jd_version_id,
                binding,
                bundle_id,
                task=task,
                report=report,
                decision="validation_snapshot_missing",
            )
        bundle_id = bundle_identity(snapshot.bundle_payload)
        consistent = (
            snapshot.extraction_task_id == extraction_task_id
            and snapshot.source_jd_version_id == source_jd_version_id
            and snapshot.data_validation_task_id == task.id
            and snapshot.validation_report_id == report.id
            and snapshot.validation_conclusion == report.conclusion
            and snapshot.bundle_id == task.bundle_id
            and bundle_id == task.bundle_id
        )
        return self._state(
            mode,
            extraction_task_id,
            source_jd_version_id,
            binding,
            bundle_id,
            task=task,
            report=report,
            snapshot=snapshot,
            decision=(
                "allow" if consistent else "validation_snapshot_inconsistent"
            ),
        )

    def _authoritative_snapshot(
        self,
        task: DataValidationTask,
        *,
        extraction_task_id: str,
        source_jd_version_id: str,
        binding: str,
    ) -> tuple[
        ValidationReport,
        ValidatedBundleSnapshot,
        str,
    ] | None:
        if task.status is not DataValidationTaskStatus.SUCCEEDED:
            return None
        report = self._reports.get_by_task(task.id)
        if (
            report is None
            or report.policy_version != binding
            or report.conclusion
            not in {ValidationConclusion.PASS, ValidationConclusion.WARN}
        ):
            return None
        snapshot = self._snapshots.get_by_idempotency_key(
            validated_snapshot_idempotency_key(report.id)
        )
        if snapshot is None:
            return None
        bundle_id = bundle_identity(snapshot.bundle_payload)
        if (
            snapshot.extraction_task_id != extraction_task_id
            or snapshot.source_jd_version_id != source_jd_version_id
            or snapshot.data_validation_task_id != task.id
            or snapshot.validation_report_id != report.id
            or snapshot.validation_conclusion != report.conclusion
            or snapshot.bundle_id != task.bundle_id
            or bundle_id != task.bundle_id
        ):
            return None
        return report, snapshot, bundle_id

    @staticmethod
    def _state(
        mode,
        extraction_task_id,
        source_jd_version_id,
        binding,
        bundle_id,
        *,
        task=None,
        report=None,
        snapshot=None,
        decision,
    ) -> ValidationDraftGateState:
        return ValidationDraftGateState(
            mode=mode,
            extraction_task_id=extraction_task_id,
            source_jd_version_id=source_jd_version_id,
            task_id=task.id if task else None,
            task_status=task.status.value if task else None,
            conclusion=report.conclusion.value if report else None,
            report_id=report.id if report else None,
            snapshot_id=snapshot.id if snapshot else None,
            policy_binding_version=binding,
            bundle_id=bundle_id,
            snapshot_bundle=(
                freeze_json_object(
                    snapshot.bundle_payload, field="snapshot_bundle"
                )
                if snapshot
                else None
            ),
            snapshot_extraction_task_id=(
                snapshot.extraction_task_id if snapshot else None
            ),
            snapshot_source_jd_version_id=(
                snapshot.source_jd_version_id if snapshot else None
            ),
            snapshot_data_validation_task_id=(
                snapshot.data_validation_task_id if snapshot else None
            ),
            snapshot_validation_report_id=(
                snapshot.validation_report_id if snapshot else None
            ),
            snapshot_validation_conclusion=(
                snapshot.validation_conclusion.value if snapshot else None
            ),
            snapshot_bundle_id=bundle_id,
            decision=decision,
        )


class SqlAlchemyValidationPublicationGate:
    """Read-only enforce-mode gate evaluated inside the JD publication UoW."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = SqlAlchemyDataValidationTaskRepository(session)
        self._reports = SqlAlchemyValidationReportRepository(session)
        self._snapshots = SqlAlchemyValidatedBundleSnapshotRepository(session)

    def evaluate(
        self,
        *,
        jd_id: str,
        parse_result_id: str,
    ) -> ValidationPublicationGateDecision:
        jd = self._session.get(JobDescription, jd_id)
        parsed = self._session.get(JDParseResult, parse_result_id)
        if (
            jd is None
            or parsed is None
            or parsed.jd_id != jd.id
            or not jd.extraction_task_id
            or not jd.source_jd_version_id
            or not jd.source_jd_id
        ):
            return self._deny("validation_lineage_inconsistent")
        version = self._session.get(SourceJDVersion, jd.source_jd_version_id)
        source = (
            self._session.get(SourceJD, version.source_jd_id)
            if version is not None
            else None
        )
        if (
            version is None
            or source is None
            or version.source_jd_id != jd.source_jd_id
        ):
            return self._deny("validation_lineage_inconsistent")
        skills, aliases = load_catalog_entries(self._session)
        binding = compute_validation_policy_binding_version(
            VALIDATION_RULESET_VERSION,
            catalog_snapshot_version(skills, aliases),
        )
        candidates = self._tasks.list_by_extraction_and_policy(
            jd.extraction_task_id,
            jd.source_jd_version_id,
            binding,
        )
        saw_inconsistent = False
        for task in candidates:
            if task.status is not DataValidationTaskStatus.SUCCEEDED:
                continue
            report = self._reports.get_by_task(task.id)
            if report is None:
                saw_inconsistent = True
                continue
            if not self._report_lineage_matches(report, task, binding):
                saw_inconsistent = True
                continue
            if report.conclusion not in {
                ValidationConclusion.PASS,
                ValidationConclusion.WARN,
            }:
                continue
            snapshot = self._snapshots.get_by_idempotency_key(
                validated_snapshot_idempotency_key(report.id)
            )
            if snapshot is None or not self._snapshot_lineage_matches(
                snapshot, task, report
            ):
                saw_inconsistent = True
                continue
            if not self._jd_matches_snapshot(
                jd,
                parsed,
                version,
                source,
                snapshot,
                skills,
                aliases,
            ):
                saw_inconsistent = True
                continue
            if report.conclusion is ValidationConclusion.PASS:
                return self._allow(task, report, snapshot, binding)
            review = self._session.get(
                ReviewTask,
                validation_governance_task_id(report.id),
            )
            if (
                review is None
                or review.object_type != VALIDATION_GOVERNANCE_OBJECT_TYPE
                or review.object_id != report.id
            ):
                return self._deny(
                    "validation_review_missing",
                    task=task,
                    report=report,
                    snapshot=snapshot,
                    binding=binding,
                )
            if review.status != "approved":
                return self._deny(
                    (
                        "validation_review_rejected"
                        if review.status == "rejected"
                        else "validation_review_pending"
                    ),
                    task=task,
                    report=report,
                    snapshot=snapshot,
                    binding=binding,
                    review_task_id=review.id,
                )
            return self._allow(
                task,
                report,
                snapshot,
                binding,
                review_task_id=review.id,
            )

        for task in candidates:
            if task.status is not DataValidationTaskStatus.SUCCEEDED:
                continue
            report = self._reports.get_by_task(task.id)
            if report is None or not self._report_lineage_matches(
                report, task, binding
            ):
                saw_inconsistent = True
                continue
            if report.conclusion is ValidationConclusion.BLOCK:
                return self._deny(
                    "validation_blocked",
                    task=task,
                    report=report,
                    binding=binding,
                )
        if saw_inconsistent:
            return self._deny(
                "validation_result_inconsistent",
                binding=binding,
            )
        if any(
            task.status
            in {
                DataValidationTaskStatus.PENDING,
                DataValidationTaskStatus.RUNNING,
            }
            for task in candidates
        ):
            return self._deny("validation_pending", binding=binding)
        if any(
            task.status is DataValidationTaskStatus.FAILED
            for task in candidates
        ):
            return self._deny("validation_failed", binding=binding)
        return self._deny("validation_task_missing", binding=binding)

    @staticmethod
    def _report_lineage_matches(
        report: ValidationReport,
        task: DataValidationTask,
        binding: str,
    ) -> bool:
        payload = thaw_json_object(report.report_payload)
        lineage = payload.get("lineage")
        return (
            report.data_validation_task_id == task.id
            and report.policy_version == task.policy_version == binding
            and payload.get("conclusion") == report.conclusion.value
            and payload.get("policy_binding_version") == binding
            and isinstance(lineage, Mapping)
            and lineage.get("data_validation_task_id") == task.id
            and lineage.get("extraction_task_id") == task.extraction_task_id
            and lineage.get("source_jd_version_id")
            == task.source_jd_version_id
            and lineage.get("bundle_id") == task.bundle_id
        )

    @staticmethod
    def _snapshot_lineage_matches(
        snapshot: ValidatedBundleSnapshot,
        task: DataValidationTask,
        report: ValidationReport,
    ) -> bool:
        return (
            snapshot.data_validation_task_id == task.id
            and snapshot.validation_report_id == report.id
            and snapshot.extraction_task_id == task.extraction_task_id
            and snapshot.source_jd_version_id == task.source_jd_version_id
            and snapshot.validation_conclusion == report.conclusion
            and snapshot.bundle_id == task.bundle_id
            and bundle_identity(snapshot.bundle_payload)
            == task.bundle_id
        )

    @staticmethod
    def _jd_matches_snapshot(
        jd: JobDescription,
        parsed: JDParseResult,
        version: SourceJDVersion,
        source: SourceJD,
        snapshot: ValidatedBundleSnapshot,
        skills: tuple[CatalogSkill, ...],
        aliases: tuple[CatalogAlias, ...],
    ) -> bool:
        try:
            bundle = parse_extracted_jd_bundle(
                thaw_json_object(snapshot.bundle_payload)
            )
            if bundle.extraction_provider == "reviewed-jd-projection":
                from app.infrastructure.jd_validation_projection import (
                    build_reviewed_extraction_bundle,
                )

                # Must match the timestamp source used when the bundle was
                # staged: the immutable created_at, never the moving
                # updated_at (see SqlAlchemyJDUoW.stage_validation_for_parse_result).
                timestamp = parsed.created_at
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                expected = build_reviewed_extraction_bundle(
                    source_platform=str(jd.source_type).strip().lower(),
                    source_record_id=str(jd.source_name or "").removeprefix("batch:").strip()
                    or str(parsed.extraction_result.get("document_id") or jd.id),
                    source_version=version.source_version,
                    cleaned_text=jd.cleaned_text,
                    extraction_result=parsed.extraction_result,
                    normalized_result=parsed.normalized_result,
                    provider="reviewed-jd-projection",
                    model_version=str(parsed.schema_version),
                    run_id=f"review:{parsed.id}:{timestamp.isoformat()}",
                    timestamp=timestamp,
                )
                return (
                    source.id == jd.source_jd_id
                    and version.raw_text == jd.raw_text
                    and jd.extraction_task_id == snapshot.extraction_task_id
                    and jd.source_jd_version_id == snapshot.source_jd_version_id
                    and expected.model_dump(mode="json")
                    == thaw_json_object(snapshot.bundle_payload)
                )
            material = map_bundle_to_framework_draft(
                bundle,
                framework_jd_id=jd.id,
                raw_text=bundle.cleaned_text,
                fallback_title=version.job_title_raw,
                catalog_skills=skills,
                catalog_aliases=aliases,
            )
        except (TypeError, ValueError):
            return False
        parsed_normalized = json.loads(
            json.dumps(parsed.normalized_result, ensure_ascii=False)
        )
        material_normalized = thaw_json_object(material.normalization_payload)
        for normalized in (parsed_normalized, material_normalized):
            classification = normalized.get("job_classification")
            if isinstance(classification, dict):
                classification.pop("position_id", None)
        return (
            source.id == jd.source_jd_id
            and source.source_platform == bundle.source_platform
            and source.source_record_id == bundle.source_record_id
            and version.source_version == bundle.source_version
            and version.raw_text == jd.raw_text
            and jd.extraction_task_id == snapshot.extraction_task_id
            and jd.source_jd_version_id == snapshot.source_jd_version_id
            and jd.source_document_id == bundle.extraction_result.document_id
            and jd.extraction_bundle_version == bundle.schema_version
            and jd.input_provider == bundle.extraction_provider
            and jd.title == material.title
            and parsed.extraction_result
            == thaw_json_object(material.extraction_payload)
            and parsed_normalized == material_normalized
            and parsed.position_title == material.position_title
            and tuple(parsed.responsibilities or ())
            == material.responsibilities
            and tuple(parsed.required_skills or ())
            == tuple(
                thaw_json_object(item) for item in material.required_skills
            )
            and tuple(parsed.bonus_skills or ())
            == tuple(
                thaw_json_object(item) for item in material.bonus_skills
            )
            and parsed.education == material.education
            and parsed.experience == material.experience
            and parsed.industry == material.industry
            and tuple(parsed.tools or ()) == material.tools
            and tuple(parsed.business_scenarios or ())
            == material.business_scenarios
        )

    @staticmethod
    def _allow(
        task: DataValidationTask,
        report: ValidationReport,
        snapshot: ValidatedBundleSnapshot,
        binding: str,
        *,
        review_task_id: str | None = None,
    ) -> ValidationPublicationGateDecision:
        return ValidationPublicationGateDecision(
            decision="allow",
            code="validation_allowed",
            validation_task_id=task.id,
            validation_report_id=report.id,
            validation_snapshot_id=snapshot.id,
            governance_review_task_id=review_task_id,
            conclusion=report.conclusion.value,
            policy_binding_version=binding,
            bundle_id=snapshot.bundle_id,
        )

    @staticmethod
    def _deny(
        code: str,
        *,
        task: DataValidationTask | None = None,
        report: ValidationReport | None = None,
        snapshot: ValidatedBundleSnapshot | None = None,
        binding: str | None = None,
        review_task_id: str | None = None,
    ) -> ValidationPublicationGateDecision:
        return ValidationPublicationGateDecision(
            decision="deny",
            code=code,
            validation_task_id=task.id if task else None,
            validation_report_id=report.id if report else None,
            validation_snapshot_id=snapshot.id if snapshot else None,
            governance_review_task_id=review_task_id,
            conclusion=report.conclusion.value if report else None,
            policy_binding_version=binding,
            bundle_id=(
                snapshot.bundle_id
                if snapshot is not None
                else task.bundle_id if task is not None else None
            ),
        )


class FrozenSkillCatalogResolutionAdapter:
    def __init__(
        self,
        skills: tuple[CatalogSkill, ...],
        aliases: tuple[CatalogAlias, ...],
        *,
        taxonomy_version: str = "skill-taxonomy-catalog-current",
    ) -> None:
        self._skills = skills
        self._aliases = aliases
        incomplete = [
            item.catalog_code
            for item in skills
            if item.catalog_code is not None and not item.classifications
        ]
        if incomplete:
            raise ValidationExecutionError(
                "skill_taxonomy_catalog_incomplete",
                "Authoritative taxonomy skills are missing classifications: "
                + ", ".join(incomplete[:10]),
            )
        self.taxonomy_version = taxonomy_version

    def resolve(self, reference: SkillCatalogReference):
        return resolve_catalog_skill(
            source_name=reference.source_name,
            claimed_skill_id=reference.claimed_skill_id,
            claimed_canonical_name=reference.claimed_canonical_name,
            skills=self._skills,
            aliases=self._aliases,
        )

    def classification_set(self, catalog_code: str):
        skill = next(
            (item for item in self._skills if item.catalog_code == catalog_code),
            None,
        )
        if skill is None:
            return None
        return skill.canonical_name, skill.classifications


class SqlAlchemyCrossSourceDuplicateAdapter:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        validation_input: ValidationInput,
    ) -> None:
        target_keys = _canonical_fact_keys(validation_input.bundle)
        found: dict[str, set[str]] = {}
        with session_factory() as session:
            rows = session.execute(
                select(
                    ValidatedFactHashRow.canonical_hash,
                    SourceJDVersion.source_jd_id,
                )
                .join(
                    SourceJDVersion,
                    SourceJDVersion.id
                    == ValidatedFactHashRow.source_jd_version_id,
                )
                .join(
                    ValidatedBundleSnapshotRow,
                    ValidatedBundleSnapshotRow.id
                    == ValidatedFactHashRow.snapshot_id,
                )
                .where(
                    ValidatedFactHashRow.canonical_hash.in_(target_keys),
                    ValidatedFactHashRow.source_jd_version_id
                    != validation_input.source_jd_version_id,
                    ValidatedBundleSnapshotRow.bundle_id
                    != validation_input.bundle_id,
                )
                .order_by(
                    ValidatedFactHashRow.canonical_hash.asc(),
                    SourceJDVersion.source_jd_id.asc(),
                )
            ).all()
            for canonical_hash, source_jd_id in rows:
                found.setdefault(canonical_hash, set()).add(source_jd_id)
        self._sources_by_hash = {
            digest: tuple(sorted(source_ids))
            for digest, source_ids in found.items()
        }

    def find_sources(self, canonical_hash: str):
        return self._sources_by_hash.get(canonical_hash, ())


class SqlAlchemyValidationPortFactory:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        taxonomy_version: str = "skill-taxonomy-catalog-current",
    ) -> None:
        self._session_factory = session_factory
        self._taxonomy_version = taxonomy_version

    def catalog(self, bundle_id: str):
        with self._session_factory() as session:
            skills, aliases = load_catalog_entries(session)
        if catalog_snapshot_version(skills, aliases) != bundle_id:
            raise ValidationExecutionError(
                "validation_policy_binding_mismatch",
                "Validation policy binding is not currently available.",
            )
        return FrozenSkillCatalogResolutionAdapter(
            skills,
            aliases,
            taxonomy_version=self._taxonomy_version,
        )

    def current_catalog(self):
        with self._session_factory() as session:
            skills, aliases = load_catalog_entries(session)
        return FrozenSkillCatalogResolutionAdapter(
            skills,
            aliases,
            taxonomy_version=self._taxonomy_version,
        )

    def cross_source_duplicates(self, validation_input: ValidationInput):
        return SqlAlchemyCrossSourceDuplicateAdapter(
            self._session_factory, validation_input
        )
