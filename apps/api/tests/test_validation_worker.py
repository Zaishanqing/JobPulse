from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from sqlalchemy import text

from jobgraph_contracts.source_identity import compute_content_hash

from app.contexts.data_validation.application import (
    ExecuteValidationTaskUseCase,
    ValidateBundleUseCase,
)
from app.contexts.data_validation.domain import (
    DataValidationError,
    DataValidationTask,
    Finding,
    FindingSeverity,
    VALIDATION_RULESET_VERSION,
    ValidatedBundleSnapshot,
    ValidationConclusion,
    ValidationReport,
    bundle_identity,
    compute_validation_policy_binding_version,
)
from app.domain.jd_skill_catalog import CatalogAlias, CatalogSkill
from app.contexts.data_validation.validators import ValidatorSet
from app.infrastructure.data_validation import (
    DataValidationPersistenceConflict,
    SqlAlchemyDataValidationUnitOfWork,
    SqlAlchemyDataValidationTaskRepository,
    SqlAlchemyValidatedBundleSnapshotRepository,
    SqlAlchemyValidationReportRepository,
    SqlAlchemyValidationInputReader,
    SqlAlchemyValidationPortFactory,
    catalog_snapshot_version,
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
from app.workers.validation_tasks import ValidationWorker, ValidationWorkerResult, run_worker
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1
from tests.runtime_database import reset_database_data, SessionLocal, engine
from tests.test_data_validation_validators import RAW_TEXT, _bundle


NOW = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
EMPTY_CATALOG_VERSION = catalog_snapshot_version((), ())
EMPTY_POLICY_BINDING = compute_validation_policy_binding_version(
    VALIDATION_RULESET_VERSION,
    EMPTY_CATALOG_VERSION,
)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


class StaticValidator:
    name = "static"

    def __init__(
        self,
        severity: FindingSeverity | None,
        *,
        counter: list[int] | None = None,
        lock: Lock | None = None,
    ) -> None:
        self._severity = severity
        self._counter = counter
        self._lock = lock

    def validate(self, context):
        if self._counter is not None:
            if self._lock is None:
                self._counter[0] += 1
            else:
                with self._lock:
                    self._counter[0] += 1
        if self._severity is None:
            return ()
        return (
            Finding(
                "static_finding",
                self._severity,
                "$",
                "Static test finding.",
                self.name,
            ),
        )


class ExplodingValidator:
    name = "exploding"

    def validate(self, context):
        raise RuntimeError("secret bundle data must not be persisted")


def _persist_pending(
    *,
    extraction_status: str = "succeeded",
    policy_version: str = EMPTY_POLICY_BINDING,
) -> DataValidationTask:
    payload = ExtractedJDBundleV1.model_validate(_bundle()).model_dump(mode="json")
    with SessionLocal() as session:
        source = SourceJD(
            id="source-1",
            source_platform="boss",
            source_record_id="job-1",
        )
        version = SourceJDVersion(
            id="version-1",
            source_jd_id=source.id,
            source_version=payload["source_version"],
            schema_version="crawler-jd-v1",
            raw_text=RAW_TEXT,
            content_hash=compute_content_hash(RAW_TEXT),
            raw_payload={"source": "test"},
            crawl_time=NOW,
            text_canonicalization_version="raw-v1",
        )
        extraction = ExtractionTask(
            id="extraction-1",
            source_jd_version_id=version.id,
            extraction_mode="llm",
            status=extraction_status,
            provider="test",
            request_id="test-request-a",
            attempt_count=1,
            max_attempts=3,
            retryable=False,
            bundle_payload=payload if extraction_status == "succeeded" else None,
            started_at=NOW,
            finished_at=NOW if extraction_status != "running" else None,
        )
        session.add(source)
        session.flush()
        session.add(version)
        session.flush()
        session.add(extraction)
        session.commit()
    task = DataValidationTask.create(
        extraction_task_id="extraction-1",
        source_jd_version_id="version-1",
        bundle_id=bundle_identity(payload),
        policy_version=policy_version,
        now=NOW,
    )
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(task)
        uow.commit()
    return task


def _worker(
    validator,
    mode: str = "observe",
    *,
    input_reader=None,
    worker_uow_factory=None,
    executor_uow_factory=None,
) -> ValidationWorker:
    def default_uow_factory():
        return SqlAlchemyDataValidationUnitOfWork(SessionLocal)

    worker_uow_factory = worker_uow_factory or default_uow_factory
    executor_uow_factory = executor_uow_factory or default_uow_factory
    executor = ExecuteValidationTaskUseCase(
        executor_uow_factory,
        input_reader or SqlAlchemyValidationInputReader(SessionLocal),
        SqlAlchemyValidationPortFactory(SessionLocal),
        ValidateBundleUseCase(ValidatorSet((validator,))),
    )
    return ValidationWorker(
        mode=mode,
        uow_factory=worker_uow_factory,
        executor=executor,
    )


def _persist_named_pending(
    name: str,
    *,
    policy_version: str = EMPTY_POLICY_BINDING,
    now: datetime = NOW,
) -> DataValidationTask:
    payload = ExtractedJDBundleV1.model_validate(_bundle()).model_dump(mode="json")
    source_id = f"source-{name}"
    version_id = f"version-{name}"
    extraction_id = f"extraction-{name}"
    with SessionLocal() as session:
        source = SourceJD(
            id=source_id,
            source_platform="boss",
            source_record_id=f"job-{name}",
        )
        version = SourceJDVersion(
            id=version_id,
            source_jd_id=source.id,
            source_version=payload["source_version"],
            schema_version="crawler-jd-v1",
            raw_text=RAW_TEXT,
            content_hash=compute_content_hash(RAW_TEXT),
            raw_payload={"source": "test"},
            crawl_time=now,
            text_canonicalization_version="raw-v1",
        )
        extraction = ExtractionTask(
            id=extraction_id,
            source_jd_version_id=version.id,
            extraction_mode="llm",
            status="succeeded",
            provider="test",
            request_id=f"test-request-{name}",
            attempt_count=1,
            max_attempts=3,
            retryable=False,
            bundle_payload=payload,
            started_at=now,
            finished_at=now,
        )
        session.add(source)
        session.flush()
        session.add(version)
        session.flush()
        session.add(extraction)
        session.commit()
    task = DataValidationTask.create(
        extraction_task_id=extraction_id,
        source_jd_version_id=version_id,
        bundle_id=bundle_identity(payload),
        policy_version=policy_version,
        now=now,
    )
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        uow.tasks.add(task)
        uow.commit()
    return task


def _assert_failed_without_results(task: DataValidationTask, code: str) -> None:
    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "failed"
        assert stored.last_error_code == code
        assert "secret" not in stored.last_error_message
        assert "Traceback" not in stored.last_error_message
        assert (
            session.query(ValidationReportRow)
            .filter_by(data_validation_task_id=task.id)
            .count()
            == 0
        )
        assert (
            session.query(ValidatedBundleSnapshotRow)
            .filter_by(data_validation_task_id=task.id)
            .count()
            == 0
        )


@pytest.mark.parametrize(
    ("severity", "conclusion", "snapshots"),
    [
        (None, ValidationConclusion.PASS, 1),
        (FindingSeverity.WARN, ValidationConclusion.WARN, 1),
        (FindingSeverity.BLOCK, ValidationConclusion.BLOCK, 0),
    ],
)
def test_worker_persists_conclusion_and_snapshot_rules(
    severity, conclusion, snapshots
):
    task = _persist_pending()

    assert _worker(StaticValidator(severity)).run_once() == ValidationWorkerResult.SUCCEEDED

    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        report = (
            session.query(ValidationReportRow)
            .filter_by(data_validation_task_id=task.id)
            .one()
        )
        assert stored.status == "succeeded"
        assert stored.lock_version == 3
        assert report.conclusion == conclusion.value
        assert report.policy_version == task.policy_version
        assert report.report_payload["ruleset_version"] == VALIDATION_RULESET_VERSION
        assert (
            report.report_payload["catalog_snapshot_version"]
            == EMPTY_CATALOG_VERSION
        )
        assert report.report_payload["policy_binding_version"] == task.policy_version
        assert report.report_payload["lineage"] == {
            "data_validation_task_id": task.id,
            "extraction_task_id": "extraction-1",
            "source_jd_version_id": "version-1",
            "source_jd_id": "source-1",
            "bundle_id": task.bundle_id,
            "catalog_snapshot_version": EMPTY_CATALOG_VERSION,
        }
        assert session.query(ValidatedBundleSnapshotRow).count() == snapshots


def test_validator_exception_marks_failed_without_partial_results():
    task = _persist_pending()

    assert _worker(ExplodingValidator()).run_once() == ValidationWorkerResult.FAILED

    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "failed"
        assert stored.last_error_code == "validation_execution_error"
        assert stored.last_error_message == "Validation task execution failed."
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_catalog_snapshot_mismatch_is_execution_failure():
    task = _persist_pending(
        policy_version=compute_validation_policy_binding_version(
            VALIDATION_RULESET_VERSION,
            f"sha256:{'f' * 64}",
        )
    )

    assert _worker(StaticValidator(None)).run_once() == ValidationWorkerResult.FAILED

    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.last_error_code == "validation_policy_binding_mismatch"


def test_off_and_no_work_do_not_modify_database():
    task = _persist_pending()
    assert _worker(StaticValidator(None), "off").run_once() == ValidationWorkerResult.DISABLED
    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, task.id).status == "pending"

    with SessionLocal() as session:
        session.delete(session.get(DataValidationTaskRow, task.id))
        session.commit()
    assert _worker(StaticValidator(None), "enforce").run_once() == ValidationWorkerResult.NO_WORK


def test_claim_is_stable_and_terminal_tasks_are_not_claimed():
    first = _persist_pending()
    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        claimed = uow.tasks.claim_next_pending()
        uow.commit()
    assert claimed.id == first.id
    assert claimed.status.value == "running"
    assert claimed.lock_version == 2

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        assert uow.tasks.claim_next_pending() is None


def test_worker_continues_after_one_failed_task():
    first = _persist_pending()
    worker = _worker(ExplodingValidator())
    assert worker.run_once() == ValidationWorkerResult.FAILED
    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, first.id).status == "failed"
        source = session.get(SourceJD, "source-1")
        source.source_record_id = "job-updated"
        session.commit()
    assert worker.run_once() == ValidationWorkerResult.NO_WORK


@pytest.mark.parametrize("failure_point", ["task", "report", "snapshot"])
def test_final_transaction_rolls_back_every_partial_write(
    monkeypatch, failure_point
):
    task = _persist_pending()

    if failure_point == "task":
        original = SqlAlchemyDataValidationTaskRepository.save

        def fail_succeeded(self, candidate):
            if candidate.status.value == "succeeded":
                original(self, candidate)
                raise RuntimeError("injected completion failure")
            return original(self, candidate)

        monkeypatch.setattr(
            SqlAlchemyDataValidationTaskRepository, "save", fail_succeeded
        )
    elif failure_point == "report":
        original = SqlAlchemyValidationReportRepository.add

        def fail_after_report_flush(self, report):
            original(self, report)
            raise RuntimeError("injected report failure")

        monkeypatch.setattr(
            SqlAlchemyValidationReportRepository,
            "add",
            fail_after_report_flush,
        )
    else:
        original = SqlAlchemyValidatedBundleSnapshotRepository.add

        def fail_after_snapshot_flush(self, snapshot):
            original(self, snapshot)
            raise RuntimeError("injected snapshot failure")

        monkeypatch.setattr(
            SqlAlchemyValidatedBundleSnapshotRepository,
            "add",
            fail_after_snapshot_flush,
        )

    assert _worker(StaticValidator(None)).run_once() == ValidationWorkerResult.FAILED
    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "failed"
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_repeated_completion_does_not_duplicate_results():
    task = _persist_pending()
    worker = _worker(StaticValidator(None))
    assert worker.run_once() == ValidationWorkerResult.SUCCEEDED
    assert worker.run_once() == ValidationWorkerResult.NO_WORK
    with SessionLocal() as session:
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 1
        assert session.get(DataValidationTaskRow, task.id).status == "succeeded"


def test_two_independent_uows_claim_at_most_one_task():
    task = _persist_pending()

    def claim():
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            claimed = uow.tasks.claim_next_pending()
            uow.commit()
            return claimed.id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_ids = list(pool.map(lambda _: claim(), range(2)))

    assert claimed_ids.count(task.id) == 1
    assert claimed_ids.count(None) == 1
    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "running"
        assert stored.lock_version == 2


def test_policy_binding_is_stable_and_bounded():
    first = compute_validation_policy_binding_version(
        VALIDATION_RULESET_VERSION,
        EMPTY_CATALOG_VERSION,
    )
    second = compute_validation_policy_binding_version(
        VALIDATION_RULESET_VERSION,
        EMPTY_CATALOG_VERSION,
    )

    assert first == second
    assert first.startswith("vpb1:")
    assert len(first) == 41
    assert len(first) <= 64
    assert compute_validation_policy_binding_version(
        "validation-policy-v1",
        EMPTY_CATALOG_VERSION,
    ) == "vpb1:validation-policy-v1:catalog-current"
    assert (
        compute_validation_policy_binding_version(
            "validation-policy-v2",
            EMPTY_CATALOG_VERSION,
        )
        != first
    )
    assert (
        compute_validation_policy_binding_version(
            VALIDATION_RULESET_VERSION,
            f"sha256:{'1' * 64}",
        )
        != first
    )


def test_catalog_snapshot_version_is_explicit_and_order_independent():
    skills = (
        CatalogSkill("skill-b", "B", "backend"),
        CatalogSkill("skill-a", "A", "language"),
    )
    aliases = (
        CatalogAlias("skill-b", "Bee"),
        CatalogAlias("skill-a", "Ay"),
    )

    first = catalog_snapshot_version(skills, aliases)
    assert first == "catalog-current"
    assert first == catalog_snapshot_version(
        tuple(reversed(skills)),
        tuple(reversed(aliases)),
    )
    assert first == catalog_snapshot_version(
        (
            CatalogSkill("skill-b", "B", "changed-category"),
            skills[1],
        ),
        aliases,
    )
    assert first == catalog_snapshot_version(
        skills,
        (CatalogAlias("skill-b", "Changed"), aliases[1]),
    )


def test_legacy_policy_version_fails_without_results():
    task = _persist_pending(policy_version=VALIDATION_RULESET_VERSION)

    assert _worker(StaticValidator(None)).run_once() == ValidationWorkerResult.FAILED

    _assert_failed_without_results(task, "validation_policy_binding_invalid")


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_enabled_modes_claim_execute_and_persist(mode):
    task = _persist_pending()

    assert _worker(StaticValidator(None), mode).run_once() == (
        ValidationWorkerResult.SUCCEEDED
    )

    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, task.id).status == "succeeded"
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 1


def test_off_mode_does_not_touch_uow_input_or_validator():
    calls = {"uow": 0, "input": 0, "validator": 0}

    def forbidden_uow():
        calls["uow"] += 1
        raise AssertionError("off mode constructed a UoW")

    class ForbiddenReader:
        def load(self, task):
            calls["input"] += 1
            raise AssertionError("off mode read input")

    class ForbiddenValidator:
        name = "forbidden"

        def validate(self, context):
            calls["validator"] += 1
            raise AssertionError("off mode validated")

    worker = _worker(
        ForbiddenValidator(),
        "off",
        input_reader=ForbiddenReader(),
        worker_uow_factory=forbidden_uow,
        executor_uow_factory=forbidden_uow,
    )

    assert worker.run_once() == ValidationWorkerResult.DISABLED
    assert calls == {"uow": 0, "input": 0, "validator": 0}


def test_no_work_does_not_read_input_or_validate():
    calls = {"input": 0, "validator": 0}

    class ForbiddenReader:
        def load(self, task):
            calls["input"] += 1
            raise AssertionError("no-work read input")

    class ForbiddenValidator:
        name = "forbidden"

        def validate(self, context):
            calls["validator"] += 1
            raise AssertionError("no-work validated")

    assert _worker(
        ForbiddenValidator(),
        input_reader=ForbiddenReader(),
    ).run_once() == ValidationWorkerResult.NO_WORK
    assert calls == {"input": 0, "validator": 0}
    with SessionLocal() as session:
        assert session.query(DataValidationTaskRow).count() == 0
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


@pytest.mark.parametrize("status", ["running", "succeeded", "failed"])
def test_terminal_and_running_tasks_are_not_claimed(status):
    task = _persist_pending()
    with SessionLocal() as session:
        row = session.get(DataValidationTaskRow, task.id)
        row.status = status
        row.attempt_count = 1
        row.started_at = NOW
        row.finished_at = None if status == "running" else NOW
        if status == "failed":
            row.last_error_code = "test_failure"
            row.last_error_message = "Safe failure."
        session.commit()

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        assert uow.tasks.claim_next_pending() is None


def test_repeated_block_completion_never_creates_snapshot():
    task = _persist_pending()
    worker = _worker(StaticValidator(FindingSeverity.BLOCK))

    assert worker.run_once() == ValidationWorkerResult.SUCCEEDED
    assert worker.run_once() == ValidationWorkerResult.NO_WORK

    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, task.id).status == "succeeded"
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_database_uniqueness_limits_task_to_one_report_and_report_to_one_snapshot():
    task = _persist_pending()
    assert _worker(StaticValidator(None)).run_once() == ValidationWorkerResult.SUCCEEDED

    with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
        stored_task = uow.tasks.get(task.id)
        stored_report = uow.reports.get_by_task(task.id)
    duplicate_report = ValidationReport.create(
        task=stored_task,
        conclusion=ValidationConclusion.PASS,
        report_payload={"duplicate": True},
        now=NOW,
    )
    with pytest.raises(DataValidationPersistenceConflict):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.reports.add(duplicate_report)

    with SessionLocal() as session:
        bundle_payload = session.get(
            ExtractionTask,
            task.extraction_task_id,
        ).bundle_payload
    duplicate_snapshot = ValidatedBundleSnapshot.create(
        task=stored_task,
        report=stored_report,
        bundle_payload=bundle_payload,
        now=NOW,
    )
    with pytest.raises(DataValidationPersistenceConflict):
        with SqlAlchemyDataValidationUnitOfWork(SessionLocal) as uow:
            uow.snapshots.add(duplicate_snapshot)

    with SessionLocal() as session:
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 1


class _CountingReader:
    def __init__(self, delegate, counter: list[int], lock: Lock) -> None:
        self._delegate = delegate
        self._counter = counter
        self._lock = lock

    def load(self, task):
        with self._lock:
            self._counter[0] += 1
        return self._delegate.load(task)


@pytest.mark.parametrize(
    ("severity", "snapshots"),
    [(None, 1), (FindingSeverity.BLOCK, 0)],
)
def test_two_workers_execute_claimed_task_exactly_once(severity, snapshots):
    task = _persist_pending()
    lock = Lock()
    input_calls = [0]
    validator_calls = [0]

    def make_worker():
        return _worker(
            StaticValidator(severity, counter=validator_calls, lock=lock),
            input_reader=_CountingReader(
                SqlAlchemyValidationInputReader(SessionLocal),
                input_calls,
                lock,
            ),
        )

    workers = (make_worker(), make_worker())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.run_once(), workers))

    assert results.count(ValidationWorkerResult.SUCCEEDED) == 1
    assert results.count(ValidationWorkerResult.NO_WORK) == 1
    assert input_calls == [1]
    assert validator_calls == [1]
    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, task.id).status == "succeeded"
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == snapshots


def _delete_lineage_row(table_name: str, row_id: str) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.execute(
                text(f"DELETE FROM {table_name} WHERE id = :row_id"),
                {"row_id": row_id},
            )
            connection.commit()
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("extraction_missing", "extraction_task_missing"),
        ("extraction_running", "extraction_task_not_validatable"),
        ("bundle_missing", "extraction_task_not_validatable"),
        ("source_version_missing", "source_jd_version_missing"),
        ("task_source_mismatch", "source_jd_version_mismatch"),
    ],
)
def test_production_input_reader_failures_are_safe(mutation, error_code):
    task = _persist_pending()
    if mutation == "extraction_missing":
        _delete_lineage_row("extraction_tasks", task.extraction_task_id)
    elif mutation == "source_version_missing":
        _delete_lineage_row("source_jd_versions", task.source_jd_version_id)
    elif mutation == "task_source_mismatch":
        with SessionLocal() as session:
            source = SourceJD(
                id="source-other",
                source_platform="boss",
                source_record_id="job-other",
            )
            version = SourceJDVersion(
                id="version-other",
                source_jd_id=source.id,
                source_version="1",
                schema_version="crawler-jd-v1",
                raw_text="other",
                content_hash=compute_content_hash("other"),
                raw_payload={"source": "test"},
                crawl_time=NOW,
                text_canonicalization_version="raw-v1",
            )
            session.add_all((source, version))
            session.flush()
            session.get(
                ExtractionTask, task.extraction_task_id
            ).source_jd_version_id = version.id
            session.commit()
    else:
        with SessionLocal() as session:
            extraction = session.get(ExtractionTask, task.extraction_task_id)
            if mutation == "extraction_running":
                extraction.status = "running"
                extraction.finished_at = None
            else:
                extraction.bundle_payload = None
            session.commit()

    assert _worker(StaticValidator(None)).run_once() == ValidationWorkerResult.FAILED

    _assert_failed_without_results(task, error_code)


class _ReplacingReader:
    def __init__(self, **updates) -> None:
        self._delegate = SqlAlchemyValidationInputReader(SessionLocal)
        self._updates = updates

    def load(self, task):
        return replace(self._delegate.load(task), **self._updates)


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        ({"extraction_task_id": "other-extraction"}, "extraction_task_mismatch"),
        (
            {"source_jd_version_id": "other-version"},
            "source_jd_version_mismatch",
        ),
    ],
)
def test_input_dto_lineage_mismatch_fails_before_validation(updates, error_code):
    task = _persist_pending()
    validator_calls = [0]

    assert _worker(
        StaticValidator(None, counter=validator_calls),
        input_reader=_ReplacingReader(**updates),
    ).run_once() == ValidationWorkerResult.FAILED

    assert validator_calls == [0]
    _assert_failed_without_results(task, error_code)


def test_input_reader_program_error_is_safely_failed():
    task = _persist_pending()

    class ExplodingReader:
        def load(self, claimed_task):
            raise RuntimeError(
                "secret bundle text and key=super-secret must never be persisted"
            )

    assert _worker(
        StaticValidator(None),
        input_reader=ExplodingReader(),
    ).run_once() == ValidationWorkerResult.FAILED

    _assert_failed_without_results(task, "validation_execution_error")


class _CommitFailureController:
    def __init__(self, *, after_commit: bool) -> None:
        self.after_commit = after_commit
        self.injected = False


def _commit_failure_factory(controller: _CommitFailureController):
    class InjectedCommitUoW(SqlAlchemyDataValidationUnitOfWork):
        def commit(self) -> None:
            succeeded = (
                self._session.query(DataValidationTaskRow)
                .filter_by(status="succeeded")
                .count()
                > 0
            )
            if succeeded and not controller.injected:
                controller.injected = True
                if controller.after_commit:
                    super().commit()
                raise RuntimeError("injected commit outcome failure")
            super().commit()

    return lambda: InjectedCommitUoW(SessionLocal)


def test_commit_failure_before_database_commit_rolls_back_and_marks_failed():
    task = _persist_pending()
    controller = _CommitFailureController(after_commit=False)

    assert _worker(
        StaticValidator(None),
        executor_uow_factory=_commit_failure_factory(controller),
    ).run_once() == ValidationWorkerResult.FAILED

    assert controller.injected is True
    _assert_failed_without_results(task, "validation_execution_error")


def test_commit_outcome_unknown_recovers_authoritative_success_without_duplicates():
    task = _persist_pending()
    controller = _CommitFailureController(after_commit=True)

    assert _worker(
        StaticValidator(None),
        executor_uow_factory=_commit_failure_factory(controller),
    ).run_once() == ValidationWorkerResult.SUCCEEDED

    assert controller.injected is True
    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, task.id).status == "succeeded"
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 1


def test_failed_marker_transaction_failure_is_not_disguised_as_success():
    task = _persist_pending()

    class FailMarkFailedUoW(SqlAlchemyDataValidationUnitOfWork):
        def commit(self) -> None:
            failed = (
                self._session.query(DataValidationTaskRow)
                .filter_by(status="failed")
                .count()
                > 0
            )
            if failed:
                raise RuntimeError(
                    "secret failed-marker transaction failure must not leak"
                )
            super().commit()

    with pytest.raises(DataValidationError, match="has not finished"):
        _worker(
            ExplodingValidator(),
            executor_uow_factory=lambda: FailMarkFailedUoW(SessionLocal),
        ).run_once()

    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "running"
        assert stored.last_error_code is None
        assert stored.last_error_message is None
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_failed_marker_commit_outcome_unknown_recovers_as_failed():
    task = _persist_pending()

    class CommitThenRaiseForFailedUoW(SqlAlchemyDataValidationUnitOfWork):
        injected = False

        def commit(self) -> None:
            failed = (
                self._session.query(DataValidationTaskRow)
                .filter_by(status="failed")
                .count()
                > 0
            )
            if failed and not self.__class__.injected:
                self.__class__.injected = True
                super().commit()
                raise RuntimeError("failed marker commit result is unknown")
            super().commit()

    assert _worker(
        ExplodingValidator(),
        executor_uow_factory=lambda: CommitThenRaiseForFailedUoW(SessionLocal),
    ).run_once() == ValidationWorkerResult.FAILED

    with SessionLocal() as session:
        stored = session.get(DataValidationTaskRow, task.id)
        assert stored.status == "failed"
        assert stored.last_error_code == "validation_execution_error"
        assert session.query(ValidationReportRow).count() == 0
        assert session.query(ValidatedBundleSnapshotRow).count() == 0


def test_worker_processes_next_task_after_a_task_failure():
    first = _persist_named_pending("first", now=NOW)
    second = _persist_named_pending(
        "second",
        now=NOW + timedelta(seconds=1),
    )

    class FailOnceValidator:
        name = "fail-once"

        def __init__(self) -> None:
            self.calls = 0

        def validate(self, context):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first task fails")
            return ()

    validator = FailOnceValidator()
    worker = _worker(validator)

    assert worker.run_once() == ValidationWorkerResult.FAILED
    assert worker.run_once() == ValidationWorkerResult.SUCCEEDED
    assert validator.calls == 2
    with SessionLocal() as session:
        assert session.get(DataValidationTaskRow, first.id).status == "failed"
        assert session.get(DataValidationTaskRow, second.id).status == "succeeded"
        assert session.query(ValidationReportRow).count() == 1
        assert session.query(ValidatedBundleSnapshotRow).count() == 1


def test_worker_loop_survives_transient_database_error():
    stop = Event()

    class TransientWorker:
        def __init__(self) -> None:
            self.calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("database starting up")
            stop.set()
            return ValidationWorkerResult.NO_WORK

    class Runtime:
        worker = TransientWorker()

    run_worker(Runtime(), stop=stop, poll_seconds=0.001)
    assert Runtime.worker.calls == 2
