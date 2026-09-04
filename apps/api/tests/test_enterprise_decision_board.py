from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, SessionLocal
from app.main import app
from app.contexts.matching_learning import (
    MatchingContractUnavailable,
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
from tests.user_factory import create_internal_user


client = TestClient(app)


class FakeMatchingService:
    def __init__(self) -> None:
        self.tasks: dict[str, RemoteTask] = {}
        self.tasks_by_idempotency_key: dict[str, RemoteTask] = {}
        self.evaluations: dict[str, RemoteEvaluation] = {}
        self.failures: dict[str, MatchingServiceError] = {}

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
        assert target_type == "enterprise_job"
        assert use_enterprise_weights is True
        assert generate_learning_path is True
        if cv_id in self.failures:
            raise self.failures[cv_id]
        existing = self.tasks_by_idempotency_key.get(idempotency_key)
        if existing is not None:
            return replace(existing, created=False)
        sequence = len(self.tasks) + 1
        task_id = f"board-task-{sequence}"
        evaluation_id = f"board-evaluation-{sequence}"
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
            raw={"provider": "matching-service", "algorithm_version": "fake-enterprise-v1"},
            target_type=target_type,
        )
        self.tasks[task_id] = task
        self.tasks_by_idempotency_key[idempotency_key] = task
        self.evaluations[evaluation_id] = default_evaluation(
            evaluation_id=evaluation_id,
            task_id=task_id,
            resume_id=cv_id,
            position_id=position_id,
            identity=identity,
            cv_profile=cv_profile,
        )
        return task

    def get_evaluation(
        self, identity, evaluation_id: str, *, correlation_id: str
    ) -> RemoteEvaluation:
        evaluation = self.evaluations.get(evaluation_id)
        if evaluation is None:
            raise MatchingServiceError(
                "EVALUATION_NOT_FOUND", "matching evaluation is unavailable", status_code=404
            )
        return evaluation

    def get_task(self, identity, task_id: str, *, correlation_id: str) -> RemoteTask:
        return self.tasks.get(task_id) or RemoteTask(
            task_id=task_id,
            status="failed",
            evaluation_id=None,
            created=False,
            error_code=None,
            error_message=None,
            attempt=1,
            created_at=None,
            updated_at=None,
            raw={},
            target_type="enterprise_job",
        )


def default_evaluation(
    *,
    evaluation_id: str,
    task_id: str,
    resume_id: str,
    position_id: str,
    identity,
    cv_profile: dict[str, object] | None,
) -> RemoteEvaluation:
    return RemoteEvaluation(
        evaluation_id=evaluation_id,
        task_id=task_id,
        stale=False,
        evaluation={
            "evaluation_status": "completed",
            "algorithm_version": "fake-enterprise-v1",
        },
        gap_analysis={},
        versions={},
        created_at=None,
        updated_at=None,
        user_id=identity.subject_id,
        resume_id=resume_id,
        validated_cv_snapshot_id=(
            str(cv_profile["verification_snapshot_id"]) if cv_profile else None
        ),
        position_id=position_id,
    )


def rich_evaluation(
    *,
    evaluation_id: str,
    task_id: str,
    resume_id: str,
    position_id: str,
    user_id: str,
    score: float = 80.0,
    matched: int = 6,
    missing: int = 2,
    critical_gaps: int = 1,
    skills: list[dict[str, object]] | None = None,
    strengths: list[dict[str, object]] | None = None,
    gaps: list[dict[str, object]] | None = None,
) -> RemoteEvaluation:
    total = matched + missing
    coverage = round(matched / total, 4) if total else None
    skill_results = skills if skills is not None else [
        {
            "requirement_id": "req-python",
            "skill_id": "skill_python",
            "skill_name": "Python",
            "importance_level": "required",
            "match_status": "matched",
            "reason_code": "exact",
            "confidence": 0.95,
        },
        {
            "requirement_id": "req-docker",
            "skill_id": "skill_docker",
            "skill_name": "Docker",
            "importance_level": "required",
            "match_status": "missing",
            "reason_code": "missing",
            "confidence": 0.1,
        },
    ]
    prioritized_gaps = []
    for index in range(critical_gaps):
        prioritized_gaps.append(
            {
                "gap_type": "required_skill_missing",
                "requirement_id": f"req-gap-{index}",
                "skill_id": f"skill_gap_{index}",
                "priority": "critical",
                "priority_score": 90.0 - index,
                "reason_codes": ["required_skill_missing"],
                "evidence": [],
            }
        )
    return RemoteEvaluation(
        evaluation_id=evaluation_id,
        task_id=task_id,
        stale=False,
        evaluation={
            "evaluation_status": "completed",
            "algorithm_version": "fake-enterprise-v1",
            "required_skill_coverage": coverage,
            "summary": {
                "hard_constraint_pass_count": 0,
                "hard_constraint_fail_count": 0,
                "required_skill_matched_count": matched,
                "required_skill_missing_count": missing,
                "bonus_skill_matched_count": 0,
                "bonus_skill_missing_count": 0,
                "coverage_denominator_policy": "exclude_unknown_unresolved_and_not_required",
            },
            "skill_results": skill_results,
            "hard_constraint_results": [],
            "responsibility_results": [],
            "project_results": [],
            "scenario_results": [],
            "final_match_result": {
                "overall_score": score,
                "match_confidence": 0.9,
                "recommendation_level": (
                    "strong_match" if score >= 80 else "potential_match"
                ),
                "hard_gate_status": "passed",
                "dimension_scores": [],
                "score_contributions": [
                    {
                        "dimension": "required_skills",
                        "result_id": "req-python",
                        "status": "scored",
                        "reason_code": "exact",
                        "score_value": 1.0,
                        "effective_weight": 0.7,
                        "weighted_points": 70.0,
                        "confidence": 0.95,
                        "candidate_evidence": [
                            {
                                "source_object_type": "validated_cv_snapshot",
                                "source_object_id": "snapshot-1",
                                "source_document_id": "doc-1",
                                "source_fragment_id": "frag-1",
                                "quote": "5 years Python",
                                "start": 0,
                                "end": 14,
                                "alignment": "exact",
                                "result_reference": "ref-1",
                            }
                        ],
                        "position_evidence": [],
                    }
                ],
                "strengths": strengths if strengths is not None else [
                    {
                        "dimension": "required_skills",
                        "result_id": "req-python",
                        "reason_code": "exact",
                        "message": "Python 经验与岗位必备技能完全匹配",
                        "evidence": [],
                    }
                ],
                "gaps": gaps if gaps is not None else [
                    {
                        "dimension": "required_skills",
                        "result_id": "req-docker",
                        "reason_code": "missing",
                        "message": "缺少 Docker 必备技能",
                        "evidence": [],
                    }
                ],
                "uncertain_items": [],
                "explanation": "基于企业权重与能力图谱的正式匹配结果",
                "algorithm_version": "fake-scoring-v1",
                "scoring_config_version": "fake-config-v1",
                "input_evaluation_algorithm_version": "fake-enterprise-v1",
                "cv_taxonomy_version": "cv-tax",
                "cv_derivation_version": "cv-deriv",
                "position_taxonomy_version": "pos-tax",
                "position_graph_version": "graph-v1",
                "position_quality_snapshot_id": "quality-1",
            },
        },
        gap_analysis={
            "generation_status": "completed",
            "result_status": "completed",
            "prioritized_gaps": prioritized_gaps,
            "learning_path": [],
            "counterfactual_suggestions": [],
        },
        versions={},
        created_at=None,
        updated_at=None,
        user_id=user_id,
        resume_id=resume_id,
        position_id=position_id,
    )


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


def _headers(username: str, role: str) -> tuple[dict, str]:
    user_id = create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={"role": role, "username": username, "password": "password123"},
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}, user_id


def _ensure_validated_cv_snapshot(resume_id: str) -> None:
    with SessionLocal() as session:
        resume = session.get(Resume, resume_id)
        source = SourceCV(
            id=str(uuid4()),
            owner_id=resume.user_id,
            source_platform="test-decision-board",
            source_record_id=f"test:{resume_id}:{uuid4()}",
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
            request_id="test:decision-board:" + str(uuid4()),
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


def _candidate_resume(personal_headers: dict, text: str = "Python FastAPI Docker") -> str:
    resume_id = client.post(
        "/api/v1/resumes/text", headers=personal_headers, json={"raw_text": text}
    ).json()["data"]["resume_id"]
    assert (
        client.post(f"/api/v1/resumes/{resume_id}/parse", headers=personal_headers).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/resumes/{resume_id}/parse-result/confirm",
            headers=personal_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/resumes/{resume_id}/skill-profile", headers=personal_headers
        ).status_code
        == 200
    )
    _ensure_validated_cv_snapshot(resume_id)
    return resume_id


def _enterprise_job(enterprise_headers: dict, name: str) -> str:
    enterprise_id = client.post(
        "/api/v1/enterprises",
        headers=enterprise_headers,
        json={"enterprise_name": name},
    ).json()["data"]["enterprise_id"]
    job_id = client.post(
        "/api/v1/enterprise-jobs",
        headers=enterprise_headers,
        json={"enterprise_id": enterprise_id, "title": "Python Engineer"},
    ).json()["data"]["enterprise_job_id"]
    weights = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
        headers=enterprise_headers,
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


def _submit(personal_headers: dict, job_id: str, resume_id: str) -> str:
    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions",
        headers=personal_headers,
        json={"resume_id": resume_id},
    )
    assert response.status_code == 200
    return response.json()["data"]["submission_id"]


def _match(enterprise_headers: dict, job_id: str, submission_id: str) -> dict:
    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise_headers,
        json={"submission_ids": [submission_id]},
    )
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["status"] == "created"
    return item


def _board(enterprise_headers: dict, job_id: str) -> dict:
    response = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-decision-board",
        headers=enterprise_headers,
    )
    assert response.status_code == 200
    return response.json()["data"]


def _register_evaluation(
    fake: FakeMatchingService,
    *,
    evaluation_id: str,
    task_id: str,
    resume_id: str,
    position_id: str,
    user_id: str,
    **kwargs,
) -> None:
    fake.evaluations[evaluation_id] = rich_evaluation(
        evaluation_id=evaluation_id,
        task_id=task_id,
        resume_id=resume_id,
        position_id=position_id,
        user_id=user_id,
        **kwargs,
    )


def _add_reference(
    *,
    task_id: str,
    evaluation_id: str,
    resume_id: str,
    job_id: str,
    user_id: str,
    status: str,
    created_at: datetime | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(
            MatchingServiceReference(
                task_id=task_id,
                evaluation_id=evaluation_id,
                user_id=user_id,
                tenant_id="tenant-board",
                resume_id=resume_id,
                position_id=f"enterprise_job:{job_id}",
                provider="matching-service",
                target_type="enterprise_job",
                status=status,
                idempotency_key=f"idem:{task_id}",
                created_at=created_at or datetime.now(timezone.utc),
            )
        )
        session.commit()


def test_board_empty_candidate_pool():
    enterprise_headers, _ = _headers("board_empty_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Empty")
    data = _board(enterprise_headers, job_id)
    assert data["enterprise_job_id"] == job_id
    assert data["total"] == 0
    assert data["ranked_count"] == 0
    assert data["items"] == []


def test_board_single_candidate_shows_formal_score(
    matching_service_fake,
):
    personal_headers, _ = _headers("board_single_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_single_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Single")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=86.4,
        matched=8,
        missing=1,
        critical_gaps=1,
    )

    data = _board(enterprise_headers, job_id)
    assert data["total"] == 1
    assert data["ranked_count"] == 1
    item = data["items"][0]
    assert item["rank"] == 1
    assert item["resume_id"] == resume_id
    assert item["candidate_status"] == "submitted"
    assert item["evaluation_status"] == "succeeded"
    assert item["evaluation_id"] == matched["evaluation_id"]
    assert item["overall_score"] == 86.4
    assert item["match_confidence"] == 0.9
    assert item["recommendation_level"] == "strong_match"
    assert item["required_coverage"]["matched"] == 8
    assert item["required_coverage"]["total"] == 9
    assert item["required_coverage"]["coverage"] == pytest.approx(8 / 9, abs=0.001)
    assert item["critical_gap_count"] == 1
    assert len(item["critical_gaps"]) == 1
    assert item["evidence"]["count"] == 1
    assert item["evidence"]["samples"] == ["5 years Python"]
    assert any(strength["message"] == "Python 经验与岗位必备技能完全匹配" for strength in item["strengths"])
    assert any(risk["kind"] == "gap" for risk in item["risks"])
    assert any(risk["kind"] == "missing_required" for risk in item["risks"])


def test_board_multiple_candidates_ranked_by_score(matching_service_fake):
    personal_headers, _ = _headers("board_multi_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_multi_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Multi")
    resume_low = _candidate_resume(personal_headers, text="Python basic")
    resume_high = _candidate_resume(personal_headers, text="Python FastAPI Docker advanced")
    for resume_id, score in ((resume_low, 72.5), (resume_high, 91.0)):
        submission_id = _submit(personal_headers, job_id, resume_id)
        matched = _match(enterprise_headers, job_id, submission_id)
        _register_evaluation(
            matching_service_fake,
            evaluation_id=matched["evaluation_id"],
            task_id=matched["task_id"],
            resume_id=resume_id,
            position_id=f"enterprise_job:{job_id}",
            user_id=user_id,
            score=score,
        )

    data = _board(enterprise_headers, job_id)
    items = data["items"]
    assert [item["resume_id"] for item in items] == [resume_high, resume_low]
    assert [item["rank"] for item in items] == [1, 2]
    assert [item["overall_score"] for item in items] == [91.0, 72.5]


def test_board_ranking_coverage_gaps_and_deterministic_tie_break(matching_service_fake):
    personal_headers, _ = _headers("board_tie_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_tie_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Tie")
    # Same score: higher coverage wins.
    resume_low_cov = _candidate_resume(personal_headers, text="Python A")
    resume_high_cov = _candidate_resume(personal_headers, text="Python B")
    for resume_id, matched_count in ((resume_low_cov, 6), (resume_high_cov, 8)):
        submission_id = _submit(personal_headers, job_id, resume_id)
        matched = _match(enterprise_headers, job_id, submission_id)
        _register_evaluation(
            matching_service_fake,
            evaluation_id=matched["evaluation_id"],
            task_id=matched["task_id"],
            resume_id=resume_id,
            position_id=f"enterprise_job:{job_id}",
            user_id=user_id,
            score=80.0,
            matched=matched_count,
            missing=2,
        )
    data = _board(enterprise_headers, job_id)
    assert [item["resume_id"] for item in data["items"]] == [resume_high_cov, resume_low_cov]

    # Same score + same coverage: fewer critical gaps wins.
    resume_many_gaps = _candidate_resume(personal_headers, text="Python C")
    submission_id = _submit(personal_headers, job_id, resume_many_gaps)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_many_gaps,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=80.0,
        matched=8,
        missing=2,
        critical_gaps=3,
    )
    data = _board(enterprise_headers, job_id)
    assert [item["resume_id"] for item in data["items"]] == [
        resume_high_cov,
        resume_many_gaps,
        resume_low_cov,
    ]

    # Fully tied candidates fall back to a deterministic order.
    resume_dup = _candidate_resume(personal_headers, text="Python D")
    submission_id = _submit(personal_headers, job_id, resume_dup)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_dup,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=80.0,
        matched=8,
        missing=2,
        critical_gaps=3,
    )
    first = _board(enterprise_headers, job_id)["items"]
    second = _board(enterprise_headers, job_id)["items"]
    assert [item["rank"] for item in first] == [1, 2, 3, 4]
    assert first == second


def test_board_picks_latest_succeeded_evaluation(matching_service_fake):
    personal_headers, _ = _headers("board_latest_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_latest_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Latest")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)

    older_id = matched["evaluation_id"]
    older_task = matched["task_id"]
    _register_evaluation(
        matching_service_fake,
        evaluation_id=older_id,
        task_id=older_task,
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=55.0,
    )
    newer_id = "board-evaluation-newer"
    newer_task = "board-task-newer"
    _register_evaluation(
        matching_service_fake,
        evaluation_id=newer_id,
        task_id=newer_task,
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=92.0,
    )
    now = datetime.now(timezone.utc)
    _add_reference(
        task_id=newer_task,
        evaluation_id=newer_id,
        resume_id=resume_id,
        job_id=job_id,
        user_id=user_id,
        status="succeeded",
        created_at=now,
    )
    # Force the older reference to an earlier created_at for a deterministic sort.
    with SessionLocal() as session:
        row = session.query(MatchingServiceReference).filter(
            MatchingServiceReference.evaluation_id == older_id
        ).one()
        row.created_at = now - timedelta(hours=2)
        session.commit()

    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["evaluation_id"] == newer_id
    assert item["evaluation_status"] == "succeeded"
    assert item["overall_score"] == 92.0


@pytest.mark.parametrize("latest_status", ["pending", "running", "failed"])
def test_board_latest_incomplete_never_falls_back_to_older_succeeded(
    matching_service_fake, latest_status
):
    personal_headers, _ = _headers(
        f"board_no_fallback_personal_{latest_status}", "personal_user"
    )
    enterprise_headers, user_id = _headers(
        f"board_no_fallback_enterprise_{latest_status}", "enterprise_user"
    )
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, f"Board No Fallback {latest_status}")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=96.0,
    )
    with SessionLocal() as session:
        older = session.query(MatchingServiceReference).filter(
            MatchingServiceReference.evaluation_id == matched["evaluation_id"]
        ).one()
        older.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()
    latest_evaluation_id = f"latest-{latest_status}-evaluation"
    _add_reference(
        task_id=f"latest-{latest_status}-task",
        evaluation_id=latest_evaluation_id,
        resume_id=resume_id,
        job_id=job_id,
        user_id=user_id,
        status=latest_status,
    )

    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["evaluation_id"] == latest_evaluation_id
    assert item["evaluation_status"] == latest_status
    assert item["overall_score"] is None
    assert item["rank"] is None

    decision = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert decision.status_code == 409
    latest_decision = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": latest_evaluation_id},
    )
    assert latest_decision.status_code == 409


def test_board_stale_evaluation_is_not_ranked_or_decidable(matching_service_fake):
    personal_headers, _ = _headers("board_stale_personal", "personal_user")
    enterprise_headers, _ = _headers("board_stale_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Stale")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    current_decision = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert current_decision.status_code == 200
    original = matching_service_fake.evaluations[matched["evaluation_id"]]
    matching_service_fake.evaluations[matched["evaluation_id"]] = replace(
        original, stale=True, stale_reason_codes=("INPUT_VERSION_CHANGED",)
    )

    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["evaluation_status"] == "stale"
    assert item["stale"] is True
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert item["decision"] is None

    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert response.status_code == 409


@pytest.mark.parametrize("changed_identity", ["cv", "job_profile"])
def test_board_identity_change_requires_rematch_and_blocks_decision(
    matching_service_fake, changed_identity
):
    personal_headers, _ = _headers(
        f"board_identity_personal_{changed_identity}", "personal_user"
    )
    enterprise_headers, _ = _headers(
        f"board_identity_enterprise_{changed_identity}", "enterprise_user"
    )
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, f"Board Identity {changed_identity}")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)

    if changed_identity == "cv":
        _ensure_validated_cv_snapshot(resume_id)
    else:
        with SessionLocal() as session:
            job = session.get(EnterpriseJob, job_id)
            job.title = "Changed Current Job Profile"
            session.commit()

    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["evaluation_status"] == "needs_rematch"
    assert item["stale"] is False
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert item["error_code"] == (
        "CV_STALE" if changed_identity == "cv" else "CONTRACT_INCOMPATIBLE"
    )
    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert response.status_code == 409


def test_board_degrades_kg_outage_to_needs_rematch_error(matching_service_fake):
    personal_headers, _ = _headers("board_kg_error_personal", "personal_user")
    enterprise_headers, _ = _headers("board_kg_error_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board KG Error")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)

    class UnavailableContracts:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def enterprise_job_profile(self, requested_job_id):
            raise MatchingContractUnavailable("kg transport unavailable")

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    original = app.state.container
    app.state.container = replace(
        original,
        candidates=replace(
            original.candidates,
            contracts=UnavailableContracts(original.candidates.contracts),
        ),
    )
    try:
        data = _board(enterprise_headers, job_id)
        decision = client.post(
            f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
            headers=enterprise_headers,
            params={"evaluation_id": matched["evaluation_id"]},
        )
    finally:
        app.state.container = original

    item = data["items"][0]
    assert item["evaluation_status"] == "needs_rematch"
    assert item["error_code"] == "KG_UNAVAILABLE"
    assert item["error_message"] == "kg transport unavailable"
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert decision.status_code == 409


def test_board_never_matched_pending_running_and_failed(matching_service_fake):
    personal_headers, _ = _headers("board_status_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_status_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Status")

    never_resume = _candidate_resume(personal_headers, text="Python never")
    _submit(personal_headers, job_id, never_resume)

    for label, status, text in (
        ("pending", "pending", "Python pending"),
        ("running", "running", "Python running"),
        ("failed", "failed", "Python failed"),
    ):
        resume_id = _candidate_resume(personal_headers, text=text)
        submission_id = _submit(personal_headers, job_id, resume_id)
        _match(enterprise_headers, job_id, submission_id)
        with SessionLocal() as session:
            reference = (
                session.query(MatchingServiceReference)
                .filter(MatchingServiceReference.resume_id == resume_id)
                .one()
            )
            reference.status = status
            session.commit()

    data = _board(enterprise_headers, job_id)
    by_resume = {item["resume_id"]: item for item in data["items"]}
    assert by_resume[never_resume]["evaluation_status"] == "never_matched"
    assert by_resume[never_resume]["overall_score"] is None
    # Non-succeeded evaluations never participate in formal ranking.
    assert all(item["rank"] is None for item in data["items"])
    assert data["ranked_count"] == 0
    statuses = [item["evaluation_status"] for item in data["items"]]
    assert statuses.count("never_matched") == 1
    assert statuses.count("pending") == 1
    assert statuses.count("running") == 1
    assert statuses.count("failed") == 1
    pending_item = next(item for item in data["items"] if item["evaluation_status"] == "pending")
    running_item = next(item for item in data["items"] if item["evaluation_status"] == "running")
    failed_item = next(item for item in data["items"] if item["evaluation_status"] == "failed")
    assert pending_item["overall_score"] is None
    assert running_item["overall_score"] is None
    assert failed_item["overall_score"] is None


def test_board_revoked_submission_is_not_ranked(matching_service_fake):
    personal_headers, _ = _headers("board_revoke_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_revoke_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Revoke")
    active_resume = _candidate_resume(personal_headers, text="Python active")
    revoked_resume = _candidate_resume(personal_headers, text="Python revoked")
    for resume_id in (active_resume, revoked_resume):
        submission_id = _submit(personal_headers, job_id, resume_id)
        matched = _match(enterprise_headers, job_id, submission_id)
        _register_evaluation(
            matching_service_fake,
            evaluation_id=matched["evaluation_id"],
            task_id=matched["task_id"],
            resume_id=resume_id,
            position_id=f"enterprise_job:{job_id}",
            user_id=user_id,
            score=88.0,
        )
    client.put(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-submissions/{revoked_resume}/revoke",
        headers=personal_headers,
    )

    data = _board(enterprise_headers, job_id)
    revoked_item = next(item for item in data["items"] if item["resume_id"] == revoked_resume)
    active_item = next(item for item in data["items"] if item["resume_id"] == active_resume)
    assert revoked_item["candidate_status"] == "revoked"
    assert revoked_item["rank"] is None
    assert active_item["rank"] == 1
    assert data["ranked_count"] == 1


def test_board_shows_fit_and_unfit_decisions(matching_service_fake):
    personal_headers, _ = _headers("board_decision_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_decision_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Decision")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=85.0,
    )

    fit = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert fit.status_code == 200
    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["decision"]["decision"] == "fit"
    assert item["decision"]["evaluation_id"] == matched["evaluation_id"]

    unfit = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-unfit",
        headers=enterprise_headers,
        params={"evaluation_id": matched["evaluation_id"]},
    )
    assert unfit.status_code == 200
    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["decision"]["decision"] == "unfit"


def test_board_unauthorized_role_rejected():
    personal_headers, _ = _headers("board_unauth_personal", "personal_user")
    enterprise_headers, _ = _headers("board_unauth_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise_headers, "Board Unauthorized")
    response = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-decision-board",
        headers=personal_headers,
    )
    assert response.status_code == 403


def test_board_cross_enterprise_isolation():
    owner_headers, _ = _headers("board_owner", "enterprise_user")
    other_headers, _ = _headers("board_other", "enterprise_user")
    job_id = _enterprise_job(owner_headers, "Board Owner")
    response = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/candidate-decision-board",
        headers=other_headers,
    )
    assert response.status_code == 403


def test_board_tolerates_missing_optional_report_fields(matching_service_fake):
    personal_headers, _ = _headers("board_optional_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_optional_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Optional")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    # Evaluation with only the bare contract: no summary, no final result, no gaps.
    matching_service_fake.evaluations[matched["evaluation_id"]] = RemoteEvaluation(
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        stale=False,
        evaluation={
            "evaluation_status": "completed",
            "algorithm_version": "fake-enterprise-v1",
        },
        gap_analysis={},
        versions={},
        created_at=None,
        updated_at=None,
        user_id=user_id,
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
    )
    data = _board(enterprise_headers, job_id)
    item = data["items"][0]
    assert item["evaluation_status"] == "succeeded"
    assert item["overall_score"] is None
    assert item["required_coverage"] is None
    assert item["critical_gap_count"] == 0
    assert item["critical_gaps"] == []
    assert item["strengths"] == []
    assert item["risks"] == []
    assert item["rank"] == 1


def test_board_succeeded_reference_with_failed_remote_shows_no_score(matching_service_fake):
    personal_headers, _ = _headers("board_demote_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_demote_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Demote")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    # Reference says succeeded (stored at task creation), but the remote
    # evaluation is actually failed: the board must not show a formal score.
    matching_service_fake.evaluations[matched["evaluation_id"]] = RemoteEvaluation(
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        stale=False,
        evaluation={
            "evaluation_status": "failed",
            "algorithm_version": "fake-enterprise-v1",
            "error_code": "REMOTE_FAILED",
            "final_match_result": {
                "overall_score": 99.0,
                "match_confidence": 0.9,
                "recommendation_level": "strong_match",
                "hard_gate_status": "passed",
                "dimension_scores": [],
                "score_contributions": [],
                "strengths": [],
                "gaps": [],
                "uncertain_items": [],
                "explanation": "stale partial result that must not be surfaced",
                "algorithm_version": "fake-scoring-v1",
                "scoring_config_version": "fake-config-v1",
                "input_evaluation_algorithm_version": "fake-enterprise-v1",
                "cv_taxonomy_version": "cv",
                "cv_derivation_version": "cv",
                "position_taxonomy_version": "pos",
                "position_graph_version": "graph",
                "position_quality_snapshot_id": "quality",
            },
        },
        gap_analysis={},
        versions={},
        created_at=None,
        updated_at=None,
        user_id=user_id,
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
    )
    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["evaluation_status"] == "failed"
    assert item["overall_score"] is None
    assert item["strengths"] == []
    assert item["risks"] == []
    assert item["error_code"] == "REMOTE_REJECTED"
    assert item["error_message"]


@pytest.mark.parametrize(
    ("raw_code", "stable_code"),
    [
        ("CV_PROFILE_NOT_FOUND", "CV_STALE"),
        ("POSITION_PROFILE_NOT_FOUND", "POSITION_PROFILE_MISSING"),
        ("knowledge_graph_503", "KG_UNAVAILABLE"),
        ("MATCHING_SERVICE_TIMEOUT", "MATCHING_TIMEOUT"),
        ("UPSTREAM_CONTRACT_INCOMPATIBLE", "CONTRACT_INCOMPATIBLE"),
        ("MATCHING_TASK_REJECTED", "REMOTE_REJECTED"),
    ],
)
def test_board_maps_failed_task_error_to_stable_code(
    matching_service_fake, raw_code, stable_code
):
    personal_headers, _ = _headers(f"board_error_{stable_code}_personal", "personal_user")
    enterprise_headers, _ = _headers(
        f"board_error_{stable_code}_enterprise", "enterprise_user"
    )
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, f"Board Error {stable_code}")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    matching_service_fake.tasks[matched["task_id"]] = replace(
        matching_service_fake.tasks[matched["task_id"]],
        status="failed",
        error_code=raw_code,
        error_message=f"real upstream error: {raw_code}",
    )
    with SessionLocal() as session:
        reference = session.query(MatchingServiceReference).filter(
            MatchingServiceReference.task_id == matched["task_id"]
        ).one()
        reference.status = "failed"
        session.commit()

    data = _board(enterprise_headers, job_id)
    item = data["items"][0]
    assert item["evaluation_status"] == "failed"
    assert item["error_code"] == stable_code
    assert item["error_message"] == f"real upstream error: {raw_code}"
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert data["ranked_count"] == 0


def test_board_uses_reference_intent_error_when_failed_task_has_no_error(
    matching_service_fake,
):
    personal_headers, _ = _headers("board_reference_error_personal", "personal_user")
    enterprise_headers, _ = _headers("board_reference_error_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Reference Error")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    matching_service_fake.tasks[matched["task_id"]] = replace(
        matching_service_fake.tasks[matched["task_id"]], status="failed"
    )
    with SessionLocal() as session:
        reference = session.query(MatchingServiceReference).filter(
            MatchingServiceReference.task_id == matched["task_id"]
        ).one()
        reference.status = "failed"
        intent = session.query(MatchingSubmissionIntent).filter(
            MatchingSubmissionIntent.idempotency_key == reference.idempotency_key
        ).one()
        intent.last_error_code = "MATCHING_SERVICE_TIMEOUT"
        session.commit()

    item = _board(enterprise_headers, job_id)["items"][0]
    assert item["error_code"] == "MATCHING_TIMEOUT"
    assert item["error_message"] == "匹配服务响应超时，请稍后重试。"


def test_board_surfaces_rejected_intent_without_service_reference(
    matching_service_fake,
):
    personal_headers, _ = _headers("board_orphan_intent_personal", "personal_user")
    enterprise_headers, _ = _headers(
        "board_orphan_intent_enterprise", "enterprise_user"
    )
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Orphan Intent")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matching_service_fake.failures[resume_id] = MatchingServiceError(
        "POSITION_PROFILE_NOT_FOUND",
        "matching rejected missing position profile",
        status_code=422,
    )

    response = client.post(
        f"/api/v1/enterprise-jobs/{job_id}/match-submissions",
        headers=enterprise_headers,
        json={"submission_ids": [submission_id]},
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["status"] == "rejected"
    with SessionLocal() as session:
        assert session.query(MatchingServiceReference).count() == 0
        intent = session.query(MatchingSubmissionIntent).one()
        assert intent.status == "rejected"
        assert intent.last_error_code == "POSITION_PROFILE_NOT_FOUND"

    data = _board(enterprise_headers, job_id)
    item = data["items"][0]
    assert item["evaluation_status"] == "failed"
    assert item["error_code"] == "POSITION_PROFILE_MISSING"
    assert item["error_message"] == "当前岗位画像不可用，请先完成岗位画像发布。"
    assert item["evaluation_id"] is None
    assert item["task_id"] is None
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert item["decision"] is None
    assert data["ranked_count"] == 0


@pytest.mark.parametrize(
    ("remote_status", "expected_status"),
    [
        ("", "failed"),
        ("cancelled", "failed"),
        ("unknown_vendor_status", "failed"),
        ("stale", "stale"),
        ("needs_rematch", "needs_rematch"),
    ],
)
def test_board_normalizes_remote_status_to_closed_decision_board_states(
    matching_service_fake, remote_status, expected_status
):
    suffix = remote_status or "empty"
    personal_headers, _ = _headers(f"board_remote_{suffix}_personal", "personal_user")
    enterprise_headers, _ = _headers(
        f"board_remote_{suffix}_enterprise", "enterprise_user"
    )
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, f"Board Remote {suffix}")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    original = matching_service_fake.evaluations[matched["evaluation_id"]]
    matching_service_fake.evaluations[matched["evaluation_id"]] = replace(
        original,
        evaluation={
            **original.evaluation,
            "evaluation_status": remote_status,
            "final_match_result": {"overall_score": 99.9},
        },
    )

    data = _board(enterprise_headers, job_id)
    item = data["items"][0]
    assert item["evaluation_status"] == expected_status
    assert item["overall_score"] is None
    assert item["rank"] is None
    assert data["ranked_count"] == 0


def test_board_does_not_change_formal_match_score(matching_service_fake):
    personal_headers, _ = _headers("board_score_personal", "personal_user")
    enterprise_headers, user_id = _headers("board_score_enterprise", "enterprise_user")
    resume_id = _candidate_resume(personal_headers)
    job_id = _enterprise_job(enterprise_headers, "Board Score")
    submission_id = _submit(personal_headers, job_id, resume_id)
    matched = _match(enterprise_headers, job_id, submission_id)
    _register_evaluation(
        matching_service_fake,
        evaluation_id=matched["evaluation_id"],
        task_id=matched["task_id"],
        resume_id=resume_id,
        position_id=f"enterprise_job:{job_id}",
        user_id=user_id,
        score=77.7,
        matched=6,
        missing=3,
        critical_gaps=2,
    )

    board_item = _board(enterprise_headers, job_id)["items"][0]
    detail = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/match-reports/{matched['evaluation_id']}",
        headers=enterprise_headers,
    ).json()["data"]
    formal_score = detail["evaluation"]["final_match_result"]["overall_score"]
    assert board_item["overall_score"] == formal_score == 77.7
