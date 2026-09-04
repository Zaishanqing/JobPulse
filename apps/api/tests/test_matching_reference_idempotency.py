from __future__ import annotations

from app.contexts.matching_learning._applications.matching import (
    _resolve_position_profile,
)
from app.contexts.matching_learning._ports.matching import (
    MatchingServiceReferenceRecord,
)
from app.api.evaluation_data import evaluation_reference_data
from app.infrastructure.matching import SqlAlchemyMatchingRepository
from app.models.matching_service_reference import MatchingServiceReference
from app.models.resume import Resume
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user


class _RecordingContracts:
    def __init__(self) -> None:
        self.enterprise_calls: list[str] = []
        self.standard_calls: list[str] = []

    def position_profile(self, position_id: str):
        self.standard_calls.append(position_id)
        return {"kind": "standard"}

    def enterprise_job_profile(self, job_id: str):
        self.enterprise_calls.append(job_id)
        return {"kind": "enterprise"}


def _record(
    user_id: str, resume_id: str, task_id: str, idempotency_key: str
) -> MatchingServiceReferenceRecord:
    return MatchingServiceReferenceRecord(
        task_id=task_id,
        evaluation_id=None,
        user_id=user_id,
        tenant_id="personal:user",
        resume_id=resume_id,
        position_id="position-1",
        provider="matching-service",
        target_type="standard_position",
        status="pending",
        idempotency_key=idempotency_key,
        access_scope="user:personal:user:user",
    )


def test_resolve_position_profile_routes_enterprise_job_to_enterprise_contract():
    contracts = _RecordingContracts()
    reference = MatchingServiceReferenceRecord(
        task_id="task-1",
        evaluation_id=None,
        user_id="u",
        tenant_id="personal:u",
        resume_id="resume-1",
        position_id="enterprise_job:job-42",
        provider="matching-service",
        target_type="enterprise_job",
        status="pending",
        idempotency_key="idem-1",
        access_scope="user:personal:u:user",
    )
    assert _resolve_position_profile(contracts, reference) == {"kind": "enterprise"}
    assert contracts.enterprise_calls == ["job-42"]
    assert contracts.standard_calls == []


def test_resolve_position_profile_routes_standard_position_to_standard_contract():
    contracts = _RecordingContracts()
    reference = MatchingServiceReferenceRecord(
        task_id="task-1",
        evaluation_id=None,
        user_id="u",
        tenant_id="personal:u",
        resume_id="resume-1",
        position_id="position-1",
        provider="matching-service",
        target_type="standard_position",
        status="pending",
        idempotency_key="idem-1",
        access_scope="user:personal:u:user",
    )
    assert _resolve_position_profile(contracts, reference) == {"kind": "standard"}
    assert contracts.standard_calls == ["position-1"]
    assert contracts.enterprise_calls == []


def test_reference_reuses_row_when_remote_task_is_recreated():
    reset_database_data()
    user_id = create_internal_user("reference_idem_user", "personal_user")
    assert user_id is not None
    with SessionLocal() as session:
        resume = Resume(
            user_id=user_id,
            source_type="text",
            raw_text="",
            parse_status="pending",
        )
        session.add(resume)
        session.commit()
        session.refresh(resume)
        repository = SqlAlchemyMatchingRepository(session)
        first = repository.upsert_service_reference(
            _record(user_id, resume.id, "task-old", "idem-1")
        )
        second = repository.upsert_service_reference(
            _record(user_id, resume.id, "task-new", "idem-1")
        )
        session.commit()

        assert first.task_id == "task-old"
        assert second.task_id == "task-new"
        rows = session.query(MatchingServiceReference).all()
        assert len(rows) == 1
        assert rows[0].task_id == "task-new"
        assert rows[0].idempotency_key == "idem-1"


def test_distinct_run_identities_keep_two_history_rows():
    reset_database_data()
    user_id = create_internal_user("reference_distinct_user", "personal_user")
    assert user_id is not None
    with SessionLocal() as session:
        resume = Resume(
            user_id=user_id,
            source_type="text",
            raw_text="",
            parse_status="pending",
        )
        session.add(resume)
        session.commit()
        session.refresh(resume)
        repository = SqlAlchemyMatchingRepository(session)
        repository.upsert_service_reference(
            _record(
                user_id,
                resume.id,
                "task-run-a",
                "personal-run:resume:position:run-a",
            )
        )
        repository.upsert_service_reference(
            _record(
                user_id,
                resume.id,
                "task-run-b",
                "personal-run:resume:position:run-b",
            )
        )
        session.commit()
        rows = session.query(MatchingServiceReference).all()
        assert len(rows) == 2
        references = repository.list_service_references(user_id)
        assert {item.task_id for item in references} == {"task-run-a", "task-run-b"}
        assert all(
            item.matching_method is None
            and item.overall_score is None
            for item in references
        )
        assert evaluation_reference_data(references[0])["matching_method"] is None
