import json

import pytest

from app.core.config import Settings, settings
from app.models.data_validation import DataValidationTask
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.review_task import ReviewTask
from app.models.standard_position import StandardPosition
from scripts.apply_position_v3_to_existing_jds import apply
from scripts.publish_position_v3_migrated_jds import publish_migration
from tests.runtime_database import SessionLocal, reset_database_data


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _classification() -> dict[str, object]:
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "source_title": "Backend Engineer",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端开发工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件研发",
        "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.92}],
        "career_level": "senior",
        "leadership_scope": "none",
        "technology_focus_codes": ["CLOUD_NATIVE"],
        "industry_context_codes": [],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.92,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["evidence-1"],
        "classification_policy_version": "position-classifier.v3.0",
    }


def test_historical_migration_creates_new_review_and_validation_lineage(tmp_path):
    run_dir = tmp_path / "run"
    normalized_dir = run_dir / "final"
    normalized_dir.mkdir(parents=True)
    (normalized_dir / "normalized_annotations.json").write_text(
        json.dumps(
            [{"document_id": "source-doc-1", "job_classification": _classification()}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with SessionLocal() as session:
        position = StandardPosition(
            id="main-position-id",
            position_code="BACKEND_ENGINEER",
            position_name="后端开发工程师",
            taxonomy_family_code="SOFTWARE_ENGINEERING",
            taxonomy_family_name="软件研发",
            skill_domain_codes=["software_engineering"],
            taxonomy_version="position-taxonomy.v3.0.0",
            lifecycle_status="active",
            sample_support_status="sufficient",
            status="catalog",
        )
        jd = JobDescription(
            id="historical-jd",
            source_type="batch",
            source_name="batch:source-doc-1",
            title="Backend Engineer",
            raw_text="Backend Engineer uses Python",
            cleaned_text="Backend Engineer uses Python",
            parse_status="completed",
        )
        parsed = JDParseResult(
            id="historical-parse",
            jd_id=jd.id,
            extraction_result={
                "schema_version": "v2",
                "document_id": "historical-jd",
                "job_title": None,
                "responsibilities": [],
                "requirements": [],
                "company_facts": [],
                "employment_facts": [],
            },
            normalized_result={
                "schema_version": "v2",
                "document_id": "historical-jd",
                "job_classification": {
                    "schema_version": "job-position-classification.v2",
                    "resolution_status": "resolved",
                },
                "normalized_requirements": [],
                "salary": None,
                "unresolved_items": [],
            },
            workflow_status="published",
            need_review=False,
        )
        publication = JDPublication(
            id="historical-publication",
            parse_result_id=parsed.id,
            jd_id=jd.id,
            document_id="source-doc-1",
            schema_version="v2",
            normalization_schema_version="v2",
            idempotency_key="historical-publication-key",
            snapshot_payload={"contract_version": "jd-publication-snapshot.v2"},
            published_by="historical-publisher",
        )
        session.add_all([position, jd, parsed, publication])
        session.commit()

    result = apply(
        [run_dir],
        settings.DATABASE_URL,
        migration_run_id="migration-test",
        execute=True,
    )

    assert result["created_v3_jd_versions"] == 1
    assert result["staged_validation_tasks"] == 1
    with SessionLocal() as session:
        historical = session.get(JDParseResult, "historical-parse")
        assert (
            historical.normalized_result["job_classification"]["schema_version"]
            == "job-position-classification.v2"
        )
        migrated = session.query(JDParseResult).filter(JDParseResult.id != historical.id).one()
        assert migrated.workflow_status == "draft"
        assert migrated.need_review is True
        classification = migrated.normalized_result["job_classification"]
        assert classification["position_id"] == "main-position-id"
        assert classification["position_code"] == "BACKEND_ENGINEER"
        assert (
            session.query(ReviewTask)
            .filter_by(
                object_type="jd_parse_result",
                object_id=migrated.id,
                status="pending",
            )
            .one()
        )
        assert session.query(DataValidationTask).count() == 1
        assert session.query(JDPublication).count() == 1

        session.query(DataValidationTask).delete()
        session.commit()

    resumed = apply(
        [run_dir],
        settings.DATABASE_URL,
        migration_run_id="migration-test",
        execute=True,
    )

    assert resumed["created_v3_jd_versions"] == 0
    assert resumed["existing_v3_jd_versions"] == 1
    assert resumed["staged_validation_tasks"] == 1
    assert resumed["resumed_validation_tasks"] == 1
    with SessionLocal() as session:
        assert session.query(JDParseResult).count() == 2
        assert session.query(DataValidationTask).count() == 1

    with pytest.raises(ValueError, match="still requires human review"):
        publish_migration(
            settings=Settings(
                DATABASE_URL=settings.DATABASE_URL,
                DATA_VALIDATION_MODE="enforce",
            ),
            migration_run_id="migration-test",
            publisher_id="publisher-1",
            expected_count=1,
            execute=False,
        )
