from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.contexts.cv_ingestion import (
    CVExtractionNotFound,
    CVReviewConflict,
)
from app.contexts.cv_ingestion.domain import (
    CVConfirmationResult,
    CVExtractionTaskRecord,
    CVReviewResult,
    SourceCVImportResult,
    ValidatedCVSnapshotRecord,
)
from app.domain.json_types import freeze_json_object
from app.main import app
from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _headers(username: str = "cv_contract_user") -> dict[str, str]:
    create_internal_user(username, "personal_user")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "personal_user",
            "username": username,
            "password": "password123",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _snapshot() -> ValidatedCVSnapshotRecord:
    payload = freeze_json_object({}, field="cv_contract_test")
    return ValidatedCVSnapshotRecord(
        snapshot_id="snapshot-1",
        cv_extraction_task_id="task-1",
        source_cv_version_id="version-1",
        validation_report_id="report-1",
        policy_version="cv-validation-policy.v2",
        conclusion="pass",
        extraction_payload=payload,
        normalized_payload=payload,
        findings_payload=payload,
        execution_metadata=payload,
        created_at=None,
    )


def _review_result() -> CVReviewResult:
    task = CVExtractionTaskRecord(
        task_id="task-1",
        source_cv_version_id="version-1",
        owner_id="owner-1",
        request_id="request-1",
        execution_id=None,
        execution_metadata=None,
        status="succeeded",
        attempt_count=1,
        max_attempts=3,
        last_error_code=None,
        last_error_message=None,
        retryable=False,
        claimed_by=None,
        lease_expires_at=None,
        heartbeat_at=None,
        next_attempt_at=None,
        finished_at=None,
        validation_conclusion="pass",
        validation_report_payload=freeze_json_object(
            {
                "policy_version": "cv-validation-policy.v2",
                "decision": "allow",
                "findings": [],
                "review_flags": [],
            },
            field="cv_contract_test",
        ),
        validation_task_id="validation-task-1",
        validation_report_id="report-1",
        resume_id=None,
        created_at=None,
        updated_at=None,
        review_payload=freeze_json_object(
            {
                "execution": {},
                "extraction": {
                    "personal_info": {
                        "expected_position": "后端工程师",
                        "evidence": {
                            "source_id": "src-0",
                            "quote": "后端工程师",
                            "start": 0,
                            "end": 6,
                            "alignment": "exact",
                            "occurrence_index": 0,
                        },
                    },
                    "skills": [
                        {
                            "item_id": "skill-1",
                            "name": "Python",
                            "evidence": {
                                "source_id": "src-1",
                                "quote": "Python",
                                "start": 0,
                                "end": 6,
                                "alignment": "exact",
                                "occurrence_index": 0,
                            },
                        }
                    ]
                },
                "normalized": {
                    "normalized_skills": [
                        {
                            "source_item_id": "skill-1",
                            "source_name": "Python",
                            "skill_id": "skill_python",
                            "canonical_name": "Python",
                            "resolution_status": "resolved",
                            "normalization_confidence": 1.0,
                            "resolution_source": "explicit_mapping",
                        }
                    ]
                },
                "review_flags": [
                    {
                        "issue_type": "CV_REVIEW_NEEDED",
                        "severity": "review",
                        "rule_scope": "cv",
                        "description": "review required",
                        "suggested_action": "confirm",
                        "item_id": "skill-1",
                    }
                ],
            },
            field="cv_contract_review",
        ),
        review_id="review-1",
        confirmation_status="pending",
        review_revision=0,
    )
    return CVReviewResult(
        task=task,
        source_cv_id="source-1",
        source_cv_version_id="version-1",
        source_text="Python",
    )


class FakeCVUseCases:
    def __init__(self) -> None:
        self.import_result = SourceCVImportResult(
            source_cv_id="source-1",
            source_cv_version_id="version-1",
            cv_extraction_task_id="task-1",
            created_source=True,
            created_version=True,
            created_task=True,
            task_status="pending",
        )
        self.confirmation = CVConfirmationResult(
            snapshot_id="snapshot-1",
            snapshot_revision=1,
            resume_id="resume-1",
            task_id="task-1",
            idempotency_key="confirm-key",
        )

    def import_and_schedule(self, actor, **kwargs):
        return self.import_result

    def get(self, actor, task_id):
        raise CVExtractionNotFound("CV extraction task not found")

    def get_review(self, actor, task_id):
        return _review_result()

    def get_review_context(self, actor, task_id):
        return _review_result()

    def confirm(self, actor, task_id, confirmation):
        return self.confirmation

    def get_snapshot(self, actor, snapshot_id):
        return _snapshot()


class FailingCVUseCases(FakeCVUseCases):
    def confirm(self, actor, task_id, confirmation):
        raise CVReviewConflict("Review payload is stale")


@contextmanager
def _with_use_cases(use_cases):
    original = app.state.container
    app.state.container = replace(original, cv_ingestion=use_cases)
    try:
        yield
    finally:
        app.state.container = original


def test_upload_response_uses_stable_task_status_and_ids():
    fake = FakeCVUseCases()
    with _with_use_cases(fake):
        response = client.post(
            "/api/v1/internal/source-cvs/import-and-extract",
            json={
                "source_record_id": "source-1",
                "raw_text": "熟练使用 Python",
            },
            headers=_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["trace_id"]
    assert body["data"]["task_status"] == "pending"
    assert body["data"]["cv_extraction_task_id"] == "task-1"


def test_task_not_found_returns_stable_error_code_and_fields():
    fake = FakeCVUseCases()
    with _with_use_cases(fake):
        response = client.get(
            "/api/v1/cv-extraction-tasks/missing",
            headers=_headers(),
        )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404
    assert body["data"]["error_code"] == "CV_EXTRACTION_TASK_NOT_FOUND"
    assert body["data"]["message"] == "CV extraction task not found"


def test_review_conflict_returns_stable_error_code():
    fake = FailingCVUseCases()
    with _with_use_cases(fake):
        response = client.post(
            "/api/v1/cv-extraction-tasks/task-1/confirm",
            json={
                "expected_review_id": "review-1",
                "idempotency_key": "confirm-key",
            },
            headers=_headers(),
        )

    assert response.status_code == 409
    body = response.json()
    assert body["data"]["error_code"] == "CV_REVIEW_CONFLICT"
    assert body["data"]["message"] == "Review payload is stale"


def test_confirm_response_exposes_idempotency_key():
    fake = FakeCVUseCases()
    with _with_use_cases(fake):
        response = client.post(
            "/api/v1/cv-extraction-tasks/task-1/confirm",
            json={
                "expected_review_id": "review-1",
                "idempotency_key": "confirm-key",
            },
            headers=_headers(),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_id"] == "snapshot-1"
    assert data["resume_id"] == "resume-1"
    assert data["idempotency_key"] == "confirm-key"


def test_snapshot_response_links_task():
    fake = FakeCVUseCases()
    with _with_use_cases(fake):
        response = client.get(
            "/api/v1/validated-cv-snapshots/snapshot-1",
            headers=_headers(),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_id"] == "snapshot-1"
    assert data["cv_extraction_task_id"] == "task-1"


def test_review_response_has_strict_structure():
    fake = FakeCVUseCases()
    with _with_use_cases(fake):
        response = client.get(
            "/api/v1/cv-extraction-tasks/task-1/review",
            headers=_headers(),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_id"] == "task-1"
    assert data["source_cv_id"] == "source-1"
    assert data["source_cv_version_id"] == "version-1"
    assert data["status"] == "succeeded"
    assert data["confirmation_status"] == "pending"
    assert data["source_text"] == "Python"
    assert data["review_id"] == "review-1"
    expected_position = next(
        item
        for item in data["reviewable_fields"]
        if item["field_path"] == "expected_position"
    )
    assert expected_position["item_id"] == "personal_info"
    assert expected_position["field_label"] == "目标岗位"
    field = next(
        item
        for item in data["reviewable_fields"]
        if item["field_id"] == "skill-1:name"
    )
    assert field["field_id"] == "skill-1:name"
    assert field["field_type"] == "name"
    assert field["section"] == "skills"
    assert field["item_id"] == "skill-1"
    assert field["field_path"] == "name"
    assert field["field_label"] == "技能"
    assert field["original_value"] == "Python"
    assert field["suggested_value"] == "Python"
    assert field["evidence"]["quote"] == "Python"
    assert field["evidence"]["start"] == 0
    assert field["evidence"]["end"] == 6
    assert field["flag_codes"] == ["CV_REVIEW_NEEDED"]
    assert data["review_flags"][0]["code"] == "CV_REVIEW_NEEDED"
    assert data["review_flags"][0]["severity"] == "review"
    assert data["validation"]["conclusion"] == "pass"
    assert data["validation"]["policy_version"] == "cv-validation-policy.v2"
    assert data["validation"]["blocking_reasons"] == []


def test_review_exposes_missing_patent_placeholder_when_source_mentions_patent():
    class PatentMentionUseCases(FakeCVUseCases):
        def get_review_context(self, actor, task_id):
            return replace(
                _review_result(),
                source_text="论文专利\n某项成果包含专利，标题待人工核验",
            )

    with _with_use_cases(PatentMentionUseCases()):
        response = client.get(
            "/api/v1/cv-extraction-tasks/task-1/review",
            headers=_headers("cv_patent_review_user"),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    field = next(
        item
        for item in data["reviewable_fields"]
        if item["field_id"] == "new_patent_001:title"
    )
    assert field["section"] == "patents"
    assert field["original_value"] is None
    assert "专利" in field["evidence"]["quote"]
    assert any(
        item["code"] == "unstructured_patent_mention"
        for item in data["review_flags"]
    )


def test_review_exposes_missing_education_fields_when_validation_flags_missing_education():
    class MissingEducationUseCases(FakeCVUseCases):
        def get_review_context(self, actor, task_id):
            result = _review_result()
            task = replace(
                result.task,
                review_payload=freeze_json_object(
                    {
                        "execution": {},
                        "extraction": {"education": []},
                        "normalized": {"normalized_skills": []},
                        "review_flags": [
                            {
                                "issue_type": "missing_education",
                                "severity": "soft_error",
                                "rule_scope": "document",
                                "description": "简历缺少教育经历。",
                                "suggested_action": "检查原文是否存在教育经历部分。",
                                "item_id": None,
                            }
                        ],
                    },
                    field="cv_contract_review",
                ),
            )
            return replace(
                result,
                task=task,
                source_text="教育经历\n广东工业大学 2024.09 — 2027.06",
            )

    with _with_use_cases(MissingEducationUseCases()):
        response = client.get(
            "/api/v1/cv-extraction-tasks/task-1/review",
            headers=_headers("cv_education_review_user"),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    fields = [
        item
        for item in data["reviewable_fields"]
        if item["item_id"] == "new_education_001"
    ]
    assert [item["field_path"] for item in fields] == [
        "school",
        "degree",
        "major",
        "date.start",
        "date.end",
    ]
    assert all(item["section"] == "education" for item in fields)
    assert all(item["original_value"] is None for item in fields)
    assert all("教育" in item["evidence"]["quote"] for item in fields)
    assert any(
        item["code"] == "missing_education_supplement"
        for item in data["review_flags"]
    )
