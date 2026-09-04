from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, SessionLocal
from app.main import app
from app.contexts.matching_learning import (
    MatchingServiceError,
    RemoteEvaluation,
    RemoteTask,
)
from app.models.data_validation import CVDataValidationTask, CVValidationReport
from app.models.enterprise_job import EnterpriseJob
from app.models.matching_service_reference import MatchingServiceReference
from app.models.matching_submission_intent import MatchingSubmissionIntent
from app.models.resume import Resume
from app.models.resume_skill import ResumeSkill
from app.models.source_cv import (
    CVExtractionTask,
    SourceCV,
    SourceCVVersion,
    ValidatedCVSnapshot,
)
from app.models.standard_position import StandardPosition
from app.models.outbox_message import OutboxMessage
from app.profile_index_events import PROFILE_INDEX_EVENT_TYPE
from app.infrastructure.matching import SqlAlchemyMatchingRepository
from tests.user_factory import create_internal_user


client = TestClient(app)


class FakeMatchingService:
    def __init__(self) -> None:
        self.tasks: dict[str, RemoteTask] = {}
        self.tasks_by_idempotency_key: dict[str, RemoteTask] = {}
        self.evaluations: dict[str, RemoteEvaluation] = {}
        self.failures: dict[str, MatchingServiceError] = {}
        self.unavailable = False
        self.correlation_ids: list[str] = []

    def create_task(
        self,
        identity,
        *,
        cv_id: str,
        position_id: str,
        target_type: str,
        use_enterprise_weights: bool,
        generate_learning_path: bool,
        cv_profile: dict[str, object] | None,
        position_profile: dict[str, object] | None,
        idempotency_key: str,
        correlation_id: str,
    ) -> RemoteTask:
        assert correlation_id
        self.correlation_ids.append(correlation_id)
        if cv_id in self.failures:
            raise self.failures[cv_id]
        assert target_type == "enterprise_job"
        assert use_enterprise_weights is True
        assert generate_learning_path is True
        assert not position_id.startswith("enterprise_job:")
        assert cv_profile is not None and cv_profile["verification_snapshot_id"]
        assert position_profile is not None and (
            position_profile.get("profile_version")
            or position_profile.get("source_version")
        )
        assert position_profile.get("graph_version") not in {None, "", "enterprise-job.disabled"}
        assert idempotency_key.startswith(("enterprise-job:", "personal-run:"))
        assert len(idempotency_key) <= 200
        existing = self.tasks_by_idempotency_key.get(idempotency_key)
        if existing is not None:
            return replace(existing, created=False)
        sequence = len(self.tasks) + 1
        task_id = f"fake-task-{sequence}"
        evaluation_id = f"fake-evaluation-{sequence}"
        cv_profile_version = str(
            cv_profile.get("profile_version") or cv_profile.get("source_version") or "cv"
        )
        position_profile_version = str(
            position_profile.get("profile_version")
            or position_profile.get("source_version")
            or "position"
        )
        task = RemoteTask(
            task_id=task_id,
            status="succeeded",
            evaluation_id=evaluation_id,
            created=True,
            error_code=None,
            error_message=None,
            attempt=1,
            created_at=None,
            updated_at=None,
            raw={
                "provider": "matching-service",
                "algorithm_version": "fake-enterprise-v1",
                "graph_version": position_profile["graph_version"],
                "source_version": (
                    f"cv={cv_profile.get('source_version', 'cv')}|"
                    f"position={position_profile.get('source_version', 'position')}"
                ),
                "taxonomy_version": (
                    f"cv={cv_profile.get('taxonomy_version', 'cv')}|"
                    f"position={position_profile.get('taxonomy_version', 'position')}"
                ),
                "use_enterprise_weights": True,
            },
            target_type=target_type,
        )
        self.tasks[task_id] = task
        self.tasks_by_idempotency_key[idempotency_key] = task
        self.evaluations[evaluation_id] = RemoteEvaluation(
            evaluation_id=evaluation_id,
            task_id=task_id,
            stale=False,
            evaluation={
                "evaluation_status": "completed",
                "algorithm_version": "fake-enterprise-v1",
                "cv_profile_version": cv_profile_version,
                "position_profile_version": position_profile_version,
            },
            gap_analysis={},
            versions={},
            created_at=None,
            updated_at=None,
            user_id=identity.subject_id,
            resume_id=cv_id,
            validated_cv_snapshot_id=str(cv_profile["verification_snapshot_id"]),
            position_id=str(position_profile["position_id"]),
        )
        return task

    def get_task(self, identity, task_id: str, *, correlation_id: str) -> RemoteTask:
        return self.tasks[task_id]

    def get_evaluation(
        self, identity, evaluation_id: str, *, correlation_id: str
    ) -> RemoteEvaluation:
        assert correlation_id
        self.correlation_ids.append(correlation_id)
        if self.unavailable:
            raise MatchingServiceError("FAKE_UNAVAILABLE", "fake service unavailable")
        return self.evaluations[evaluation_id]


@pytest.fixture
def matching_service_fake():
    fake = FakeMatchingService()
    original = app.state.container
    app.state.container = replace(
        original,
        candidates=replace(original.candidates, matching_service=fake),
    )
    yield fake
    app.state.container = original


@pytest.fixture(autouse=True)
def reset_database(matching_service_fake):
    reset_database_data()
    yield
    reset_database_data()


def _headers(username: str, role: str) -> dict:
    create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={"role": role, "username": username, "password": "password123"},
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _ensure_validated_cv_snapshot(resume_id: str) -> None:
    """Create minimum ValidatedCVSnapshot so MatchingContractReader.cv_profile
    does not return None for this resume."""
    with SessionLocal() as session:
        resume = session.get(Resume, resume_id)
        if resume is None:
            raise RuntimeError(f"Resume {resume_id} not found")
        source = SourceCV(
            id=str(uuid4()),
            owner_id=resume.user_id,
            source_platform="test-enterprise-candidates",
            source_record_id=f"test:{resume_id}",
        )
        version = SourceCVVersion(
            id=str(uuid4()),
            source_cv_id=source.id,
            raw_text=resume.raw_text or "test resume",
            source_version="1",
        )
        session.add(source)
        session.flush()
        session.add(version)
        session.flush()
        extraction = CVExtractionTask(
            id=str(uuid4()),
            source_cv_version_id=version.id,
            request_id="test:enterprise-candidates:" + str(uuid4()),
            status="succeeded",
            confirmation_status="confirmed",
            confirmed_at=datetime.now(timezone.utc),
            confirmed_by=resume.user_id,
            review_id="review-" + str(uuid4()),
            review_revision=1,
        )
        session.add(extraction)
        session.flush()
        validation_task = CVDataValidationTask(
            id=str(uuid4()),
            cv_extraction_task_id=extraction.id,
            source_cv_version_id=version.id,
            policy_version="contract-probe-v1",
            status="succeeded",
        )
        report = CVValidationReport(
            id=str(uuid4()),
            cv_data_validation_task_id=validation_task.id,
            conclusion="pass",
            policy_version="contract-probe-v1",
            report_payload={},
        )
        skill_rows = (
            session.query(ResumeSkill)
            .filter(ResumeSkill.resume_id == resume.id)
            .order_by(ResumeSkill.id)
            .all()
        )
        normalized_skills = [
            {
                "source_item_id": f"skill-{index}",
                "source_scope": "skills",
                "source_name": row.raw_skill,
                "skill_id": row.skill_id,
                "canonical_name": row.raw_skill,
                "category_code": "technical_skill",
                "resolution_status": "resolved",
                "normalization_confidence": 1.0,
                "resolution_source": "explicit_mapping",
            }
            for index, row in enumerate(skill_rows)
        ]
        extracted_skills = [
            {
                "item_id": f"skill-{index}",
                "name": row.raw_skill,
                "item_type": "technical_skill",
                "evidence": {
                    "source_id": "legacy",
                    "quote": row.raw_skill,
                    "start": 0,
                    "end": len(row.raw_skill),
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            }
            for index, row in enumerate(skill_rows)
        ]
        snapshot = ValidatedCVSnapshot(
            id=str(uuid4()),
            cv_extraction_task_id=extraction.id,
            source_cv_version_id=version.id,
            snapshot_revision=1,
            confirmed_at=datetime.now(timezone.utc),
            confirmed_by=resume.user_id,
            extraction_provider="test",
            model="test",
            prompt_version="test",
            extraction_schema_version="2.4",
            normalization_version="2.0",
            taxonomy_version="skill-taxonomy-snapshot.v1",
            validation_report_id=report.id,
            policy_version="contract-probe-v1",
            conclusion="pass",
            extraction_payload={
                "document_id": version.id,
                "skills": extracted_skills,
                "education": [],
                "work_experience": [],
                "project_experience": [],
                "languages": [],
                "certificates": [],
                "awards": [],
                "self_evaluation": [],
            },
            normalized_payload={
                "document_id": version.id,
                "normalized_skills": normalized_skills,
                "unresolved_items": [],
            },
            findings_payload={},
            execution_metadata={},
            evidence_payload={"document_id": version.id, "skills": extracted_skills},
        )
        session.add(validation_task)
        session.flush()
        session.add(report)
        session.flush()
        session.add(snapshot)
        session.flush()
        resume.source_cv_version_id = version.id
        resume.validated_cv_snapshot_id = snapshot.id
        extraction.latest_validated_cv_snapshot_id = snapshot.id
        session.commit()


def _candidate_resume(headers: dict, text: str = "Python FastAPI Docker") -> str:
    resume_id = client.post(
        "/api/v1/resumes/text", headers=headers, json={"raw_text": text}
    ).json()["data"]["resume_id"]
    assert client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers).status_code == 200
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile", headers=headers
    ).status_code == 200
    _ensure_validated_cv_snapshot(resume_id)
    return resume_id


def _enterprise_job(headers: dict, name: str, *, with_weights: bool = True) -> str:
    enterprise_id = client.post(
        "/api/v1/enterprises",
        headers=headers,
        json={"enterprise_name": name},
    ).json()["data"]["enterprise_id"]
    job_id = client.post(
        "/api/v1/enterprise-jobs",
        headers=headers,
        json={"enterprise_id": enterprise_id, "title": "Python Engineer"},
    ).json()["data"]["enterprise_job_id"]
    if with_weights:
        weights = client.put(
            f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
            headers=headers,
            json={
                "weights": [
                    {"skill_id": "skill_python", "weight": 0.7, "is_required": True},
                    {"skill_id": "skill_docker", "weight": 0.3, "is_bonus": True},
                ]
            },
        )
        assert weights.status_code == 200
    with SessionLocal() as session:
        position = StandardPosition(
            position_code=f"TEST_{name.upper().replace(' ', '_')}",
            position_name=name,
            taxonomy_family_code="test-taxonomy-v1",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
            core_responsibilities=["Build services"],
            required_skills=[
                {
                    "skill_id": "skill_python",
                    "skill_name": "Python",
                    "weight": 0.7,
                    "importance_level": "required",
                }
            ],
            bonus_skills=[
                {
                    "skill_id": "skill_docker",
                    "skill_name": "Docker",
                    "weight": 0.3,
                    "importance_level": "bonus",
                }
            ],
            status="published",
        )
        session.add(position)
        session.flush()
        job = session.get(EnterpriseJob, job_id)
        job.standard_position_id = position.id
        job.status = "published"
        session.commit()
    return job_id


def _submit(personal_headers: dict, job_id: str, resume_id: str):
    return client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions",
        headers=personal_headers,
        json={"resume_id": resume_id},
    )


def _submission_id(personal_headers: dict, job_id: str, resume_id: str) -> str:
    response = _submit(personal_headers, job_id, resume_id)
    assert response.status_code == 200
    return response.json()["data"]["submission_id"]


def _profile_events() -> list[dict[str, object]]:
    with SessionLocal() as session:
        rows = (
            session.query(OutboxMessage)
            .filter(OutboxMessage.event_type == PROFILE_INDEX_EVENT_TYPE)
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .all()
        )
        return [dict(row.payload) for row in rows]


def _vector_disabled_container():
    original = app.state.container
    return replace(
        original,
        resumes=replace(original.resumes, vector_index_enabled=False),
        candidates=replace(original.candidates, vector_index_enabled=False),
    )


def test_submission_does_not_emit_vector_event_when_index_is_disabled():
    original = app.state.container
    app.state.container = _vector_disabled_container()
    try:
        personal = _headers("projection_personal", "personal_user")
        enterprise = _headers("projection_enterprise", "enterprise_user")
        resume_id = _candidate_resume(personal)
        job_id = _enterprise_job(enterprise, "Projection Enterprise")

        assert _submit(personal, job_id, resume_id).status_code == 200

        assert _profile_events() == []
    finally:
        app.state.container = original


def test_cv_update_does_not_emit_vector_event_when_index_is_disabled():
    personal = _headers("projection_update_personal", "personal_user")
    enterprise = _headers("projection_update_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Projection Update Enterprise")
    assert _submit(personal, job_id, resume_id).status_code == 200
    before = len(_profile_events())

    updated = client.put(
        f"/api/v1/resumes/{resume_id}/parse-result",
        headers=personal,
        json={"projects": [{"description": "updated authorized profile"}]},
    )
    assert updated.status_code == 409

    assert len(_profile_events()) == before


def test_submission_revoke_does_not_emit_vector_event_when_index_is_disabled():
    original = app.state.container
    app.state.container = _vector_disabled_container()
    try:
        personal = _headers("projection_revoke_personal", "personal_user")
        enterprise = _headers("projection_revoke_enterprise", "enterprise_user")
        resume_id = _candidate_resume(personal)
        job_id = _enterprise_job(enterprise, "Projection Revoke Enterprise")
        assert _submit(personal, job_id, resume_id).status_code == 200

        revoked = client.put(
            f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions/{resume_id}/revoke",
            headers=personal,
        )
        assert revoked.status_code == 200
        assert _profile_events() == []
    finally:
        app.state.container = original


def test_enterprise_candidate_match_list_detail_and_decision_are_persisted(
    matching_service_fake,
):
    personal = _headers("candidate_personal", "personal_user")
    enterprise = _headers("candidate_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Candidate Enterprise")
    submission_id = _submission_id(personal, job_id, resume_id)

    matched = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id, submission_id]},
    )
    assert matched.status_code == 200
    body = matched.json()["data"]
    assert body["implementation_status"] == "matching_service_async"
    assert body["enterprise_job_id"] == job_id
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["submission_id"] == submission_id
    assert item["resume_id"] == resume_id
    assert item["status"] == "created"
    assert item["task_id"]
    evaluation_id = item["evaluation_id"]
    assert evaluation_id
    with SessionLocal() as session:
        reference = session.query(MatchingServiceReference).one()
        assert reference.task_id == item["task_id"]
        assert reference.evaluation_id == evaluation_id
        assert reference.resume_id == resume_id
        assert reference.position_id == f"enterprise_job:{job_id}"
        assert reference.target_type == "enterprise_job"
        assert reference.graph_version != "enterprise-job.disabled"

    listing = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/match-reports", headers=enterprise
    )
    assert listing.status_code == 200
    listed = listing.json()["data"]
    assert len(listed) == 1
    assert listed[0]["evaluation_id"] == evaluation_id
    assert listed[0]["position_id"] == f"enterprise_job:{job_id}"
    assert "result_type" not in listed[0]

    detail = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/match-reports/{evaluation_id}",
        headers=enterprise,
    )
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["evaluation_id"] == evaluation_id
    assert data["evaluation"]["evaluation_status"] == "completed"
    assert data["evaluation"]["algorithm_version"] == "fake-enterprise-v1"
    assert data["lineage"]["position_id"] == f"enterprise_job:{job_id}"

    fit = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise,
        params={"evaluation_id": evaluation_id},
    )
    unfit = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-unfit",
        headers=enterprise,
        params={"evaluation_id": evaluation_id},
    )
    assert fit.status_code == 200
    assert unfit.status_code == 200
    assert fit.json()["data"]["decision_id"] == unfit.json()["data"]["decision_id"]
    assert unfit.json()["data"]["candidate_status"] == "unfit"
    assert unfit.json()["data"]["evaluation_id"] == evaluation_id
    assert unfit.json()["data"]["task_id"] == item["task_id"]
    assert unfit.json()["data"]["algorithm_version"] == "fake-enterprise-v1"
    assert unfit.json()["data"]["implementation_status"] == "database_persisted_candidate_decision"


def test_enterprise_match_listing_is_job_scoped_without_legacy_reports():
    personal = _headers("candidate_listing_personal", "personal_user")
    enterprise = _headers("candidate_listing_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_a = _enterprise_job(enterprise, "Listing Enterprise A")
    job_b = _enterprise_job(enterprise, "Listing Enterprise B")
    submission_a = _submission_id(personal, job_a, resume_id)
    submission_b = _submission_id(personal, job_b, resume_id)
    for job_id, submission_id in ((job_a, submission_a), (job_b, submission_b)):
        response = client.post(
            f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
            headers=enterprise,
            json={"submission_ids": [submission_id]},
        )
        assert response.json()["data"]["items"][0]["status"] == "created"

    listing_a = client.get(
        f"/api/v1/enterprise-jobs/{job_a}/match-reports", headers=enterprise
    )
    listing_b = client.get(
        f"/api/v1/enterprise-jobs/{job_b}/match-reports", headers=enterprise
    )
    assert len(listing_a.json()["data"]) == 1
    assert len(listing_b.json()["data"]) == 1
    assert listing_a.json()["data"][0]["position_id"] == f"enterprise_job:{job_a}"
    assert listing_b.json()["data"][0]["position_id"] == f"enterprise_job:{job_b}"
    assert all("result_type" not in item for item in listing_a.json()["data"])


def test_enterprise_cannot_access_another_enterprise_candidate_data():
    personal = _headers("candidate_scope_personal", "personal_user")
    owner = _headers("candidate_scope_owner", "enterprise_user")
    other = _headers("candidate_scope_other", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(owner, "Candidate Scope Owner")
    submission_id = _submission_id(personal, job_id, resume_id)
    matched = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=owner,
        json={"submission_ids": [submission_id]},
    ).json()["data"]["items"][0]
    evaluation_id = matched["evaluation_id"]

    responses = [
        client.get(
            f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions",
            headers=other,
        ),
        client.post(
            f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
            headers=other,
            json={"submission_ids": [submission_id]},
        ),
        client.get(
            f"/api/v1/enterprise-jobs/{job_id}/match-reports", headers=other
        ),
        client.get(
            f"/api/v1/enterprise-jobs/{job_id}/match-reports/{evaluation_id}",
            headers=other,
        ),
        client.post(
            f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
            headers=other,
            params={"evaluation_id": evaluation_id},
        ),
    ]

    assert [response.status_code for response in responses] == [403] * len(responses)


def test_enterprise_inline_profiles_match_async_service_contract():
    personal = _headers("candidate_contract_personal", "personal_user")
    enterprise = _headers("candidate_contract_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Contract Enterprise")
    contracts = app.state.container.candidates.contracts
    cv_profile = contracts.cv_profile(resume_id)
    position_profile = contracts.enterprise_job_profile(job_id)
    assert cv_profile is not None
    assert position_profile is not None
    assert position_profile["graph_version"] != "enterprise-job.disabled"

    service_root = (
        Path(__file__).resolve().parents[3]
        / "services"
        / "matching-service"
    )
    probe = (
        "import json,sys;"
        "from app.application.evaluation_tasks import EvaluationTaskService;"
        "p=json.loads(sys.stdin.read());r=EvaluationTaskService._parse_profiles(p);"
        "print(json.dumps({'error':getattr(r,'error_code',None),"
        "'message':getattr(r,'error_message',None),"
        "'cv_valid':isinstance(r,tuple) and r[0].schema_version=='cv-match-profile.v1' "
        "and bool(r[0].source_version),"
        "'position_valid':isinstance(r,tuple) and r[1].schema_version=='position-match-profile.v1' "
        "and bool(r[1].source_version)}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=service_root,
        input=json.dumps(
            {"cv_profile": cv_profile, "position_profile": position_profile},
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {
        "error": None,
        "message": None,
        "cv_valid": True,
        "position_valid": True,
    }


def test_enterprise_candidate_decision_rejects_invalid_remote_evaluations(
    matching_service_fake,
):
    personal = _headers("candidate_decision_personal", "personal_user")
    enterprise = _headers("candidate_decision_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Decision Enterprise")
    submission_id = _submission_id(personal, job_id, resume_id)
    matched = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    ).json()["data"]["items"][0]
    evaluation_id = matched["evaluation_id"]
    endpoint = f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit"

    assert client.post(endpoint, headers=enterprise).status_code == 409
    original = matching_service_fake.evaluations[evaluation_id]
    cases = (
        replace(original, resume_id="another-resume"),
        replace(original, position_id="enterprise_job:another-job"),
        replace(original, stale=True),
        replace(original, evaluation={**original.evaluation, "evaluation_status": "pending"}),
    )
    for invalid in cases:
        matching_service_fake.evaluations[evaluation_id] = invalid
        assert client.post(
            endpoint, headers=enterprise, params={"evaluation_id": evaluation_id}
        ).status_code == 409
    matching_service_fake.evaluations[evaluation_id] = original
    matching_service_fake.unavailable = True
    assert client.post(
        endpoint, headers=enterprise, params={"evaluation_id": evaluation_id}
    ).status_code == 409


def test_enterprise_batch_matching_isolated_failures_have_error_codes(
    matching_service_fake,
):
    personal = _headers("candidate_batch_personal", "personal_user")
    enterprise = _headers("candidate_batch_enterprise", "enterprise_user")
    first = _candidate_resume(personal)
    second = _candidate_resume(personal, text="Python Docker")
    job_id = _enterprise_job(enterprise, "Batch Enterprise")
    first_submission = _submission_id(personal, job_id, first)
    second_submission = _submission_id(personal, job_id, second)
    matching_service_fake.failures[second] = MatchingServiceError(
        "FAKE_REJECTED", "deterministic fake rejection", status_code=422
    )

    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={
            "submission_ids": [
                first_submission,
                second_submission,
                "missing-submission",
            ]
        },
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items[0]["submission_id"] == first_submission
    assert items[0]["status"] == "created"
    assert items[0]["evaluation_id"]
    assert items[1]["submission_id"] == second_submission
    assert items[1]["status"] == "rejected"
    assert items[1]["error_code"] == "FAKE_REJECTED"
    assert items[2] == {
        "submission_id": "missing-submission",
        "resume_id": "",
        "status": "error",
        "task_id": None,
        "evaluation_id": None,
        "error_code": "CANDIDATE_SUBMISSION_NOT_FOUND",
        "error_message": "Candidate submission not found",
    }
    assert matching_service_fake.correlation_ids
    assert all(matching_service_fake.correlation_ids)
    with SessionLocal() as session:
        assert session.query(MatchingServiceReference).count() == 1


def test_enterprise_candidate_matching_uses_raw_job_grant_and_jd_profile_without_custom_weights(
    matching_service_fake,
):
    personal = _headers("candidate_no_weight_personal", "personal_user")
    enterprise = _headers("candidate_no_weight_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "No Weight Enterprise", with_weights=False)
    submission_id = _submission_id(personal, job_id, resume_id)

    preflight = client.get(
        "/api/v1/matches/preflight",
        headers=personal,
        params={
            "resume_id": resume_id,
            "position_id": job_id,
            "target_type": "enterprise_job",
        },
    )
    assert preflight.status_code == 200
    assert preflight.json()["data"]["ready"] is True

    current_container = app.state.container
    app.state.container = replace(
        current_container,
        matching=replace(current_container.matching, service=matching_service_fake),
    )
    try:
        personal_match = client.post(
            "/api/v1/matches/tasks",
            headers={**personal, "Idempotency-Key": f"personal-run:{resume_id}:{job_id}"},
            json={
                "resume_id": resume_id,
                "target_type": "enterprise_job",
                "target_id": job_id,
                "use_enterprise_weights": True,
                "generate_learning_path": True,
            },
        )
    finally:
        app.state.container = current_container
    assert personal_match.status_code == 200
    assert personal_match.json()["data"]["evaluation_id"]

    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["status"] == "created"
    assert item["evaluation_id"]
    assert len(matching_service_fake.tasks) == 2


def test_enterprise_matching_retries_remote_unknown_with_same_idempotency_key(
    matching_service_fake,
):
    personal = _headers("candidate_remote_retry_personal", "personal_user")
    enterprise = _headers("candidate_remote_retry_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Remote Retry Enterprise")
    submission_id = _submission_id(personal, job_id, resume_id)
    matching_service_fake.failures[resume_id] = MatchingServiceError(
        "FAKE_UNAVAILABLE", "deterministic transient failure", status_code=503
    )
    endpoint = f"/api/v1/enterprise-jobs/{job_id}/match-submissions"

    first = client.post(
        endpoint,
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    ).json()["data"]["items"][0]
    assert first["status"] == "reconciling"
    assert first["error_code"] == "FAKE_UNAVAILABLE"
    with SessionLocal() as session:
        intent = session.query(MatchingSubmissionIntent).one()
        assert intent.status == "remote_unknown"
        assert intent.retry_count == 1

    del matching_service_fake.failures[resume_id]
    recovered = client.post(
        endpoint,
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    ).json()["data"]["items"][0]
    assert recovered["status"] == "created"
    assert recovered["task_id"]
    assert recovered["evaluation_id"]
    with SessionLocal() as session:
        assert session.query(MatchingSubmissionIntent).one().status == "reference_saved"
        assert session.query(MatchingServiceReference).count() == 1


def test_enterprise_matching_preserves_and_logs_unknown_persistence_error(
    matching_service_fake, monkeypatch, caplog
):
    personal = _headers("candidate_reference_retry_personal", "personal_user")
    enterprise = _headers("candidate_reference_retry_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal)
    job_id = _enterprise_job(enterprise, "Reference Retry Enterprise")
    submission_id = _submission_id(personal, job_id, resume_id)
    original_upsert = SqlAlchemyMatchingRepository.upsert_service_reference
    attempts = 0

    def fail_first_upsert(repository, record):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("deterministic reference persistence failure")
        return original_upsert(repository, record)

    monkeypatch.setattr(
        SqlAlchemyMatchingRepository,
        "upsert_service_reference",
        fail_first_upsert,
    )
    endpoint = f"/api/v1/enterprise-jobs/{job_id}/match-submissions"
    with pytest.raises(RuntimeError, match="reference persistence failure"):
        client.post(
            endpoint,
            headers=enterprise,
            json={"submission_ids": [submission_id]},
        )
    with SessionLocal() as session:
        assert session.query(MatchingSubmissionIntent).one().status == "intended"
        assert session.query(MatchingServiceReference).count() == 0
    assert attempts == 1
    assert len(matching_service_fake.tasks) == 1
    assert "Unhandled request error" in caplog.text


def test_enterprise_candidate_flow_validates_inputs_profiles_reports_and_ownership():
    personal = _headers("candidate_personal_negative", "personal_user")
    enterprise = _headers("candidate_enterprise_owner", "enterprise_user")
    other_enterprise = _headers("candidate_enterprise_other", "enterprise_user")
    resume_without_profile = client.post(
        "/api/v1/resumes/text", headers=personal, json={"raw_text": "Python"}
    ).json()["data"]["resume_id"]
    job_id = _enterprise_job(enterprise, "Owner Enterprise")
    other_job_id = _enterprise_job(other_enterprise, "Other Enterprise")
    rejected = _submit(personal, other_job_id, resume_without_profile)
    assert rejected.status_code == 422
    own_resume = _candidate_resume(personal)
    other_submission = _submission_id(personal, other_job_id, own_resume)

    assert client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": []},
    ).status_code == 422
    missing = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": ["not-found"]},
    )
    assert missing.status_code == 200
    assert missing.json()["data"]["items"][0]["status"] == "error"
    wrong_job = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [other_submission]},
    )
    assert wrong_job.status_code == 200
    assert wrong_job.json()["data"]["items"][0]["error_code"] == (
        "CANDIDATE_SUBMISSION_NOT_FOUND"
    )
    assert client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_without_profile}/mark-fit",
        headers=enterprise,
    ).status_code == 403
    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}/match-reports",
        headers=other_enterprise,
    ).status_code == 403
    own_submission = _submission_id(personal, job_id, own_resume)
    missing_profile = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [own_submission]},
    )
    assert missing_profile.status_code == 200
    assert missing_profile.json()["data"]["items"][0]["status"] == "created"


def test_candidate_submission_is_job_scoped_revocable_and_owner_controlled():
    owner = _headers("candidate_submission_owner", "personal_user")
    other_personal = _headers("candidate_submission_other", "personal_user")
    enterprise = _headers("candidate_submission_enterprise", "enterprise_user")
    other_enterprise = _headers(
        "candidate_submission_other_enterprise", "enterprise_user"
    )
    resume_id = _candidate_resume(owner)
    job_a = _enterprise_job(enterprise, "Submission Enterprise A")
    job_b = _enterprise_job(enterprise, "Submission Enterprise B")
    other_job = _enterprise_job(other_enterprise, "Submission Other Enterprise")

    unauthorized = client.post(
        f"/api/v1/enterprise-jobs/{job_a}/match-submissions",
        headers=enterprise,
        json={"submission_ids": ["missing-submission"]},
    )
    forged_by_enterprise = _submit(enterprise, job_a, resume_id)
    forged_by_other_personal = _submit(other_personal, job_a, resume_id)
    submitted = _submit(owner, job_a, resume_id)

    assert unauthorized.status_code == 200
    assert unauthorized.json()["data"]["items"][0]["status"] == "error"
    assert forged_by_enterprise.status_code == 403
    assert forged_by_other_personal.status_code == 403
    assert submitted.status_code == 200
    submission_id = submitted.json()["data"]["submission_id"]
    wrong_job = client.post(
        f"/api/v1/enterprise-jobs/{job_b}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    )
    other_tenant = client.post(
        f"/api/v1/enterprise-jobs/{other_job}/match-submissions",
        headers=other_enterprise,
        json={"submission_ids": [submission_id]},
    )
    assert wrong_job.status_code == 200
    assert wrong_job.json()["data"]["items"][0]["status"] == "error"
    assert other_tenant.status_code == 200
    assert other_tenant.json()["data"]["items"][0]["status"] == "error"

    allowed = client.post(
        f"/api/v1/enterprise-jobs/{job_a}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    )
    revoked = client.put(
        f"/api/v1/enterprise-jobs/{job_a}/candidate-submissions/{resume_id}/revoke",
        headers=owner,
    )
    after_revoke = client.post(
        f"/api/v1/enterprise-jobs/{job_a}/match-submissions",
        headers=enterprise,
        json={"submission_ids": [submission_id]},
    )

    assert allowed.status_code == 200
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"
    assert after_revoke.status_code == 200
    assert after_revoke.json()["data"]["items"][0]["status"] == "rejected"
    assert after_revoke.json()["data"]["items"][0]["error_code"] == (
        "CANDIDATE_SUBMISSION_INACTIVE"
    )


def test_personal_application_options_restore_submission_lifecycle_from_backend():
    owner = _headers("application_options_owner", "personal_user")
    other_personal = _headers("application_options_other", "personal_user")
    enterprise = _headers("application_options_enterprise", "enterprise_user")
    eligible_resume = _candidate_resume(owner)
    other_resume = _candidate_resume(other_personal)
    ineligible_resume = client.post(
        "/api/v1/resumes/text",
        headers=owner,
        json={"raw_text": "Snapshot is intentionally absent"},
    ).json()["data"]["resume_id"]
    job_id = _enterprise_job(enterprise, "Application Options Enterprise")

    initial = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submission-options",
        headers=owner,
    )
    assert initial.status_code == 200
    options = {item["resume_id"]: item for item in initial.json()["data"]}
    assert set(options) == {eligible_resume, ineligible_resume}
    assert other_resume not in options
    assert options[eligible_resume]["eligible"] is True
    assert options[eligible_resume]["submission"] is None
    assert options[ineligible_resume]["eligible"] is False
    assert options[ineligible_resume]["eligibility_reason"] == (
        "validated_cv_snapshot_missing"
    )
    assert _submit(owner, job_id, ineligible_resume).status_code == 422

    submitted = _submit(owner, job_id, eligible_resume)
    assert submitted.status_code == 200
    enterprise_visible = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions",
        headers=enterprise,
    )
    assert enterprise_visible.status_code == 200
    assert enterprise_visible.json()["data"][0]["resume_id"] == eligible_resume

    restored = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submission-options",
        headers=owner,
    )
    restored_option = next(
        item for item in restored.json()["data"] if item["resume_id"] == eligible_resume
    )
    assert restored_option["submission"]["status"] == "submitted"
    assert set(restored_option["submission"]) == {
        "submission_id",
        "resume_id",
        "status",
        "created_at",
        "updated_at",
    }

    assert client.put(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions/{eligible_resume}/revoke",
        headers=owner,
    ).status_code == 200
    after_refresh = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submission-options",
        headers=owner,
    )
    refreshed_option = next(
        item
        for item in after_refresh.json()["data"]
        if item["resume_id"] == eligible_resume
    )
    assert refreshed_option["submission"]["status"] == "revoked"


def test_application_options_require_personal_actor_and_published_job():
    owner = _headers("application_scope_owner", "personal_user")
    enterprise = _headers("application_scope_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise, "Application Scope Enterprise")

    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submission-options",
        headers=enterprise,
    ).status_code == 403
    assert client.put(
        f"/api/v1/enterprise-jobs/{job_id}/pause", headers=enterprise
    ).status_code == 200
    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submission-options",
        headers=owner,
    ).status_code == 404
