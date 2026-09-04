import json
import socket
import ssl
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.contexts.cv_ingestion import CVIngestionUseCases
from app.contexts.cv_ingestion.domain import CVReviewConfirmation
from app.contexts.data_validation import CVValidationPolicy, CVValidatorSet
from app.contexts.data_validation.fakes import FakeSkillCatalogResolutionPort
from app.domain.accounts import AccountActor
from app.domain.jd_skill_catalog import CatalogClassification
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.infrastructure.cv_ingestion import (
    ApplicationResumeImporter,
    CVExtractionProviderError,
    HttpCVExtractionProvider,
    SqlAlchemyCVIngestionUnitOfWork,
)
from app.api.v1.cv_ingestion import run_cv_extraction
from app.main import app
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.data_validation import CVDataValidationTask, CVValidationReport
from app.models.source_cv import (
    CVExtractionTask,
    SourceCV,
    SourceCVVersion,
    ValidatedCVSnapshot,
)
from tests.runtime_database import reset_database_data, SessionLocal
from tests.user_factory import create_internal_user


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


class FakeCVProvider:
    request_id = "test-cv-provider-v1"

    def __init__(
        self,
        *,
        review_flags=None,
        fail_once: bool = False,
        request_id: str = "test-cv-provider-v1",
        skill_name: str = "Python",
    ):
        self.review_flags = review_flags or []
        self.fail_once = fail_once
        self.request_id = request_id
        self.skill_name = skill_name
        self.calls = 0

    def extract(self, *, document_id: str, raw_text: str, progress_callback=None):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("provider temporarily unavailable")
        return freeze_json_object(
            {
                "contract_version": "cv-extraction-http.v1",
                "document_id": document_id,
                "execution": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "prompt_version": "cv-prompt.v1",
                    "schema_version": "2.4",
                    "normalization_version": "2.0",
                    "taxonomy_version": "skill-taxonomy-snapshot.v1",
                    "latency_ms": 1,
                },
                "extraction_result": {
                    "document_id": document_id,
                    "education": [],
                    "project_experience": [],
                    "work_experience": [],
                    "certificates": [],
                    "awards": [],
                    "skills": [
                        {
                            "item_id": "skill-1",
                            "name": self.skill_name,
                            "item_type": "technical_skill",
                            "proficiency": "proficient",
                            "evidence": {
                                "source_document_id": document_id,
                                "source_id": document_id,
                                "quote": f"熟练使用 {self.skill_name}",
                                "start": 0,
                                "end": len(f"熟练使用 {self.skill_name}"),
                                "alignment": "exact",
                                "occurrence_index": 0,
                            },
                        }
                    ],
                },
                "normalized_result": {
                    "document_id": document_id,
                    "normalized_skills": [
                        {
                            "source_item_id": "skill-1",
                            "source_scope": "skills",
                            "source_name": self.skill_name,
                            "skill_id": "python",
                            "canonical_name": self.skill_name,
                            "category_code": "technical_skill",
                            "resolution_status": "resolved",
                            "normalization_confidence": 1.0,
                            "resolution_source": "canonical_name",
                        }
                    ],
                    "unresolved_items": [],
                },
                "review_flags": self.review_flags,
            },
            field="fake_cv_response",
        )

    def demo_request_id(self, dataset_version: str) -> str:
        return f"demo:{dataset_version}"

    def load_demo_snapshot(self, *, dataset_version: str, document_id: str):
        payload = thaw_json_object(
            self.extract(document_id=document_id, raw_text="熟练使用 Python")
        )
        self.calls -= 1
        payload["execution"].update({
            "mode": "demo_snapshot",
            "provider": "jobgraph_demo_data",
            "model": "not_applicable",
            "prompt_version": "not_applicable",
            "is_demo": True,
            "dataset_version": dataset_version,
        })
        return "熟练使用 Python", freeze_json_object(payload, field="fake_demo_cv")


def _actor(username: str) -> AccountActor:
    account_id = create_internal_user(username, "personal_user")
    assert account_id is not None
    return AccountActor(account_id, "personal_user")


def _use_cases(
    provider: FakeCVProvider,
    *,
    policy_version: str = "cv-validation-policy.v2",
    catalog_provider=FakeSkillCatalogResolutionPort,
) -> CVIngestionUseCases:
    return CVIngestionUseCases(
        lambda: SqlAlchemyCVIngestionUnitOfWork(SessionLocal),
        provider,
        ApplicationResumeImporter(app.state.container.resumes),
        CVValidatorSet(
            CVValidationPolicy(version=policy_version),
            catalog_provider,
        ),
        enabled=True,
        max_attempts=3,
    )


def _position_classification(status: str = "resolved") -> dict:
    resolved = status == "resolved"
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "source_title": "后端开发工程师",
        "position_id": None,
        "position_code": "BACKEND_ENGINEER" if resolved else None,
        "position_name": "后端开发工程师" if resolved else None,
        "family_code": "SOFTWARE_ENGINEERING" if resolved else None,
        "family_name": "软件研发" if resolved else None,
        "candidate_positions": (
            [
                {
                    "position_code": "BACKEND_ENGINEER",
                    "score": 0.91,
                }
            ]
            if resolved
            else []
        ),
        "career_level": "mid",
        "leadership_scope": "none",
        "technology_focus_codes": [],
        "industry_context_codes": [],
        "observed_skill_domain_codes": [],
        "confidence": 0.91 if resolved else 0.4,
        "classification_status": status,
        "review_reason_codes": [] if resolved else ["CATALOG_GAP"],
        "evidence_refs": ["src_0001"] if resolved else [],
        "classification_policy_version": "position-classifier.v3.0",
    }


def _as_v3(payload: dict, *, status: str = "resolved") -> dict:
    payload["contract_version"] = "cv-extraction-http.v3"
    position_evidence = {
        "source_document_id": payload["document_id"],
        "source_id": "src_position",
        "quote": "后端开发工程师",
        "start": 0,
        "end": len("后端开发工程师"),
        "alignment": "exact",
        "occurrence_index": 0,
    }
    payload["extraction_result"]["personal_info"] = {
        "expected_position": "后端开发工程师",
        "evidence": position_evidence,
        "field_evidence": [
            {
                "field_name": "expected_position",
                "evidence": position_evidence,
            }
        ],
    }
    payload["skill_taxonomy"] = {
        "schema_version": "skill-taxonomy-projection.v1",
        "taxonomy_version": "skill-taxonomy-snapshot.v1",
        "skills": [
            {
                "skill_id": "python",
                "canonical_name": "Python",
                "classifications": [
                    {
                        "facet": "concept_class",
                        "code": "technology",
                        "is_primary": True,
                    },
                    {
                        "facet": "technology_kind",
                        "code": "language",
                        "is_primary": True,
                    },
                ],
            }
        ],
    }
    payload["normalized_result"]["position_classifications"] = [
        {
            "feature_id": "role_personal_info_expected_position",
            "source_object_id": "personal_info",
            "source_scope": "personal_info.expected_position",
            "role_kind": "expected",
            "job_classification": _position_classification(status),
        }
    ]
    return payload


def _v3_catalog():
    return FakeSkillCatalogResolutionPort(
        taxonomy_version="skill-taxonomy-snapshot.v1",
        classification_sets={
            "python": (
                "Python",
                (
                    CatalogClassification(
                        "concept_class",
                        "technology",
                        True,
                    ),
                    CatalogClassification(
                        "technology_kind",
                        "language",
                        True,
                    ),
                ),
            )
        },
    )


def _confirm_review(use_cases, actor, scheduled, *, resume_id=None):
    review = use_cases.get_review(actor, scheduled.cv_extraction_task_id)
    execution = review.execution_metadata
    return use_cases.confirm(
        actor,
        scheduled.cv_extraction_task_id,
        CVReviewConfirmation(
            expected_review_id=review.review_id,
            idempotency_key=f"confirm-{scheduled.cv_extraction_task_id}",
            normalization_version=execution["normalization_version"],
            taxonomy_version=execution["taxonomy_version"],
            display_name="智能抽取简历",
        ),
        resume_id=resume_id,
    )


def test_cv_import_run_and_repeat_are_idempotent_across_all_artifacts():
    with SessionLocal() as session:
        baseline_resume_count = session.query(Resume).count()
        baseline_parse_result_count = session.query(ResumeParseResult).count()
    actor = _actor("cv_ingestion_user_1")
    provider = FakeCVProvider()
    use_cases = _use_cases(provider)

    first = use_cases.import_and_schedule(
        actor, source_record_id="resume-001", raw_text="熟练使用 Python"
    )
    repeated = use_cases.import_and_schedule(
        actor, source_record_id="resume-001", raw_text="熟练使用 Python"
    )
    assert repeated == first.__class__(
        first.source_cv_id,
        first.source_cv_version_id,
        first.cv_extraction_task_id,
        False,
        False,
        False,
        "pending",
    )

    completed = use_cases.run(actor, first.cv_extraction_task_id)
    use_cases.run(actor, first.cv_extraction_task_id)
    assert completed.status == "succeeded"
    assert completed.confirmation_status == "pending"
    assert completed.validation_conclusion == "pass"
    assert completed.execution_metadata["model"] == "deepseek-v4-flash"
    assert completed.execution_metadata["current_stage"] == "review_pending"
    assert [
        item["stage"] for item in completed.execution_metadata["task_stages"]
    ] == ["extracting", "contract_validating", "review_pending"]
    assert completed.execution_id is not None
    assert completed.review_id is not None
    assert provider.calls == 1

    confirmed = _confirm_review(use_cases, actor, first)
    repeated_confirm = _confirm_review(use_cases, actor, first)
    assert confirmed.snapshot_id == repeated_confirm.snapshot_id
    assert confirmed.resume_id == repeated_confirm.resume_id
    profile = app.state.container.resumes.get_skill_profile(actor, confirmed.resume_id)
    assert [item.raw_skill for item in profile] == ["Python"]
    with SessionLocal() as session:
        assert session.query(SourceCV).count() == 1
        assert session.query(SourceCVVersion).count() == 1
        assert session.query(CVExtractionTask).count() == 1
        assert session.query(ValidatedCVSnapshot).count() == 1
        assert session.query(Resume).count() == baseline_resume_count + 1
        assert (
            session.query(ResumeParseResult).count()
            == baseline_parse_result_count + 1
        )
        resume = session.get(Resume, confirmed.resume_id)
        assert resume.source_cv_version_id == first.source_cv_version_id
        assert resume.validated_cv_snapshot_id is not None
        snapshot = session.query(ValidatedCVSnapshot).one()
        assert snapshot.execution_metadata["schema_version"] == "2.4"
        task = session.get(CVExtractionTask, first.cv_extraction_task_id)
        assert task.confirmation_status == "confirmed"
        assert task.latest_validated_cv_snapshot_id == snapshot.id


def test_pending_cv_extraction_cancel_is_explicit_and_idempotent():
    actor = _actor("cv_cancel_user")
    use_cases = _use_cases(FakeCVProvider())
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-cancel", raw_text="使用 Python"
    )
    cancelled = use_cases.cancel(actor, scheduled.cv_extraction_task_id)
    replayed = use_cases.cancel(actor, scheduled.cv_extraction_task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.retryable is False
    assert cancelled.finished_at is not None
    assert replayed.task_id == cancelled.task_id
    assert replayed.status == "cancelled"
    with pytest.raises(Exception, match="could not be claimed|cancel"):
        use_cases.run(actor, scheduled.cv_extraction_task_id)


def test_named_demo_snapshot_uses_explicit_path_without_llm_call():
    actor = _actor("cv_demo_snapshot_user")
    provider = FakeCVProvider()
    use_cases = _use_cases(provider)

    result = use_cases.import_demo_snapshot(
        actor, dataset_version="jobgraph-demo-cv.v1"
    )

    assert provider.calls == 0
    snapshot = use_cases.get_snapshot(actor, result.snapshot_id)
    assert snapshot.execution_metadata["mode"] == "demo_snapshot"
    assert snapshot.execution_metadata["provider"] == "jobgraph_demo_data"
    assert snapshot.execution_metadata["is_demo"] is True
    assert snapshot.execution_metadata["dataset_version"] == "jobgraph-demo-cv.v1"


def test_reextract_creates_fresh_task_for_same_source_version_and_preserves_history():
    actor = _actor("cv_reextract_user")
    provider = FakeCVProvider()
    use_cases = _use_cases(provider)
    imported = use_cases.import_and_schedule(
        actor, source_record_id="resume-reextract", raw_text="熟练使用 Python"
    )
    completed = use_cases.run(actor, imported.cv_extraction_task_id)

    fresh = use_cases.reextract(actor, completed.task_id)

    assert fresh.task_id != completed.task_id
    assert fresh.source_cv_version_id == completed.source_cv_version_id
    assert fresh.status == "pending"
    assert ":reextract:" in fresh.request_id
    with SessionLocal() as session:
        tasks = (
            session.query(CVExtractionTask)
            .filter(CVExtractionTask.source_cv_version_id == completed.source_cv_version_id)
            .order_by(CVExtractionTask.created_at.asc())
            .all()
        )
        assert [task.id for task in tasks] == [completed.task_id, fresh.task_id]
        assert tasks[0].review_payload is not None


def test_blocking_cv_review_flag_prevents_snapshot_and_resume_creation():
    actor = _actor("cv_ingestion_user_2")
    use_cases = _use_cases(
        FakeCVProvider(review_flags=[{
            "cv_id": "placeholder",
            "issue_type": "identity_conflict",
            "severity": "blocking",
            "rule_scope": "document",
            "description": "Identity conflict",
            "suggested_action": "Review identity",
        }])
    )
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-002", raw_text="存在身份冲突"
    )

    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)

    assert completed.status == "succeeded"
    assert completed.validation_conclusion == "block"
    assert completed.resume_id is None
    with pytest.raises(Exception, match="Blocked CV extraction cannot be confirmed"):
        _confirm_review(use_cases, actor, scheduled)
    with SessionLocal() as session:
        assert session.query(ValidatedCVSnapshot).count() == 0
        assert session.query(Resume).count() == 0


def test_strict_cv_validation_reviews_empty_and_blocks_invalid_results():
    provider = FakeCVProvider()
    complete = thaw_json_object(
        provider.extract(document_id="version-1", raw_text="Python")
    )
    validator = CVValidatorSet(CVValidationPolicy(), FakeSkillCatalogResolutionPort)

    empty = dict(complete)
    empty["extraction_result"] = {
        **complete["extraction_result"],
        "skills": [],
    }
    empty_result = validator.validate(
        freeze_json_object(empty, field="empty_cv"),
        source_cv_version_id="version-1",
    )
    assert empty_result.decision == "review"
    assert any(
        item["code"] == "cv_extraction_content_empty"
        for item in empty_result.report["findings"]
    )

    malformed = dict(complete)
    malformed["extraction_result"] = {
        **complete["extraction_result"],
        "education": ["invalid"],
    }
    malformed_result = validator.validate(
        freeze_json_object(malformed, field="malformed_cv"),
        source_cv_version_id="version-1",
    )
    assert malformed_result.decision == "block"

    wrong_schema = dict(complete)
    wrong_schema["execution"] = {
        **complete["execution"],
        "schema_version": "cv-schema.invalid",
    }
    wrong_schema_result = validator.validate(
        freeze_json_object(wrong_schema, field="wrong_schema_cv"),
        source_cv_version_id="version-1",
    )
    assert wrong_schema_result.decision == "block"

    wrong_document = dict(complete)
    wrong_document["document_id"] = "version-other"
    wrong_document_result = validator.validate(
        freeze_json_object(wrong_document, field="wrong_document_cv"),
        source_cv_version_id="version-1",
    )
    assert wrong_document_result.decision == "block"
    assert any(
        item["code"] == "cv_document_lineage_invalid"
        for item in wrong_document_result.report["findings"]
    )


def test_cv_v2_taxonomy_projection_must_match_authoritative_catalog():
    payload = thaw_json_object(
        FakeCVProvider().extract(document_id="version-1", raw_text="Python")
    )
    payload["contract_version"] = "cv-extraction-http.v2"
    payload["skill_taxonomy"] = {
        "schema_version": "skill-taxonomy-projection.v1",
        "taxonomy_version": "skill-taxonomy-snapshot.v1",
        "skills": [
            {
                "skill_id": "python",
                "canonical_name": "Python",
                "classifications": [
                    {
                        "facet": "concept_class",
                        "code": "technology",
                        "is_primary": True,
                    },
                    {
                        "facet": "technology_kind",
                        "code": "language",
                        "is_primary": True,
                    },
                ],
            }
        ],
    }
    catalog = FakeSkillCatalogResolutionPort(
        taxonomy_version="skill-taxonomy-snapshot.v1",
        classification_sets={
            "python": (
                "Python",
                (
                    CatalogClassification("concept_class", "technology", True),
                    CatalogClassification("technology_kind", "language", True),
                ),
            )
        },
    )
    validator = CVValidatorSet(CVValidationPolicy(), lambda: catalog)
    result = validator.validate(
        freeze_json_object(payload, field="v2_cv"),
        source_cv_version_id="version-1",
    )
    assert result.decision == "allow"

    payload["skill_taxonomy"]["skills"][0]["canonical_name"] = "Wrong"
    mismatch = validator.validate(
        freeze_json_object(payload, field="v2_cv_mismatch"),
        source_cv_version_id="version-1",
    )
    assert mismatch.decision == "block"
    assert any(
        item["code"] == "cv_taxonomy_projection_content_mismatch"
        for item in mismatch.report["findings"]
    )


def test_cv_v3_position_classification_flows_into_confirmed_snapshot():
    class V3Provider(FakeCVProvider):
        request_id = "test-cv-provider-v3"

        def extract(self, *, document_id: str, raw_text: str, progress_callback=None):
            payload = thaw_json_object(
                super().extract(document_id=document_id, raw_text=raw_text)
            )
            skill_evidence = payload["extraction_result"]["skills"][0][
                "evidence"
            ]
            skill_evidence["start"] = raw_text.index(skill_evidence["quote"])
            skill_evidence["end"] = (
                skill_evidence["start"] + len(skill_evidence["quote"])
            )
            return freeze_json_object(
                _as_v3(payload),
                field="fake_cv_v3_response",
            )

    actor = _actor("cv_ingestion_position_v3")
    use_cases = _use_cases(
        V3Provider(),
        catalog_provider=_v3_catalog,
    )
    scheduled = use_cases.import_and_schedule(
        actor,
        source_record_id="resume-position-v3",
        raw_text="后端开发工程师\n熟练使用 Python",
    )

    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)
    assert completed.validation_conclusion == "pass"
    assert (
        completed.review_payload["normalized"]["position_classifications"][0][
            "job_classification"
        ]["position_code"]
        == "BACKEND_ENGINEER"
    )

    confirmed = _confirm_review(use_cases, actor, scheduled)
    snapshot = use_cases.get_snapshot(actor, confirmed.snapshot_id)
    roles = snapshot.normalized_payload["position_classifications"]
    assert len(roles) == 1
    assert (
        roles[0]["job_classification"]["position_code"]
        == "BACKEND_ENGINEER"
    )


def test_cv_v3_unresolved_position_requires_review_and_duplicates_block():
    payload = _as_v3(
        thaw_json_object(
            FakeCVProvider().extract(
                document_id="version-1",
                raw_text="Python",
            )
        ),
        status="catalog_gap",
    )
    validator = CVValidatorSet(CVValidationPolicy(), _v3_catalog)

    unresolved = validator.validate(
        freeze_json_object(payload, field="v3_cv_unresolved"),
        source_cv_version_id="version-1",
    )
    assert unresolved.decision == "review"
    assert any(
        item["code"] == "cv_position_classification_unresolved"
        for item in unresolved.report["findings"]
    )

    payload["normalized_result"]["position_classifications"].append(
        payload["normalized_result"]["position_classifications"][0]
    )
    duplicate = validator.validate(
        freeze_json_object(payload, field="v3_cv_duplicate"),
        source_cv_version_id="version-1",
    )
    assert duplicate.decision == "block"
    assert any(
        item["code"] == "cv_position_classification_identity_duplicate"
        for item in duplicate.report["findings"]
    )

    payload["normalized_result"]["position_classifications"] = []
    missing = validator.validate(
        freeze_json_object(payload, field="v3_cv_missing_position"),
        source_cv_version_id="version-1",
    )
    assert missing.decision == "block"
    assert any(
        item["code"] == "cv_position_classification_coverage_mismatch"
        for item in missing.report["findings"]
    )


def test_review_cv_creates_confirmable_resume_and_preserves_flags():
    actor = _actor("cv_ingestion_review")
    review_flags = [{
        "cv_id": "placeholder",
        "issue_type": "employment_date_uncertain",
        "severity": "review",
        "rule_scope": "document",
        "description": "Employment date needs confirmation",
        "suggested_action": "Confirm the employment date",
    }]
    use_cases = _use_cases(FakeCVProvider(review_flags=review_flags))
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-review", raw_text="熟练使用 Python"
    )

    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)

    assert completed.status == "succeeded"
    assert completed.validation_conclusion == "warn"
    assert completed.confirmation_status == "pending"
    assert completed.review_payload["review_flags"][0]["issue_type"] == (
        "employment_date_uncertain"
    )
    assert completed.validation_report_payload is not None
    assert completed.validation_report_payload["review_flags"][0]["issue_type"] == (
        "employment_date_uncertain"
    )
    confirmed = _confirm_review(use_cases, actor, scheduled)
    snapshot_id = confirmed.snapshot_id
    with SessionLocal() as session:
        snapshot = session.query(ValidatedCVSnapshot).one()
        resume = session.get(Resume, confirmed.resume_id)
        parse_result = (
            session.query(ResumeParseResult)
            .filter(ResumeParseResult.resume_id == confirmed.resume_id)
            .one()
        )
        assert snapshot.conclusion == "warn"
        assert snapshot.id == snapshot_id
        assert resume.validated_cv_snapshot_id == snapshot.id
        assert parse_result.need_review is True
    profile = app.state.container.resumes.get_skill_profile(actor, confirmed.resume_id)
    assert [item.raw_skill for item in profile] == ["Python"]


def test_confirm_with_requested_resume_id_uses_stable_identity():
    actor = _actor("cv_ingestion_stable_id")
    use_cases = _use_cases(FakeCVProvider())
    scheduled = use_cases.import_and_schedule(
        actor,
        source_record_id="resume-stable-id",
        raw_text="熟练使用 Python",
    )
    use_cases.run(actor, scheduled.cv_extraction_task_id)

    confirmed = _confirm_review(use_cases, actor, scheduled, resume_id="stable-cv-001")

    assert confirmed.resume_id == "stable-cv-001"
    with SessionLocal() as session:
        assert session.get(Resume, "stable-cv-001") is not None
        assert (
            session.get(Resume, "stable-cv-001").validated_cv_snapshot_id
            == confirmed.snapshot_id
        )


def test_new_snapshot_updates_existing_resume_without_duplicate_artifacts():
    actor = _actor("cv_ingestion_snapshot_update")
    first_use_cases = _use_cases(
        FakeCVProvider(
            request_id="snapshot-a",
            skill_name="Python",
        )
    )
    first = first_use_cases.import_and_schedule(
        actor, source_record_id="resume-versioned", raw_text="熟练使用 Python"
    )
    first_use_cases.run(actor, first.cv_extraction_task_id)
    first_confirmed = _confirm_review(first_use_cases, actor, first)

    second_use_cases = _use_cases(
        FakeCVProvider(
            request_id="snapshot-b",
            skill_name="FastAPI",
        )
    )
    second = second_use_cases.import_and_schedule(
        actor,
        source_record_id="resume-versioned",
        raw_text="熟练使用 FastAPI",
        source_version="2",
    )
    second_use_cases.run(actor, second.cv_extraction_task_id)
    second_confirmed = _confirm_review(second_use_cases, actor, second)

    assert first.source_cv_version_id != second.source_cv_version_id
    assert first.cv_extraction_task_id != second.cv_extraction_task_id
    assert first_confirmed.resume_id == second_confirmed.resume_id
    assert first_confirmed.snapshot_id != second_confirmed.snapshot_id
    with SessionLocal() as session:
        snapshots = (
            session.query(ValidatedCVSnapshot)
            .order_by(ValidatedCVSnapshot.snapshot_revision)
            .all()
        )
        resume = session.get(Resume, first_confirmed.resume_id)
        parse_result = (
            session.query(ResumeParseResult)
            .filter(ResumeParseResult.resume_id == resume.id)
            .one()
        )
        assert len(snapshots) == 2
        assert session.query(Resume).count() == 1
        assert resume.validated_cv_snapshot_id == snapshots[-1].id
        assert parse_result.skills[0]["raw_skill"] == "FastAPI"

    repeated = second_use_cases.run(actor, second.cv_extraction_task_id)
    assert repeated.status == "succeeded"
    with SessionLocal() as session:
        assert session.query(ValidatedCVSnapshot).count() == 2
        assert session.query(Resume).count() == 1


def test_validation_policy_version_is_consistent_from_task_to_snapshot():
    policy_version = "cv-validation-policy.v3-test"
    actor = _actor("cv_ingestion_policy_version")
    use_cases = _use_cases(
        FakeCVProvider(),
        policy_version=policy_version,
    )
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-policy", raw_text="熟练使用 Python"
    )

    use_cases.run(actor, scheduled.cv_extraction_task_id)
    _confirm_review(use_cases, actor, scheduled)

    with SessionLocal() as session:
        task = session.get(CVExtractionTask, scheduled.cv_extraction_task_id)
        validation_task = session.query(CVDataValidationTask).one()
        validation_report = session.query(CVValidationReport).one()
        snapshot = session.query(ValidatedCVSnapshot).one()
        assert task.request_id == "test-cv-provider-v1"
        assert validation_task.policy_version == policy_version
        assert validation_report.policy_version == policy_version
        assert snapshot.policy_version == policy_version


def _http_provider() -> HttpCVExtractionProvider:
    return HttpCVExtractionProvider(
        "http://cv-extraction.test",
        "test-token",
        1,
        1,
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        prompt_version="cv-prompt.v1",
        schema_version="2.4",
        normalization_version="2.0",
        validation_policy_version="cv-validation-policy.v3-test",
    )


class _FakeProviderResponse:
    def __init__(
        self,
        status_code: int,
        payload=None,
        *,
        invalid_json: bool = False,
    ):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("not json")
        return self._payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload) if self._payload is not None else ""


def _provider_request() -> httpx.Request:
    return httpx.Request(
        "POST",
        "http://cv-extraction.test/api/v3/cv-extractions",
    )


def test_http_provider_requests_v3_and_preserves_position_classification(
    monkeypatch,
):
    provider = _http_provider()
    raw_text = "后端开发工程师\n熟练使用 Python"
    payload = _as_v3(
        thaw_json_object(
            FakeCVProvider().extract(
                document_id="doc-1",
                raw_text=raw_text,
            )
        )
    )
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return _FakeProviderResponse(
            200,
            {"code": 0, "message": "success", "data": payload},
        )

    monkeypatch.setattr(httpx, "post", post)

    result = provider.extract(document_id="doc-1", raw_text=raw_text)

    assert captured["url"].endswith("/api/v3/cv-extractions")
    assert captured["json"]["document_id"] == "doc-1"
    assert (
        result["normalized_result"]["position_classifications"][0][
            "job_classification"
        ]["position_code"]
        == "BACKEND_ENGINEER"
    )


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "CV_EXTRACTION_AUTH_FAILED"),
        (403, "CV_EXTRACTION_AUTH_FAILED"),
        (404, "CV_EXTRACTION_MODEL_NOT_AVAILABLE"),
        (429, "CV_EXTRACTION_RATE_LIMITED"),
        (500, "CV_EXTRACTION_PROVIDER_UNAVAILABLE"),
    ],
)
def test_http_provider_maps_status_codes_to_domain_errors(
    monkeypatch,
    status_code,
    expected_code,
):
    provider = _http_provider()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _FakeProviderResponse(status_code),
    )

    with pytest.raises(CVExtractionProviderError) as exc_info:
        provider.extract(document_id="doc-1", raw_text="Python")

    assert exc_info.value.code == expected_code


def test_http_provider_passes_through_upstream_domain_code(monkeypatch):
    provider = _http_provider()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _FakeProviderResponse(
            422,
            {
                "detail": {
                    "code": "CV_EVIDENCE_ALIGNMENT_INVALID",
                    "message": "Evidence occurrence_index is not reproducible",
                }
            },
        ),
    )

    with pytest.raises(CVExtractionProviderError) as exc_info:
        provider.extract(document_id="doc-1", raw_text="Python")

    assert exc_info.value.code == "CV_EVIDENCE_ALIGNMENT_INVALID"
    assert "occurrence_index" in str(exc_info.value)


def test_http_provider_404_message_names_the_configured_model(monkeypatch):
    provider = _http_provider()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _FakeProviderResponse(404),
    )

    with pytest.raises(CVExtractionProviderError) as exc_info:
        provider.extract(document_id="doc-1", raw_text="Python")

    assert exc_info.value.code == "CV_EXTRACTION_MODEL_NOT_AVAILABLE"
    assert "deepseek-v4-flash" in str(exc_info.value)


def test_http_provider_separates_timeout_and_connection_failures(monkeypatch):
    provider = _http_provider()
    request = _provider_request()

    def timeout_post(*_args, **_kwargs):
        raise httpx.TimeoutException("slow", request=request)

    monkeypatch.setattr(httpx, "post", timeout_post)
    with pytest.raises(CVExtractionProviderError) as timeout_error:
        provider.extract(document_id="doc-1", raw_text="Python")
    assert timeout_error.value.code == "CV_EXTRACTION_PROVIDER_TIMEOUT"

    tls_error = httpx.ConnectError("tls", request=request)
    tls_error.__cause__ = ssl.SSLError("tls failure")

    def tls_post(*_args, **_kwargs):
        raise tls_error

    monkeypatch.setattr(httpx, "post", tls_post)
    with pytest.raises(CVExtractionProviderError) as tls_exc:
        provider.extract(document_id="doc-1", raw_text="Python")
    assert tls_exc.value.code == "CV_EXTRACTION_PROVIDER_CONNECTION_FAILED"
    assert "tls" in str(tls_exc.value)

    dns_error = httpx.ConnectError("dns", request=request)
    dns_error.__cause__ = socket.gaierror(-2, "Name or service not known")

    def dns_post(*_args, **_kwargs):
        raise dns_error

    monkeypatch.setattr(httpx, "post", dns_post)
    with pytest.raises(CVExtractionProviderError) as dns_exc:
        provider.extract(document_id="doc-1", raw_text="Python")
    assert dns_exc.value.code == "CV_EXTRACTION_PROVIDER_CONNECTION_FAILED"
    assert "dns" in str(dns_exc.value)


def test_http_provider_maps_invalid_response_and_contract_errors(monkeypatch):
    provider = _http_provider()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _FakeProviderResponse(200, invalid_json=True),
    )
    with pytest.raises(CVExtractionProviderError) as invalid_json_error:
        provider.extract(document_id="doc-1", raw_text="Python")
    assert (
        invalid_json_error.value.code
        == "CV_EXTRACTION_PROVIDER_INVALID_RESPONSE"
    )

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _FakeProviderResponse(200, {"code": 1, "data": {}}),
    )
    with pytest.raises(CVExtractionProviderError) as contract_error:
        provider.extract(document_id="doc-1", raw_text="Python")
    assert contract_error.value.code == "CV_EXTRACTION_CONTRACT_INVALID"


def test_failed_cv_task_is_retriable_with_bounded_attempt_count():
    actor = _actor("cv_ingestion_user_3")
    provider = FakeCVProvider(fail_once=True)
    use_cases = _use_cases(provider)
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-003", raw_text="熟练使用 Python"
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        use_cases.run(actor, scheduled.cv_extraction_task_id)
    failed = use_cases.get(actor, scheduled.cv_extraction_task_id)
    assert failed.status == "failed"
    assert failed.attempt_count == 1

    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)
    assert completed.status == "succeeded"
    assert completed.attempt_count == 2
    assert provider.calls == 2


def test_claim_is_fenced_and_expired_lease_is_recovered():
    actor = _actor("cv_ingestion_lease")
    use_cases = _use_cases(FakeCVProvider())
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-lease", raw_text="熟练使用 Python"
    )
    now = datetime.now(timezone.utc)

    with SqlAlchemyCVIngestionUnitOfWork(SessionLocal) as uow:
        first = uow.repository.claim(
            scheduled.cv_extraction_task_id,
            worker_id="worker-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=1),
        )
        uow.commit()
    assert first is not None
    with SqlAlchemyCVIngestionUnitOfWork(SessionLocal) as uow:
        assert (
            uow.repository.claim(
                scheduled.cv_extraction_task_id,
                worker_id="worker-2",
                now=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            is None
        )
        assert uow.repository.recover_stale(now=now + timedelta(seconds=2)) == 1
        second = uow.repository.claim(
            scheduled.cv_extraction_task_id,
            worker_id="worker-2",
            now=now + timedelta(seconds=2),
            lease_expires_at=now + timedelta(seconds=12),
        )
        uow.commit()
    assert second is not None
    assert second.claimed_by == "worker-2"
    assert second.attempt_count == 2


def test_expired_leases_stop_after_max_attempts():
    actor = _actor("cv_ingestion_exhausted")
    provider = FakeCVProvider()
    use_cases = CVIngestionUseCases(
        lambda: SqlAlchemyCVIngestionUnitOfWork(SessionLocal),
        provider,
        ApplicationResumeImporter(app.state.container.resumes),
        CVValidatorSet(CVValidationPolicy(), FakeSkillCatalogResolutionPort),
        enabled=True,
        max_attempts=2,
    )
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-exhausted", raw_text="熟练使用 Python"
    )
    now = datetime.now(timezone.utc)
    with SqlAlchemyCVIngestionUnitOfWork(SessionLocal) as uow:
        for attempt in range(2):
            claimed = uow.repository.claim(
                scheduled.cv_extraction_task_id,
                worker_id=f"worker-{attempt}",
                now=now + timedelta(seconds=attempt * 2),
                lease_expires_at=now + timedelta(seconds=attempt * 2 + 1),
            )
            assert claimed is not None
            assert uow.repository.recover_stale(now=now + timedelta(seconds=attempt * 2 + 2)) == 1
        assert (
            uow.repository.claim(
                scheduled.cv_extraction_task_id,
                worker_id="worker-final",
                now=now + timedelta(seconds=10),
                lease_expires_at=now + timedelta(seconds=20),
            )
            is None
        )
        exhausted = uow.repository.get_task(scheduled.cv_extraction_task_id)
        uow.commit()
    assert exhausted is not None
    assert exhausted.status == "failed"
    assert exhausted.retryable is False
    assert exhausted.attempt_count == exhausted.max_attempts == 2


def test_http_run_compatibility_only_queues_and_never_calls_provider():
    actor = _actor("cv_ingestion_http_queue")
    provider = FakeCVProvider()
    use_cases = _use_cases(provider)
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="resume-http", raw_text="熟练使用 Python"
    )

    response = run_cv_extraction(
        scheduled.cv_extraction_task_id,
        actor=actor,
        use_cases=use_cases,
    )

    assert response["data"]["status"] == "pending"
    assert provider.calls == 0
