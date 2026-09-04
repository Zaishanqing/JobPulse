from datetime import datetime, timezone

import pytest

from jobgraph_contracts.source_identity import compute_content_hash

from app.contexts.data_validation import (
    DataValidationError,
    DataValidationTask,
    ValidatedBundleSnapshot,
    ValidationConclusion,
    ValidationReport,
    bundle_identity,
    validation_task_idempotency_key,
)
from app.infrastructure.data_validation import (
    DataValidationPersistenceConflict,
    SqlAlchemyDataValidationTaskRepository,
    SqlAlchemyDataValidationUnitOfWork,
    StaleDataValidationTask,
)
from app.models.data_validation import (
    DataValidationTask as DataValidationTaskRow,
)
from app.models.data_validation import (
    ValidatedBundleSnapshot as ValidatedBundleSnapshotRow,
)
from app.models.data_validation import ValidationReport as ValidationReportRow
from app.models.extraction_task import ExtractionTask
from app.models.source_jd import SourceJD, SourceJDVersion
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine


NOW = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
BUNDLE = {
    "schema_version": "extracted-jd-bundle-v1",
    "bundle_id": "bundle-1",
    "document": {"id": "doc-1"},
}
BUNDLE_ID = bundle_identity(BUNDLE)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _extraction_lineage() -> tuple[str, str]:
    with SessionLocal() as session:
        source = SourceJD(
            id="source-1",
            source_platform="test",
            source_record_id="record-1",
        )
        version = SourceJDVersion(
            id="source-version-1",
            source_jd_id=source.id,
            source_version="1",
            schema_version="crawler-jd-v1",
            raw_text="raw",
            content_hash=compute_content_hash("raw"),
            raw_payload={"raw": True},
            crawl_time=NOW,
            text_canonicalization_version="raw-v1",
        )
        extraction = ExtractionTask(
            id="extraction-1",
            source_jd_version_id=version.id,
            extraction_mode="llm",
            status="succeeded",
            provider="test",
            request_id="test-request-1",
            attempt_count=1,
            max_attempts=3,
            retryable=False,
            bundle_payload=BUNDLE,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add(source)
        session.flush()
        session.add(version)
        session.flush()
        session.add(extraction)
        session.commit()
        extraction_id = extraction.id
        version_id = version.id
    return extraction_id, version_id


def _task() -> DataValidationTask:
    extraction_id, version_id = _extraction_lineage()
    return DataValidationTask.create(
        extraction_task_id=extraction_id,
        source_jd_version_id=version_id,
        bundle_id=BUNDLE_ID,
        policy_version="policy-v1",
        max_attempts=3,
        now=NOW,
    )


def _persist_complete_flow(
    conclusion: ValidationConclusion = ValidationConclusion.PASS,
) -> tuple[DataValidationTask, ValidationReport, ValidatedBundleSnapshot | None]:
    pending = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        running = uow.tasks.save(pending.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        report = ValidationReport.create(
            task=succeeded,
            conclusion=conclusion,
            report_payload={"summary": {"conclusion": conclusion.value}},
            now=NOW,
        )
        snapshot = (
            ValidatedBundleSnapshot.create(
                task=succeeded,
                report=report,
                bundle_payload=BUNDLE,
                now=NOW,
            )
            if conclusion is not ValidationConclusion.BLOCK
            else None
        )
        uow.reports.add(report)
        if snapshot is not None:
            uow.snapshots.add(snapshot)
        uow.commit()
    return succeeded, report, snapshot


def test_repository_round_trip_preserves_all_snapshot_lineage():
    task, report, snapshot = _persist_complete_flow(ValidationConclusion.WARN)
    assert snapshot is not None

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        stored_task = uow.tasks.get_by_idempotency_key(task.idempotency_key)
        stored_report = uow.reports.get_by_task(task.id)
        stored_snapshot = uow.snapshots.get_by_idempotency_key(
            snapshot.idempotency_key
        )

    assert stored_task == task
    assert stored_report == report
    assert stored_snapshot == snapshot
    assert stored_snapshot.bundle_payload == BUNDLE
    assert stored_snapshot.report_payload == report.report_payload
    assert stored_snapshot.extraction_task_id == task.extraction_task_id
    assert stored_snapshot.source_jd_version_id == task.source_jd_version_id
    assert stored_task.lock_version == 3


def test_normal_state_transitions_increment_lock_version():
    pending = _task()
    assert pending.lock_version == 1

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        added = uow.tasks.add(pending)
        running = uow.tasks.save(added.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        uow.commit()

    assert added.lock_version == 1
    assert running.lock_version == 2
    assert succeeded.lock_version == 3


def test_repositories_flush_but_do_not_commit():
    pending = _task()

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        assert uow.tasks.get(pending.id) is not None

    with SessionLocal() as session:
        assert session.query(DataValidationTaskRow).count() == 0


def test_uow_rolls_back_task_report_and_snapshot_atomically():
    pending = _task()

    with pytest.raises(RuntimeError, match="force rollback"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(pending)
            running = uow.tasks.save(pending.mark_running(NOW))
            succeeded = uow.tasks.save(running.mark_succeeded(NOW))
            report = ValidationReport.create(
                task=succeeded,
                conclusion=ValidationConclusion.PASS,
                report_payload={"result": "pass"},
                now=NOW,
            )
            snapshot = ValidatedBundleSnapshot.create(
                task=succeeded,
                report=report,
                bundle_payload=BUNDLE,
                now=NOW,
            )
            uow.reports.add(report)
            uow.snapshots.add(snapshot)
            raise RuntimeError("force rollback")

    with SessionLocal() as session:
        assert session.query(DataValidationTaskRow).count() == 0
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_task_natural_key_and_idempotency_are_unique():
    first = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(first)
        uow.commit()

    duplicate = DataValidationTask.create(
        extraction_task_id=first.extraction_task_id,
        source_jd_version_id=first.source_jd_version_id,
        bundle_id=first.bundle_id,
        policy_version=first.policy_version,
        now=NOW,
    )
    assert duplicate.idempotency_key == first.idempotency_key

    with pytest.raises(DataValidationPersistenceConflict):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(duplicate)

    with SessionLocal() as session:
        assert session.query(DataValidationTaskRow).count() == 1


def test_block_report_is_persisted_without_snapshot():
    task, report, snapshot = _persist_complete_flow(ValidationConclusion.BLOCK)

    assert task.status.value == "succeeded"
    assert report.conclusion is ValidationConclusion.BLOCK
    assert snapshot is None
    with SessionLocal() as session:
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_repository_rejects_mismatched_source_lineage():
    task = _task()
    mismatched = DataValidationTask(
        **{**task.__dict__, "source_jd_version_id": "other-version"}
    )

    with pytest.raises(DataValidationError, match="do not match"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(mismatched)


def test_repository_rejects_bundle_id_and_snapshot_copy_mismatch():
    task = _task()
    wrong_bundle_id = "bundle-other"
    wrong_bundle = DataValidationTask(
        **{
            **task.__dict__,
            "bundle_id": wrong_bundle_id,
            "idempotency_key": validation_task_idempotency_key(
                task.extraction_task_id,
                wrong_bundle_id,
                task.policy_version,
            ),
        }
    )
    with pytest.raises(DataValidationError, match="bundle_id"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(wrong_bundle)

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(task)
        running = uow.tasks.save(task.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        report = ValidationReport.create(
            task=succeeded,
            conclusion=ValidationConclusion.PASS,
            report_payload={"result": "pass"},
            now=NOW,
        )
        uow.reports.add(report)
        uow.commit()
    mismatched_snapshot = ValidatedBundleSnapshot.create(
        task=succeeded,
        report=report,
        bundle_payload={"different": True},
        now=NOW,
    )
    with pytest.raises(DataValidationError, match="lineage"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.snapshots.add(mismatched_snapshot)


def test_persisted_snapshot_rejects_update_and_delete():
    _, _, snapshot = _persist_complete_flow()
    assert snapshot is not None

    with SessionLocal() as session:
        row = session.get(ValidatedBundleSnapshotRow, snapshot.id)
        row.bundle_payload = {"changed": True}
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        row = session.get(ValidatedBundleSnapshotRow, snapshot.id)
        session.delete(row)
        with pytest.raises(ValueError, match="immutable"):
            session.commit()


def test_repository_rejects_cross_state_jumps_and_terminal_regression():
    pending = _task()
    skipped_to_succeeded = pending.mark_running(NOW).mark_succeeded(NOW)
    with pytest.raises(DataValidationError, match="pending -> succeeded"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(pending)
            uow.tasks.save(skipped_to_succeeded)

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        running = uow.tasks.save(pending.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        uow.commit()

    forged_pending = DataValidationTask(
        **{
            **succeeded.__dict__,
            "status": "pending",
            "attempt_count": 0,
            "started_at": None,
            "finished_at": None,
            "retryable": False,
        }
    )
    with pytest.raises(DataValidationError, match="succeeded -> pending"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.save(forged_pending)


def test_repository_allows_only_retryable_failed_to_running():
    pending = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        running = uow.tasks.save(pending.mark_running(NOW))
        failed = uow.tasks.save(
            running.mark_failed(
                error_code="temporary",
                error_message="retry",
                retryable=True,
                now=NOW,
            )
        )
        uow.commit()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        stored = uow.tasks.save(failed.mark_running(NOW))
        uow.commit()

    assert stored.status.value == "running"
    assert stored.attempt_count == 2
    assert stored.lock_version == 4


def test_repositories_reject_forged_derived_idempotency_keys():
    pending = _task()
    forged_task = DataValidationTask(**pending.__dict__)
    object.__setattr__(forged_task, "idempotency_key", "forged-task")
    with pytest.raises(DataValidationError, match="idempotency_key"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.add(forged_task)

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        running = uow.tasks.save(pending.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        report = ValidationReport.create(
            task=succeeded,
            conclusion=ValidationConclusion.PASS,
            report_payload={"result": "pass"},
            now=NOW,
        )
        uow.commit()

    forged_report = ValidationReport(**report.__dict__)
    object.__setattr__(forged_report, "idempotency_key", "forged-report")
    with pytest.raises(DataValidationError, match="idempotency_key"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.reports.add(forged_report)

    snapshot = ValidatedBundleSnapshot.create(
        task=succeeded,
        report=report,
        bundle_payload=BUNDLE,
        now=NOW,
    )
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.reports.add(report)
        uow.commit()

    forged_snapshot = ValidatedBundleSnapshot(**snapshot.__dict__)
    object.__setattr__(
        forged_snapshot, "idempotency_key", "forged-snapshot"
    )
    with pytest.raises(DataValidationError, match="idempotency_key"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.snapshots.add(forged_snapshot)


def test_stale_session_cannot_overwrite_succeeded_task():
    pending = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        uow.commit()

    session_a = SessionLocal()
    session_b = SessionLocal()
    try:
        repository_a = SqlAlchemyDataValidationTaskRepository(session_a)
        repository_b = SqlAlchemyDataValidationTaskRepository(session_b)
        stale_pending = repository_a.get(pending.id)
        fresh_pending = repository_b.get(pending.id)
        running = repository_b.save(fresh_pending.mark_running(NOW))
        succeeded = repository_b.save(running.mark_succeeded(NOW))
        session_b.commit()

        with pytest.raises(StaleDataValidationTask, match="lock_version"):
            repository_a.save(stale_pending.mark_running(NOW))
        session_a.rollback()
    finally:
        session_a.close()
        session_b.close()

    with SessionLocal() as session:
        row = session.get(DataValidationTaskRow, pending.id)
        assert row.status == "succeeded"
        assert row.lock_version == succeeded.lock_version == 3


def test_two_sessions_pending_to_running_only_one_succeeds():
    pending = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        uow.commit()

    session_a = SessionLocal()
    session_b = SessionLocal()
    try:
        repository_a = SqlAlchemyDataValidationTaskRepository(session_a)
        repository_b = SqlAlchemyDataValidationTaskRepository(session_b)
        candidate_a = repository_a.get(pending.id).mark_running(NOW)
        candidate_b = repository_b.get(pending.id).mark_running(NOW)

        winner = repository_a.save(candidate_a)
        session_a.commit()
        with pytest.raises(StaleDataValidationTask, match="lock_version"):
            repository_b.save(candidate_b)
        session_b.rollback()
    finally:
        session_a.close()
        session_b.close()

    with SessionLocal() as session:
        row = session.get(DataValidationTaskRow, pending.id)
        assert row.status == "running"
        assert row.lock_version == winner.lock_version == 2


def test_old_lock_version_object_cannot_update_current_task():
    pending = _task()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(pending)
        running = uow.tasks.save(pending.mark_running(NOW))
        succeeded = uow.tasks.save(running.mark_succeeded(NOW))
        uow.commit()

    stale_failed = running.mark_failed(
        error_code="late",
        error_message="stale",
        retryable=False,
        now=NOW,
    )
    with pytest.raises(StaleDataValidationTask, match="lock_version"):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.tasks.save(stale_failed)

    with SessionLocal() as session:
        row = session.get(DataValidationTaskRow, pending.id)
        assert row.status == "succeeded"
        assert row.lock_version == succeeded.lock_version == 3
