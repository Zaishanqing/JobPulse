from collections.abc import Callable
from datetime import datetime, timezone

import pytest

from app.infrastructure.evaluation import SqlAlchemyEvaluationUnitOfWork
from app.infrastructure.resumes import SqlAlchemyResumeUnitOfWork
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.infrastructure.trends import SqlAlchemyTrendUnitOfWork
from app.models.evaluation import EvaluationReport
from app.models.predicted_position import PredictedPosition
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.task_record import TaskRecord as TaskRow
from app.models.user import User
from app.ports.tasks import TaskLog, TaskPayload, TaskRecord
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    with SessionLocal() as session:
        session.add(
            User(
                id="atomic-user",
                username="atomic-user",
                hashed_password="test-only",
                role="personal_user",
            )
        )
        session.flush()
        session.add(
            Resume(
                id="atomic-resume",
                user_id="atomic-user",
                source_type="text",
                raw_text="atomic",
                parse_status="completed",
            )
        )
        session.commit()
    yield
    reset_database_data()


def _resume_row() -> ResumeParseResult:
    return ResumeParseResult(
        resume_id="atomic-resume",
        education=[],
        projects=[],
        internships=[],
        skills=[],
        certificates=[],
        competitions=[],
        parse_confidence=0.88,
        need_review=True,
    )


def _prediction_row() -> PredictedPosition:
    return PredictedPosition(
        position_name="Atomic predicted position",
        prediction_basis=[],
        related_source_ids=[],
        potential_responsibilities=[],
        potential_skills=[],
        industry_scenarios=[],
        confidence_score=0.8,
        status="candidate",
    )


def _evaluation_row() -> EvaluationReport:
    return EvaluationReport(
        report_type="cluster",
        dataset_id=None,
        metrics={},
        error_cases=[],
        evaluation_status="completed",
        algorithm_version="cluster-rule-eval-v1",
        config_snapshot={},
        evaluated_count=1,
        error_count=0,
    )


CASES = (
    ("resume_parse", SqlAlchemyResumeUnitOfWork, _resume_row, ResumeParseResult),
    ("predicted_position", SqlAlchemyTrendUnitOfWork, _prediction_row, PredictedPosition),
    ("cluster_evaluation", SqlAlchemyEvaluationUnitOfWork, _evaluation_row, EvaluationReport),
)


def _task(case_name: str) -> TaskRecord:
    now = datetime.now(timezone.utc)
    return TaskRecord(
        task_id=f"atomic-{case_name}",
        task_type=f"atomic-{case_name}",
        status="succeeded",
        progress=1.0,
        input_payload=TaskPayload.from_mapping({"case": case_name}),
        result_payload=TaskPayload.from_mapping({"result": case_name}),
        result_reference=f"result:{case_name}",
        error_code=None,
        error_message=None,
        created_by=None,
        attempt_count=1,
        logs=(TaskLog("succeeded", now.isoformat(), "completed"),),
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )


@pytest.mark.parametrize("case_name,uow_type,row_factory,row_type", CASES)
def test_business_result_and_task_commit_together(
    case_name: str,
    uow_type: type,
    row_factory: Callable[[], object],
    row_type: type,
):
    with uow_type(SessionLocal) as uow:
        uow._session.add(row_factory())
        uow.add_task(_task(case_name))
        uow.commit()

    with SessionLocal() as session:
        assert session.query(row_type).count() == 1
        task = session.get(TaskRow, f"atomic-{case_name}")
        assert task is not None
        assert task.task_type == f"atomic-{case_name}"
        assert task.status == "succeeded"
        assert task.progress == 1.0
        assert task.input_payload == {"case": case_name}
        assert task.result_payload == {"result": case_name}
        assert task.result_reference == f"result:{case_name}"


@pytest.mark.parametrize("case_name,uow_type,row_factory,row_type", CASES)
def test_task_add_or_flush_failure_rolls_back_business_result(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    uow_type: type,
    row_factory: Callable[[], object],
    row_type: type,
):
    original_add = SqlAlchemyTaskRepository.add
    rollback_calls = 0
    original_rollback = uow_type.rollback

    def failing_add(repository, record):
        original_add(repository, record)
        repository._session.flush()
        raise RuntimeError("injected task add/flush failure")

    def recording_rollback(uow):
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback(uow)

    monkeypatch.setattr(SqlAlchemyTaskRepository, "add", failing_add)
    monkeypatch.setattr(uow_type, "rollback", recording_rollback)

    with pytest.raises(RuntimeError, match="task add/flush failure"):
        with uow_type(SessionLocal) as uow:
            uow._session.add(row_factory())
            uow.add_task(_task(case_name))
            uow.commit()

    assert rollback_calls == 1
    with SessionLocal() as session:
        assert session.query(row_type).count() == 0
        assert session.get(TaskRow, f"atomic-{case_name}") is None


@pytest.mark.parametrize("case_name,uow_type,row_factory,row_type", CASES)
def test_final_commit_failure_rolls_back_business_result_and_task(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    uow_type: type,
    row_factory: Callable[[], object],
    row_type: type,
):
    rollback_calls = 0
    original_rollback = uow_type.rollback

    def failing_commit(uow):
        uow._session.flush()
        raise RuntimeError("injected final commit failure")

    def recording_rollback(uow):
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback(uow)

    monkeypatch.setattr(uow_type, "commit", failing_commit)
    monkeypatch.setattr(uow_type, "rollback", recording_rollback)

    with pytest.raises(RuntimeError, match="final commit failure"):
        with uow_type(SessionLocal) as uow:
            uow._session.add(row_factory())
            uow.add_task(_task(case_name))
            uow.commit()

    assert rollback_calls == 1
    with SessionLocal() as session:
        assert session.query(row_type).count() == 0
        assert session.get(TaskRow, f"atomic-{case_name}") is None
