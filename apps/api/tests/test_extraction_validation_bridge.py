from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import text

from app.api.dependencies.extraction_tasks import get_extraction_task_use_cases
from app.contexts.data_validation.application import (
    ExecuteValidationTaskUseCase,
    ValidateBundleUseCase,
)
from app.contexts.data_validation.domain import (
    Finding,
    FindingSeverity,
)
from app.contexts.data_validation.validators import ValidatorSet
from app.contexts.extraction_tasks import (
    ExtractionValidationBlocked,
    ExtractionValidationFailed,
    ExtractionValidationInconsistent,
    ExtractionValidationPending,
    ExtractionValidationSnapshotMissing,
    ExtractionTaskUseCases,
)
from app.contexts.source_jds import SourceJDUseCases
from app.infrastructure.data_validation import (
    SqlAlchemyDataValidationTaskRepository,
    SqlAlchemyDataValidationUnitOfWork,
    SqlAlchemyValidationInputReader,
    SqlAlchemyValidationPortFactory,
    SqlAlchemyValidationTaskScheduler,
)
from app.infrastructure.extraction_tasks import (
    SqlAlchemyExtractionTaskUnitOfWork,
)
from app.infrastructure.source_jds import SqlAlchemySourceJDUnitOfWork
from app.models.data_validation import DataValidationTask
from app.models.data_validation import ValidatedBundleSnapshot, ValidationReport
from app.models.extraction_task import ExtractionTask
from app.models.jd import JobDescription
from app.models.skill import Skill
from app.main import app
from app.workers.validation_tasks import ValidationWorker, ValidationWorkerResult
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from tests.test_extraction_draft_import import (
    FakeProvider,
    _envelope,
    _headers,
    client,
)


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


class _StaticValidator:
    name = "bridge-static"

    def __init__(self, severity: FindingSeverity | None = None) -> None:
        self._severity = severity

    def validate(self, context):
        if self._severity is None:
            return ()
        return (
            Finding(
                "bridge_finding",
                self._severity,
                "$",
                "Bridge test finding.",
                self.name,
            ),
        )


def _use_cases(mode: str, provider: FakeProvider | None = None):
    return ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal, mode),
        provider or FakeProvider(),
        3,
        data_validation_mode=mode,
    )


def _succeed(mode: str, provider: FakeProvider | None = None):
    with SessionLocal() as session:
        session.add(
            Skill(
                id="skill-python",
                skill_name="Python",
                category="programming_language",
            )
        )
        session.commit()
    source = SourceJDUseCases(
        lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)
    ).import_source_jd(_envelope())
    use_cases = _use_cases(mode, provider)
    task = use_cases.create_extraction_task(source.source_jd_version_id, "llm")
    return use_cases, use_cases.run_extraction_task(task.id)


def _run_validation(severity: FindingSeverity | None = None):
    def uow_factory():
        return SqlAlchemyDataValidationUnitOfWork(SessionLocal)

    executor = ExecuteValidationTaskUseCase(
        uow_factory,
        SqlAlchemyValidationInputReader(SessionLocal),
        SqlAlchemyValidationPortFactory(SessionLocal),
        ValidateBundleUseCase(ValidatorSet((_StaticValidator(severity),))),
    )
    return ValidationWorker(
        mode="enforce",
        uow_factory=uow_factory,
        executor=executor,
    ).run_once()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("off", 0), ("observe", 1), ("enforce", 1)],
)
def test_extraction_success_schedules_by_mode(mode: str, expected: int):
    _, task = _succeed(mode)

    with SessionLocal() as session:
        rows = session.query(DataValidationTask).all()
        assert len(rows) == expected
        if rows:
            assert rows[0].status == "pending"
            assert rows[0].extraction_task_id == task.id
            assert rows[0].source_jd_version_id == task.source_jd_version_id
            assert rows[0].policy_version.startswith("vpb1:")


def test_repeated_succeeded_run_ensures_without_recalling_provider():
    provider = FakeProvider()
    use_cases, task = _succeed("observe")
    use_cases._provider = provider

    first = use_cases.run_extraction_task(task.id)
    second = use_cases.run_extraction_task(task.id)

    assert first.id == second.id == task.id
    assert provider.calls == 0
    with SessionLocal() as session:
        assert session.query(DataValidationTask).count() == 1


def test_catalog_content_change_keeps_explicit_policy_binding():
    use_cases, task = _succeed("observe")
    with SessionLocal() as session:
        first = session.query(DataValidationTask).one()
        first_binding = first.policy_version
        session.add(
            Skill(id="skill-sql", skill_name="SQL", category="query_language")
        )
        session.commit()

    use_cases.run_extraction_task(task.id)

    with SessionLocal() as session:
        rows = session.query(DataValidationTask).order_by(
            DataValidationTask.created_at
        ).all()
        assert len(rows) == 1
        assert rows[0].policy_version == first_binding


def test_scheduler_failure_rolls_back_extraction_success(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("forced scheduler failure")

    monkeypatch.setattr(
        SqlAlchemyValidationTaskScheduler,
        "ensure_for_extraction",
        fail,
    )
    with SessionLocal() as session:
        session.add(
            Skill(
                id="skill-python",
                skill_name="Python",
                category="programming_language",
            )
        )
        session.commit()
    source = SourceJDUseCases(
        lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)
    ).import_source_jd(_envelope())
    use_cases = _use_cases("observe")
    task = use_cases.create_extraction_task(source.source_jd_version_id, "llm")

    with pytest.raises(RuntimeError, match="forced scheduler failure"):
        use_cases.run_extraction_task(task.id)

    with SessionLocal() as session:
        persisted = session.get(ExtractionTask, task.id)
        assert persisted.status == "running"
        assert persisted.bundle_payload is None
        assert session.query(DataValidationTask).count() == 0


def test_observe_imports_while_pending_and_never_waits_for_worker():
    use_cases, task = _succeed("observe")

    draft = use_cases.import_extraction_bundle(task.id)

    assert draft.extraction_task_id == task.id
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 1
        assert session.query(DataValidationTask).one().status == "pending"


def test_observe_block_does_not_revoke_or_block_draft():
    use_cases, task = _succeed("observe")
    assert _run_validation(FindingSeverity.BLOCK) is ValidationWorkerResult.SUCCEEDED

    first = use_cases.import_extraction_bundle(task.id)
    second = use_cases.import_extraction_bundle(task.id)

    assert first == second


def test_enforce_pending_then_pass_snapshot_allows_import():
    use_cases, task = _succeed("enforce")

    with pytest.raises(ExtractionValidationPending):
        use_cases.import_extraction_bundle(task.id)
    assert _run_validation() is ValidationWorkerResult.SUCCEEDED

    draft = use_cases.import_extraction_bundle(task.id)
    assert draft.extraction_task_id == task.id


def test_enforce_warn_snapshot_allows_import():
    use_cases, task = _succeed("enforce")
    assert _run_validation(FindingSeverity.WARN) is ValidationWorkerResult.SUCCEEDED

    draft = use_cases.import_extraction_bundle(task.id)

    assert draft.extraction_task_id == task.id


@pytest.mark.parametrize("severity", [None, FindingSeverity.WARN])
def test_enforce_uses_existing_snapshot_after_extraction_bundle_changes(severity):
    use_cases, task = _succeed("enforce")
    assert _run_validation(severity) is ValidationWorkerResult.SUCCEEDED
    with SessionLocal() as session:
        extraction = session.get(ExtractionTask, task.id)
        extraction.bundle_payload = {
            **extraction.bundle_payload,
            "extraction_provider": "mutated-provider",
        }
        session.commit()

    draft = use_cases.import_extraction_bundle(task.id)

    with SessionLocal() as session:
        jd = session.get(JobDescription, draft.jd_id)
        assert jd.input_provider == "fake-deepseek"
        assert session.query(DataValidationTask).count() == 1


def test_enforce_pending_api_uses_409_and_stable_safe_code():
    use_cases, task = _succeed("enforce")
    app.dependency_overrides[get_extraction_task_use_cases] = lambda: use_cases

    response = client.post(
        f"/api/v1/extraction-tasks/{task.id}/import-draft",
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == 409
    assert response.json()["message"] == (
        "validation_pending: Validation is still pending."
    )


def test_enforce_block_is_stable_gate_error():
    use_cases, task = _succeed("enforce")
    assert _run_validation(FindingSeverity.BLOCK) is ValidationWorkerResult.SUCCEEDED

    with pytest.raises(ExtractionValidationBlocked) as captured:
        use_cases.import_extraction_bundle(task.id)

    assert captured.value.code == "validation_blocked"


def test_enforce_failed_task_is_stable_gate_error():
    use_cases, task = _succeed("enforce")
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        row = session.query(DataValidationTask).one()
        row.status = "failed"
        row.attempt_count = 1
        row.started_at = now
        row.finished_at = now
        row.last_error_code = "validation_execution_error"
        row.last_error_message = "Validation task execution failed."
        session.commit()

    with pytest.raises(ExtractionValidationFailed):
        use_cases.import_extraction_bundle(task.id)


def test_enforce_missing_snapshot_is_stable_gate_error():
    use_cases, task = _succeed("enforce")
    assert _run_validation() is ValidationWorkerResult.SUCCEEDED
    with SessionLocal() as session:
        snapshot = session.query(ValidatedBundleSnapshot).one()
        session.execute(
            text("DELETE FROM validated_bundle_snapshots WHERE id = :id"),
            {"id": snapshot.id},
        )
        session.commit()

    with pytest.raises(ExtractionValidationSnapshotMissing):
        use_cases.import_extraction_bundle(task.id)


def test_enforce_succeeded_without_report_is_inconsistent():
    use_cases, task = _succeed("enforce")
    assert _run_validation() is ValidationWorkerResult.SUCCEEDED
    with SessionLocal() as session:
        session.execute(text("DELETE FROM validated_bundle_snapshots"))
        session.execute(text("DELETE FROM validation_reports"))
        session.commit()

    with pytest.raises(ExtractionValidationInconsistent) as captured:
        use_cases.import_extraction_bundle(task.id)

    assert captured.value.code == "validation_result_inconsistent"


def test_enforce_snapshot_bundle_id_mismatch_is_inconsistent():
    use_cases, task = _succeed("enforce")
    assert _run_validation() is ValidationWorkerResult.SUCCEEDED
    with SessionLocal() as session:
        report = session.query(ValidationReport).one()
        session.execute(
            text(
                "UPDATE validated_bundle_snapshots "
                "SET bundle_id = :fingerprint "
                "WHERE validation_report_id = :report_id"
            ),
            {
                "fingerprint": f"sha256:{'0' * 64}",
                "report_id": report.id,
            },
        )
        session.commit()

    with pytest.raises(ExtractionValidationInconsistent) as captured:
        use_cases.import_extraction_bundle(task.id)

    assert captured.value.code == "validation_snapshot_inconsistent"


def test_mode_switch_keeps_existing_draft_forward_only():
    observe, task = _succeed("observe")
    existing = observe.import_extraction_bundle(task.id)
    enforce = _use_cases("enforce")

    returned = enforce.import_extraction_bundle(task.id)

    assert returned == existing
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 1


def test_scheduler_adapter_uses_same_session_and_does_not_commit():
    _, task = _succeed("off")
    with SessionLocal() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        extraction = session.get(ExtractionTask, task.id)
        adapter = SqlAlchemyValidationTaskScheduler(session)
        reference = adapter.ensure_for_extraction(
            extraction_task_id=task.id,
            source_jd_version_id=task.source_jd_version_id,
            bundle_payload=extraction.bundle_payload,
        )
        assert reference.created is True
        session.rollback()
    with SessionLocal() as session:
        assert (
            SqlAlchemyDataValidationTaskRepository(session).get(reference.task_id)
            is None
        )


def test_two_sessions_ensure_one_natural_key_and_same_task(monkeypatch):
    _, task = _succeed("off")
    barrier = Barrier(2)
    original = SqlAlchemyDataValidationTaskRepository.get_by_idempotency_key

    def synchronize_missing_lookup(self, idempotency_key):
        existing = original(self, idempotency_key)
        if existing is None:
            barrier.wait(timeout=10)
        return existing

    monkeypatch.setattr(
        SqlAlchemyDataValidationTaskRepository,
        "get_by_idempotency_key",
        synchronize_missing_lookup,
    )

    def ensure_in_independent_session(_):
        with SessionLocal() as session:
            extraction = session.get(ExtractionTask, task.id)
            scheduler = SqlAlchemyValidationTaskScheduler(session)
            reference = scheduler.ensure_for_extraction(
                extraction_task_id=extraction.id,
                source_jd_version_id=extraction.source_jd_version_id,
                bundle_payload=extraction.bundle_payload,
            )
            session.commit()
            return reference

    with ThreadPoolExecutor(max_workers=2) as pool:
        references = tuple(pool.map(ensure_in_independent_session, range(2)))

    assert references[0].task_id == references[1].task_id
    assert {reference.created for reference in references} == {False, True}
    with SessionLocal() as session:
        rows = session.query(DataValidationTask).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.id == references[0].task_id
        assert row.extraction_task_id == task.id
        assert row.source_jd_version_id == task.source_jd_version_id
        assert row.bundle_id == references[0].bundle_id
        assert row.policy_version == references[0].policy_binding_version
        assert references[0].bundle_id == references[1].bundle_id
        assert (
            references[0].policy_binding_version
            == references[1].policy_binding_version
        )


def test_two_enforce_import_sessions_create_one_draft():
    use_cases, task = _succeed("enforce")
    assert _run_validation() is ValidationWorkerResult.SUCCEEDED

    with ThreadPoolExecutor(max_workers=2) as pool:
        drafts = tuple(
            pool.map(lambda _: use_cases.import_extraction_bundle(task.id), range(2))
        )

    assert drafts[0] == drafts[1]
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 1
