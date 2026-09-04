import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.offline_import import OfflineImportBatch, OfflineImportItem


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env["ALEMBIC_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _assert_alembic_at_head(
    database_url: str,
    current: str | None = None,
    heads: str | None = None,
) -> None:
    if current is None:
        current = _run_alembic(database_url, "current").stdout
    if heads is None:
        heads = _run_alembic(database_url, "heads").stdout
    head_ids = {
        line.split()[0]
        for line in heads.splitlines()
        if line.strip()
    }
    assert head_ids and any(head_id in current for head_id in head_ids), (
        current,
        heads,
    )


def test_offline_bundle_migration_upgrades_and_downgrades_sqlite():
    database_path = Path("data") / f"offline_migration_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260726_31")
        _run_alembic(database_url, "upgrade", "20260726_32")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "offline_import_batches" in tables
            assert "offline_import_items" in tables
            assert {
                column["name"]
                for column in inspector.get_columns("offline_import_batches")
            } == {
                column.name
                for column in OfflineImportBatch.__table__.columns
                if column.name != "bundle_digest"
            }
            assert {
                column["name"]
                for column in inspector.get_columns("offline_import_items")
            } == {column.name for column in OfflineImportItem.__table__.columns}
            assert {
                tuple(key["constrained_columns"])
                for key in inspector.get_foreign_keys("offline_import_items")
            } == {("batch_id",)}
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260726_31")
        engine = create_engine(database_url)
        try:
            tables = set(inspect(engine).get_table_names())
            assert "offline_import_batches" not in tables
            assert "offline_import_items" not in tables
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_initial_migration_upgrades_an_empty_database_and_is_current():
    database_path = Path("data") / f"migration_test_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "head")
        check = _run_alembic(database_url, "check")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            extraction_task_columns = {
                item["name"] for item in inspector.get_columns("extraction_tasks")
            }
            jd_columns = {
                item["name"] for item in inspector.get_columns("job_descriptions")
            }
            jd_indexes = {
                item["name"]: item for item in inspector.get_indexes("job_descriptions")
            }
            publication_columns = {
                item["name"] for item in inspector.get_columns("jd_publications")
            }
            publication_indexes = {
                item["name"]: item
                for item in inspector.get_indexes("jd_publications")
            }
            with engine.connect() as connection:
                publication_triggers = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND tbl_name = 'jd_publications'"
                    ).all()
                }
            validation_task_column_details = {
                item["name"]: item
                for item in inspector.get_columns("data_validation_tasks")
            }
            validation_task_columns = set(validation_task_column_details)
            validation_report_columns = {
                item["name"]
                for item in inspector.get_columns("validation_reports")
            }
            validation_snapshot_columns = {
                item["name"]
                for item in inspector.get_columns("validated_bundle_snapshots")
            }
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert "No new upgrade operations detected" in check.stdout
    assert {
        "alembic_version",
        "users",
        "resumes",
        "trend_report_review_adjustments",
    } <= tables
    assert "match_reports" not in tables
    assert {"source_jds", "source_jd_versions", "extraction_tasks"} <= tables
    assert {"skill_taxonomy_nodes", "skill_classifications"} <= tables
    assert "jd_publications" in tables
    assert {"claimed_by", "lease_expires_at", "heartbeat_at"} <= extraction_task_columns
    assert {
        "source_jd_id",
        "source_jd_version_id",
        "extraction_task_id",
        "source_document_id",
        "extraction_bundle_version",
    } <= jd_columns
    assert jd_indexes["ix_job_descriptions_extraction_task_id"]["unique"] == 1
    assert {
        "parse_result_id",
        "source_jd_version_id",
        "extraction_task_id",
        "snapshot_payload",
    } <= publication_columns
    assert "ix_jd_publications_parse_result_id" in publication_indexes
    assert publication_triggers == {
        "jd_publications_reject_update",
        "jd_publications_reject_delete",
    }
    assert {
        "extraction_task_id",
        "source_jd_version_id",
        "bundle_id",
        "policy_version",
        "idempotency_key",
        "status",
        "lock_version",
    } <= validation_task_columns
    assert validation_task_column_details["lock_version"]["nullable"] is False
    assert "1" in str(validation_task_column_details["lock_version"]["default"])
    assert {
        "data_validation_task_id",
        "conclusion",
        "report_payload",
    } <= validation_report_columns
    assert {
        "validation_report_id",
        "data_validation_task_id",
        "extraction_task_id",
        "source_jd_version_id",
        "bundle_payload",
        "report_payload",
    } <= validation_snapshot_columns


def test_matching_reference_product_summary_columns_are_available():
    database_path = Path("data") / f"migration_matching_summary_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "head")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            columns = {
                item["name"]
                for item in inspector.get_columns("matching_service_references")
            }
            assert {
                "matching_method",
                "degraded",
                "overall_score",
            } <= columns
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_normalization_candidate_review_pool_migration_merges_duplicates():
    database_path = Path("data") / f"migration_candidate_pool_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260804_58")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                statement = (
                    "INSERT INTO skill_normalization_candidates "
                    "(id, raw_skill, candidate_skill_id, confidence, context, status, "
                    "created_at, updated_at) VALUES "
                    "(:id, :raw_skill, NULL, 0, :context, 'pending', "
                    ":created_at, :updated_at)"
                )
                connection.exec_driver_sql(
                    statement,
                    {
                        "id": "candidate-1",
                        "raw_skill": " ＡＩ  Agent ",
                        "context": "JD evidence",
                        "created_at": "2026-08-01 00:00:00",
                        "updated_at": "2026-08-01 00:00:00",
                    },
                )
                connection.exec_driver_sql(
                    statement,
                    {
                        "id": "candidate-2",
                        "raw_skill": "ai agent",
                        "context": "CV evidence",
                        "created_at": "2026-08-02 00:00:00",
                        "updated_at": "2026-08-02 00:00:00",
                    },
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "20260805_59")
        verification = create_engine(database_url)
        try:
            with verification.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT raw_skill, normalized_skill, occurrence_count, "
                    "source_type, evidence_samples, status, first_seen_at, last_seen_at "
                    "FROM skill_normalization_candidates"
                ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["raw_skill"] == "AI Agent"
            assert rows[0]["normalized_skill"] == "ai agent"
            assert rows[0]["occurrence_count"] == 2
            assert rows[0]["source_type"] == "unknown"
            assert rows[0]["status"] == "pending"
            assert {
                item["evidence"] for item in json.loads(rows[0]["evidence_samples"])
            } == {"JD evidence", "CV evidence"}
            assert rows[0]["first_seen_at"]
            assert rows[0]["last_seen_at"]
        finally:
            verification.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_candidate_grant_version_migration_recovers_partial_sqlite_ddl():
    database_path = Path("data") / f"migration_grant_partial_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260729_37")
        partial_engine = create_engine(database_url)
        try:
            # Reproduce the persisted volume: SQLite committed the column, but
            # Alembic never advanced beyond revision 37.
            with partial_engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE candidate_submissions "
                    "ADD COLUMN grant_version INTEGER DEFAULT 1 NOT NULL"
                )
        finally:
            partial_engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        _assert_alembic_at_head(database_url)

        verification_engine = create_engine(database_url)
        try:
            columns = {
                column["name"]
                for column in inspect(verification_engine).get_columns(
                    "candidate_submissions"
                )
            }
            assert "grant_version" in columns
        finally:
            verification_engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_remote_skill_trend_migration_recovers_schema_applied_before_version_stamp():
    database_path = Path("data") / f"migration_trend_report_partial_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260730_41")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                for statement in (
                    "ALTER TABLE trend_reports ADD COLUMN provider_run_id VARCHAR(80)",
                    "ALTER TABLE trend_reports ADD COLUMN input_fingerprint VARCHAR(64)",
                    "ALTER TABLE trend_reports ADD COLUMN algorithm_version VARCHAR(128)",
                    "ALTER TABLE trend_reports ADD COLUMN formula_version VARCHAR(128)",
                    "ALTER TABLE trend_reports ADD COLUMN skill_catalog_version VARCHAR(128)",
                    "ALTER TABLE trend_reports ADD COLUMN source_coverage FLOAT",
                    "ALTER TABLE trend_reports ADD COLUMN missing_sources JSON NOT NULL DEFAULT '[]'",
                    "ALTER TABLE trend_reports ADD COLUMN quality_flags JSON NOT NULL DEFAULT '[]'",
                    "ALTER TABLE trend_reports ADD COLUMN evidence_references JSON NOT NULL DEFAULT '[]'",
                    "ALTER TABLE trend_reports ADD COLUMN unresolved_terms JSON NOT NULL DEFAULT '[]'",
                    "CREATE UNIQUE INDEX uq_trend_report_provider_position_graph "
                    "ON trend_reports (provider_run_id, position_id, graph_version_id)",
                    "CREATE INDEX ix_trend_reports_provider_run_id ON trend_reports (provider_run_id)",
                ):
                    connection.exec_driver_sql(statement)
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        _run_alembic(database_url, "upgrade", "head")
        _assert_alembic_at_head(database_url)
        verification = create_engine(database_url)
        try:
            columns = {item["name"] for item in inspect(verification).get_columns("trend_reports")}
            assert {
                "provider_run_id",
                "unresolved_terms",
                "source_coverage",
                "skill_trend_details",
            } <= columns
        finally:
            verification.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_resume_identity_migration_backfills_legacy_text_and_file_names():
    database_path = Path("data") / f"migration_resume_identity_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260729_38")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO file_assets "
                    "(id, owner_user_id, filename, path, size, created_at) VALUES "
                    "('file-resume', 'user-resume', 'candidate.pdf', "
                    "'candidate.pdf', 12, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO resumes "
                    "(id, user_id, source_type, file_id, raw_text, parse_status, "
                    "input_extraction_status, created_at, updated_at) VALUES "
                    "('resume-text', 'user-resume', 'text', NULL, 'text', "
                    "'completed', 'not_required', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('resume-file', 'user-resume', 'file', 'file-resume', 'file', "
                    "'completed', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        verification_engine = create_engine(database_url)
        try:
            with verification_engine.connect() as connection:
                rows = dict(
                    connection.exec_driver_sql(
                        "SELECT id, display_name FROM resumes ORDER BY id"
                    ).all()
                )
                original_filename = connection.exec_driver_sql(
                    "SELECT original_filename FROM resumes WHERE id = 'resume-file'"
                ).scalar_one()
        finally:
            verification_engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert rows == {
        "resume-file": "candidate.pdf",
        "resume-text": "文本简历",
    }
    assert original_filename == "candidate.pdf"


def test_source_jd_migration_preserves_existing_jds_and_enforces_immutability():
    database_path = Path("data") / f"migration_source_jd_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260722_21")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO job_descriptions "
                    "(id, source_type, title, raw_text, parse_status, "
                    "input_extraction_status, is_downweighted, created_at, updated_at) "
                    "VALUES ('legacy-jd', 'legacy', 'Legacy', 'unchanged', 'pending', "
                    "'not_required', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                legacy_count = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM job_descriptions WHERE id = 'legacy-jd'"
                ).scalar_one()
                source_count = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM source_jds"
                ).scalar_one()
                connection.exec_driver_sql(
                    "INSERT INTO source_jds "
                    "(id, source_platform, source_record_id, created_at, updated_at) "
                    "VALUES ('source-1', 'boss', 'record-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO source_jd_versions "
                    "(id, source_jd_id, source_version, schema_version, raw_text, content_hash, "
                    "raw_payload, crawl_time, text_canonicalization_version, created_at) VALUES "
                    "('version-1', 'source-1', '1', "
                    "'crawler-jd-v1', 'raw', 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'{}', CURRENT_TIMESTAMP, 'raw-v1', CURRENT_TIMESTAMP)"
                )
                with pytest.raises(Exception, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE source_jd_versions SET raw_text = 'changed' "
                        "WHERE id = 'version-1'"
                    )
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert legacy_count == 1
    assert source_count == 0


def test_incremental_migrations_repair_legacy_create_all_hybrid_schema():
    database_path = Path("data") / f"migration_hybrid_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "f3819d64bc82")
        engine = create_engine(database_url)
        try:
            # Historical application startup created tables known before the
            # current taxonomy migration, but create_all could not add columns
            # to existing tables. Future tables must not be injected into the
            # historical schema being simulated here.
            Base.metadata.create_all(
                bind=engine,
                tables=[
                    table
                    for table in Base.metadata.tables.values()
                    if table.name
                        not in {
                            "skill_taxonomy_nodes",
                            "skill_classifications",
                                "trend_report_review_adjustments",
                                "learning_path_records",
                                "review_task_outcomes",
                            }
                ],
            )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        check = _run_alembic(database_url, "check")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            jd_columns = {column["name"] for column in inspector.get_columns("job_descriptions")}
            resume_columns = {column["name"] for column in inspector.get_columns("resumes")}
            evaluation_columns = {
                column["name"] for column in inspector.get_columns("evaluation_reports")
            }
            jd_parse_columns = {
                column["name"] for column in inspector.get_columns("jd_parse_results")
            }
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert {"file_id", "input_extraction_status", "input_error_code"} <= jd_columns
    assert {
        "input_extraction_status",
        "input_error_code",
        "validated_cv_snapshot_id",
        "display_name",
        "original_filename",
    } <= resume_columns
    assert {"evaluation_status", "algorithm_version", "evaluated_count"} <= evaluation_columns
    assert {"schema_version", "normalization_schema_version"} <= jd_parse_columns
    assert "No new upgrade operations detected" in check.stdout


def test_repeated_upgrade_preserves_mixed_v2_v3_schema_versions():
    database_path = Path("data") / f"migration_mixed_versions_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "head")
        engine = create_engine(database_url)
        try:
            with Session(engine) as session:
                v2_jd = JobDescription(
                    source_type="migration_test",
                    title="V2",
                    raw_text="V2 payload",
                )
                v3_jd = JobDescription(
                    source_type="migration_test",
                    title="V3",
                    raw_text="V3 payload",
                )
                session.add_all([v2_jd, v3_jd])
                session.flush()
                session.add_all(
                    [
                        JDParseResult(
                            jd_id=v2_jd.id,
                            schema_version="v2",
                            normalization_schema_version="v2",
                            extraction_result={"schema_version": "v2"},
                            normalized_result={"schema_version": "v2"},
                        ),
                        JDParseResult(
                            jd_id=v3_jd.id,
                            schema_version="v3_complex_reaudit",
                            normalization_schema_version="v3_norm_reaudit",
                            extraction_result={
                                "schema_version": "v3_complex_reaudit"
                            },
                            normalized_result={
                                "schema_version": "v3_norm_reaudit"
                            },
                        ),
                    ]
                )
                session.commit()
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        verification_engine = create_engine(database_url)
        try:
            with Session(verification_engine) as session:
                results = session.query(JDParseResult).order_by(
                    JDParseResult.schema_version
                ).all()
                observed = {
                    (
                        result.schema_version,
                        result.normalization_schema_version,
                        result.extraction_result["schema_version"],
                        result.normalized_result["schema_version"],
                    )
                    for result in results
                }
        finally:
            verification_engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert observed == {
        ("v2", "v2", "v2", "v2"),
        (
            "v3_complex_reaudit",
            "v3_norm_reaudit",
            "v3_complex_reaudit",
            "v3_norm_reaudit",
        ),
    }


@pytest.mark.parametrize(
    ("resume_existing", "jd_existing"),
    [
        ({"input_extraction_status"}, {"file_id"}),
        (
            {
                "input_extraction_status",
                "input_provider",
                "input_error_code",
                "input_error_message",
            },
            {"input_extraction_status", "input_provider"},
        ),
        (
            {"input_error_code"},
            {
                "file_id",
                "input_extraction_status",
                "input_provider",
                "input_error_code",
                "input_error_message",
            },
        ),
    ],
)
def test_input_extraction_migration_recovers_partial_column_states(
    resume_existing: set[str], jd_existing: set[str]
):
    database_path = Path("data") / f"migration_partial_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    ddl = {
        "file_id": "file_id VARCHAR(36)",
        "input_extraction_status": (
            "input_extraction_status VARCHAR(32) NOT NULL DEFAULT 'not_required'"
        ),
        "input_provider": "input_provider VARCHAR(64)",
        "input_error_code": "input_error_code VARCHAR(128)",
        "input_error_message": "input_error_message TEXT",
    }
    try:
        _run_alembic(database_url, "upgrade", "20260712_05")
        partial_engine = create_engine(database_url)
        try:
            with partial_engine.begin() as connection:
                for column in resume_existing:
                    connection.exec_driver_sql(f"ALTER TABLE resumes ADD COLUMN {ddl[column]}")
                for column in jd_existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE job_descriptions ADD COLUMN {ddl[column]}"
                    )
        finally:
            partial_engine.dispose()

        _run_alembic(database_url, "upgrade", "head")
        _run_alembic(database_url, "upgrade", "head")
        current = _run_alembic(database_url, "current")
        heads = _run_alembic(database_url, "heads")
        check = _run_alembic(database_url, "check")
        verification_engine = create_engine(database_url)
        try:
            inspector = inspect(verification_engine)
            resume_columns = {
                column["name"] for column in inspector.get_columns("resumes")
            }
            jd_columns = {
                column["name"] for column in inspector.get_columns("job_descriptions")
            }
            jd_foreign_keys = inspector.get_foreign_keys("job_descriptions")
            table_names = set(inspector.get_table_names())
            cv_task_columns = {
                column["name"]
                for column in inspector.get_columns("cv_extraction_tasks")
            }
            cv_snapshot_columns = {
                column["name"]
                for column in inspector.get_columns("validated_cv_snapshots")
            }
        finally:
            verification_engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    expected_extraction = {
        "input_extraction_status",
        "input_provider",
        "input_error_code",
        "input_error_message",
    }
    assert expected_extraction <= resume_columns
    assert expected_extraction | {"file_id"} <= jd_columns
    assert any(
        foreign_key.get("constrained_columns") == ["file_id"]
        for foreign_key in jd_foreign_keys
    )
    assert "match_reports" not in table_names
    assert {"execution_id", "execution_metadata"} <= cv_task_columns
    assert "execution_metadata" in cv_snapshot_columns
    _assert_alembic_at_head(database_url, current.stdout, heads.stdout)
    assert "No new upgrade operations detected" in check.stdout


def test_match_resume_lineage_migration_downgrade_and_upgrade():
    database_path = Path("data") / f"migration_match_lineage_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260726_30")
        historical_engine = create_engine(database_url)
        try:
            with historical_engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO users "
                    "(id, username, hashed_password, role, is_active, "
                    "created_at, updated_at) VALUES "
                    "('match-user', 'match-user', 'test-only', "
                    "'personal_user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO resumes "
                    "(id, user_id, source_type, raw_text, parse_status, "
                    "created_at, updated_at) VALUES "
                    "('match-resume', 'match-user', 'text', 'resume', "
                    "'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO match_reports "
                    "(id, user_id, resume_id, target_type, target_id, "
                    "use_enterprise_weights, overall_score, radar, "
                    "matched_skills, missing_skills, weak_skills, "
                    "bonus_skills, project_match, explanation, status, "
                    "created_at, updated_at) VALUES "
                    "('historical-report', 'match-user', 'match-resume', "
                    "'standard_position', 'position-1', 0, 0.5, '[]', "
                    "'[]', '[]', '[]', '[]', '{}', '{}', 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            historical_engine.dispose()
        _run_alembic(database_url, "upgrade", "20260726_31")
        upgraded_engine = create_engine(database_url)
        try:
            upgraded = inspect(upgraded_engine)
            assert {
                "validated_cv_snapshot_id",
                "resume_profile_fingerprint",
                "position_profile_fingerprint",
                "algorithm_version",
                "provider",
                "rule_based",
            } <= {
                column["name"] for column in upgraded.get_columns("match_reports")
            }
            assert {
                tuple(item.get("constrained_columns") or ())
                for item in upgraded.get_foreign_keys("match_reports")
            } >= {("resume_id",), ("validated_cv_snapshot_id",)}
            with upgraded_engine.connect() as connection:
                assert connection.exec_driver_sql(
                    "SELECT status FROM match_reports "
                    "WHERE id = 'historical-report'"
                ).scalar_one() == "current"
            for rejected_status in ("completed", "unknown"):
                with pytest.raises(IntegrityError):
                    with upgraded_engine.begin() as connection:
                        connection.exec_driver_sql(
                            "UPDATE match_reports SET status = ? "
                            "WHERE id = 'historical-report'",
                            (rejected_status,),
                        )
        finally:
            upgraded_engine.dispose()

        _run_alembic(database_url, "downgrade", "20260726_30")
        downgraded_engine = create_engine(database_url)
        try:
            downgraded = inspect(downgraded_engine)
            assert "resume_profile_fingerprint" not in {
                column["name"] for column in downgraded.get_columns("match_reports")
            }
        finally:
            downgraded_engine.dispose()

        _run_alembic(database_url, "upgrade", "20260726_31")
        assert "20260726_31" in _run_alembic(
            database_url, "current"
        ).stdout
    finally:
        database_path.unlink(missing_ok=True)


def test_match_lineage_migration_rejects_partial_schema_and_unknown_status():
    for scenario in ("partial_schema", "unknown_status"):
        database_path = (
            Path("data")
            / f"migration_match_invalid_{scenario}_{uuid4().hex}.db"
        )
        database_url = f"sqlite:///{database_path.as_posix()}"
        try:
            _run_alembic(database_url, "upgrade", "20260726_30")
            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    if scenario == "partial_schema":
                        connection.exec_driver_sql(
                            "ALTER TABLE match_reports ADD COLUMN "
                            "resume_profile_fingerprint VARCHAR(71)"
                        )
                    else:
                        connection.exec_driver_sql(
                            "INSERT INTO users "
                            "(id, username, hashed_password, role, is_active, "
                            "created_at, updated_at) VALUES "
                            "('invalid-user', 'invalid-user', 'test-only', "
                            "'personal_user', 1, CURRENT_TIMESTAMP, "
                            "CURRENT_TIMESTAMP)"
                        )
                        connection.exec_driver_sql(
                            "INSERT INTO resumes "
                            "(id, user_id, source_type, raw_text, parse_status, "
                            "created_at, updated_at) VALUES "
                            "('invalid-resume', 'invalid-user', 'text', "
                            "'resume', 'completed', CURRENT_TIMESTAMP, "
                            "CURRENT_TIMESTAMP)"
                        )
                        connection.exec_driver_sql(
                            "INSERT INTO match_reports "
                            "(id, user_id, resume_id, target_type, target_id, "
                            "use_enterprise_weights, overall_score, radar, "
                            "matched_skills, missing_skills, weak_skills, "
                            "bonus_skills, project_match, explanation, status, "
                            "created_at, updated_at) VALUES "
                            "('invalid-report', 'invalid-user', "
                            "'invalid-resume', 'standard_position', "
                            "'position-1', 0, 0.5, '[]', '[]', '[]', '[]', "
                            "'[]', '{}', '{}', 'corrupted', CURRENT_TIMESTAMP, "
                            "CURRENT_TIMESTAMP)"
                        )
            finally:
                engine.dispose()

            env = os.environ.copy()
            env["ENVIRONMENT"] = "test"
            env["ALEMBIC_DATABASE_URL"] = database_url
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "upgrade",
                    "20260726_31",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        finally:
            database_path.unlink(missing_ok=True)

        assert result.returncode != 0
        output = result.stdout + result.stderr
        expected = (
            "lineage schema is incomplete"
            if scenario == "partial_schema"
            else "Unexpected match report status"
        )
        assert expected in output


def test_data_validation_migration_history_repeat_downgrade_and_upgrade():
    database_path = Path("data") / f"migration_validation_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260723_26")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.exec_driver_sql(
                    "INSERT INTO source_jds "
                    "(id, source_platform, source_record_id, created_at, updated_at) "
                    "VALUES ('source-history', 'test', 'record-history', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO source_jd_versions "
                    "(id, source_jd_id, content_hash, schema_version, raw_text, "
                    "raw_payload, crawl_time, text_canonicalization_version, created_at) "
                    "VALUES ('version-history', 'source-history', 'sha256:"
                    + "1" * 64
                    + "', 'crawler-jd-v1', 'raw', '{}', CURRENT_TIMESTAMP, "
                    "'raw-v1', CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO extraction_tasks "
                    "(id, source_jd_version_id, status, provider, request_fingerprint, "
                    "attempt_count, max_attempts, retryable, bundle_payload, "
                    "created_at, updated_at) VALUES "
                    "('extraction-history', 'version-history', 'succeeded', 'test', "
                    "'sha256:"
                    + "2" * 64
                    + "', 1, 3, 0, '{\"bundle\":true}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "20260731_43")
        _run_alembic(database_url, "upgrade", "20260731_43")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            snapshot_foreign_keys = {
                tuple(item["constrained_columns"])
                for item in inspector.get_foreign_keys(
                    "validated_bundle_snapshots"
                )
            }
            task_uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(
                    "data_validation_tasks"
                )
            }
            report_uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(
                    "validation_reports"
                )
            }
            snapshot_uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(
                    "validated_bundle_snapshots"
                )
            }
            snapshot_checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints(
                    "validated_bundle_snapshots"
                )
            }
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.exec_driver_sql(
                    "INSERT INTO data_validation_tasks "
                    "(id, extraction_task_id, source_jd_version_id, "
                    "bundle_fingerprint, policy_version, idempotency_key, status, "
                    "attempt_count, max_attempts, retryable, created_at, updated_at) "
                    "VALUES ('validation-history', 'extraction-history', "
                    "'version-history', 'sha256:"
                    + "3" * 64
                    + "', 'policy-v1', 'validation-task:history', 'succeeded', "
                    "1, 3, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO validation_reports "
                    "(id, data_validation_task_id, conclusion, idempotency_key, "
                    "policy_version, report_payload, created_at) VALUES "
                    "('report-history', 'validation-history', 'warn', "
                    "'validation-report:history', 'policy-v1', "
                    "'{\"conclusion\":\"warn\"}', CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO validated_bundle_snapshots "
                    "(id, validation_report_id, data_validation_task_id, "
                    "extraction_task_id, source_jd_version_id, validation_conclusion, "
                    "bundle_fingerprint, idempotency_key, bundle_payload, "
                    "report_payload, created_at) VALUES "
                    "('snapshot-history', 'report-history', 'validation-history', "
                    "'extraction-history', 'version-history', 'warn', 'sha256:"
                    + "3" * 64
                    + "', 'validated-bundle:history', '{\"bundle\":true}', "
                    "'{\"conclusion\":\"warn\"}', CURRENT_TIMESTAMP)"
                )
                history_lock_version = connection.exec_driver_sql(
                    "SELECT lock_version FROM data_validation_tasks "
                    "WHERE id = 'validation-history'"
                ).scalar_one()
            with engine.begin() as connection:
                with pytest.raises(Exception, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE validated_bundle_snapshots "
                        "SET bundle_fingerprint = 'changed' "
                        "WHERE id = 'snapshot-history'"
                    )
            with engine.begin() as connection:
                with pytest.raises(Exception, match="immutable"):
                    connection.exec_driver_sql(
                        "DELETE FROM validated_bundle_snapshots "
                        "WHERE id = 'snapshot-history'"
                    )
            with engine.begin() as connection:
                with pytest.raises(Exception):
                    connection.exec_driver_sql(
                        "INSERT INTO validated_bundle_snapshots "
                        "(id, validation_report_id, data_validation_task_id, "
                        "extraction_task_id, source_jd_version_id, "
                        "validation_conclusion, bundle_fingerprint, idempotency_key, "
                        "bundle_payload, report_payload, created_at) VALUES "
                        "('snapshot-blocked', 'report-history', "
                        "'validation-history', 'extraction-history', "
                        "'version-history', 'block', 'sha256:"
                        + "3" * 64
                        + "', 'validated-bundle:blocked', '{}', '{}', "
                        "CURRENT_TIMESTAMP)"
                    )
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                with pytest.raises(Exception):
                    connection.exec_driver_sql(
                        "INSERT INTO data_validation_tasks "
                        "(id, extraction_task_id, source_jd_version_id, "
                        "bundle_fingerprint, policy_version, idempotency_key, "
                        "status, attempt_count, max_attempts, retryable, "
                        "created_at, updated_at) VALUES "
                        "('validation-orphan', 'missing-extraction', "
                        "'version-history', 'sha256:"
                        + "4" * 64
                        + "', 'policy-v1', 'validation-task:orphan', 'pending', "
                        "0, 3, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260723_26")
        downgraded_engine = create_engine(database_url)
        try:
            downgraded_tables = set(inspect(downgraded_engine).get_table_names())
        finally:
            downgraded_engine.dispose()
        _run_alembic(database_url, "upgrade", "head")
        upgraded_engine = create_engine(database_url)
        try:
            upgraded_tables = set(inspect(upgraded_engine).get_table_names())
            with upgraded_engine.connect() as connection:
                history_count = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM extraction_tasks "
                    "WHERE id = 'extraction-history'"
                ).scalar_one()
        finally:
            upgraded_engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert {
        ("validation_report_id",),
        ("data_validation_task_id",),
        ("extraction_task_id",),
        ("source_jd_version_id",),
    } <= snapshot_foreign_keys
    assert {
        ("idempotency_key",),
        ("extraction_task_id", "bundle_fingerprint", "policy_version"),
    } <= task_uniques
    assert {
        ("data_validation_task_id",),
        ("idempotency_key",),
    } <= report_uniques
    assert {
        ("validation_report_id",),
        ("idempotency_key",),
    } <= snapshot_uniques
    assert (
        "block"
        not in snapshot_checks[
            "ck_validated_bundle_snapshots_non_blocking"
        ]
    )
    assert "validated_bundle_snapshots" not in downgraded_tables
    assert {
        "data_validation_tasks",
        "validation_reports",
        "validated_bundle_snapshots",
    } <= upgraded_tables
    assert history_count == 1
    assert history_lock_version == 1


def test_sqlite_snapshot_insert_guard_rejects_block_and_forged_lineage():
    database_path = Path("data") / f"migration_validation_guard_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    insert_snapshot_sql = (
        "INSERT INTO validated_bundle_snapshots "
        "(id, validation_report_id, data_validation_task_id, "
        "extraction_task_id, source_jd_version_id, validation_conclusion, "
        "bundle_fingerprint, idempotency_key, bundle_payload, "
        "report_payload, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', CURRENT_TIMESTAMP)"
    )
    try:
        _run_alembic(database_url, "upgrade", "20260731_43")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                for source_id, record_id in (
                    ("source-guard", "record-guard"),
                    ("source-alt", "record-alt"),
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO source_jds "
                        "(id, source_platform, source_record_id, created_at, "
                        "updated_at) VALUES (?, 'test', ?, CURRENT_TIMESTAMP, "
                        "CURRENT_TIMESTAMP)",
                        (source_id, record_id),
                    )
                for version_id, source_id, marker in (
                    ("version-guard", "source-guard", "6"),
                    ("version-alt", "source-alt", "7"),
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO source_jd_versions "
                        "(id, source_jd_id, content_hash, schema_version, "
                        "raw_text, raw_payload, crawl_time, "
                        "text_canonicalization_version, created_at) VALUES "
                        "(?, ?, ?, 'crawler-jd-v1', 'raw', '{}', "
                        "CURRENT_TIMESTAMP, 'raw-v1', CURRENT_TIMESTAMP)",
                        (version_id, source_id, f"sha256:{marker * 64}"),
                    )
                for extraction_id, version_id, marker in (
                    ("extraction-guard", "version-guard", "8"),
                    ("extraction-alt", "version-alt", "9"),
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO extraction_tasks "
                        "(id, source_jd_version_id, status, provider, "
                        "request_fingerprint, attempt_count, max_attempts, "
                        "retryable, bundle_payload, created_at, updated_at) "
                        "VALUES (?, ?, 'succeeded', 'test', ?, 1, 3, 0, "
                        "'{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (
                            extraction_id,
                            version_id,
                            f"sha256:{marker * 64}",
                        ),
                    )
                tasks = (
                    (
                        "task-warn",
                        "extraction-guard",
                        "version-guard",
                        f"sha256:{'a' * 64}",
                        "policy-warn",
                    ),
                    (
                        "task-block",
                        "extraction-guard",
                        "version-guard",
                        f"sha256:{'b' * 64}",
                        "policy-block",
                    ),
                    (
                        "task-alt",
                        "extraction-alt",
                        "version-alt",
                        f"sha256:{'c' * 64}",
                        "policy-alt",
                    ),
                    (
                        "task-inconsistent",
                        "extraction-guard",
                        "version-alt",
                        f"sha256:{'d' * 64}",
                        "policy-inconsistent",
                    ),
                )
                for task in tasks:
                    connection.exec_driver_sql(
                        "INSERT INTO data_validation_tasks "
                        "(id, extraction_task_id, source_jd_version_id, "
                        "bundle_fingerprint, policy_version, idempotency_key, "
                        "status, attempt_count, max_attempts, retryable, "
                        "created_at, updated_at) VALUES "
                        "(?, ?, ?, ?, ?, ?, 'succeeded', 1, 3, 0, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (*task, f"validation-task:{task[0]}"),
                    )
                for report_id, task_id, decision in (
                    ("report-warn", "task-warn", "warn"),
                    ("report-block", "task-block", "block"),
                    ("report-alt", "task-alt", "warn"),
                    ("report-inconsistent", "task-inconsistent", "warn"),
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO validation_reports "
                        "(id, data_validation_task_id, conclusion, "
                        "idempotency_key, policy_version, report_payload, "
                        "created_at) VALUES (?, ?, ?, ?, 'policy', '{}', "
                        "CURRENT_TIMESTAMP)",
                        (
                            report_id,
                            task_id,
                            decision,
                            f"validation-report:{report_id}",
                        ),
                    )

            rejected_snapshots = (
                (
                    "snapshot-block",
                    "report-block",
                    "task-block",
                    "extraction-guard",
                    "version-guard",
                    "pass",
                    f"sha256:{'b' * 64}",
                    "snapshot:block",
                ),
                (
                    "snapshot-report-task",
                    "report-warn",
                    "task-alt",
                    "extraction-alt",
                    "version-alt",
                    "warn",
                    f"sha256:{'c' * 64}",
                    "snapshot:report-task",
                ),
                (
                    "snapshot-decision",
                    "report-warn",
                    "task-warn",
                    "extraction-guard",
                    "version-guard",
                    "pass",
                    f"sha256:{'a' * 64}",
                    "snapshot:decision",
                ),
                (
                    "snapshot-extraction",
                    "report-warn",
                    "task-warn",
                    "extraction-alt",
                    "version-guard",
                    "warn",
                    f"sha256:{'a' * 64}",
                    "snapshot:extraction",
                ),
                (
                    "snapshot-source",
                    "report-warn",
                    "task-warn",
                    "extraction-guard",
                    "version-alt",
                    "warn",
                    f"sha256:{'a' * 64}",
                    "snapshot:source",
                ),
                (
                    "snapshot-fingerprint",
                    "report-warn",
                    "task-warn",
                    "extraction-guard",
                    "version-guard",
                    "warn",
                    f"sha256:{'e' * 64}",
                    "snapshot:fingerprint",
                ),
                (
                    "snapshot-extraction-source",
                    "report-inconsistent",
                    "task-inconsistent",
                    "extraction-guard",
                    "version-alt",
                    "warn",
                    f"sha256:{'d' * 64}",
                    "snapshot:extraction-source",
                ),
            )
            for values in rejected_snapshots:
                with engine.begin() as connection:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    with pytest.raises(Exception, match="admission|lineage"):
                        connection.exec_driver_sql(
                            insert_snapshot_sql, values
                        )

            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.exec_driver_sql(
                    insert_snapshot_sql,
                    (
                        "snapshot-valid",
                        "report-warn",
                        "task-warn",
                        "extraction-guard",
                        "version-guard",
                        "warn",
                        f"sha256:{'a' * 64}",
                        "snapshot:valid",
                    ),
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260723_26")
        downgraded_engine = create_engine(database_url)
        try:
            with downgraded_engine.connect() as connection:
                remaining_guard = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'trigger' AND "
                    "name = 'validated_bundle_snapshots_validate_insert'"
                ).scalar_one()
        finally:
            downgraded_engine.dispose()
        assert remaining_guard == 0
        _run_alembic(database_url, "upgrade", "head")
    finally:
        database_path.unlink(missing_ok=True)


def test_outbox_migration_rejects_an_existing_partial_table() -> None:
    database_path = Path("data") / f"migration_outbox_partial_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260716_17")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE outbox_messages (id VARCHAR(36) PRIMARY KEY)"
                )
        finally:
            engine.dispose()

        env = os.environ.copy()
        env["ENVIRONMENT"] = "test"
        env["ALEMBIC_DATABASE_URL"] = database_url
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        database_path.unlink(missing_ok=True)

    assert result.returncode != 0
    assert "Existing outbox_messages table is incomplete" in (
        result.stdout + result.stderr
    )


def _create_existing_outbox_schema(
    connection,
    *,
    include_event_unique: bool = True,
    include_status_index: bool = True,
    status_nullable: bool = False,
    attempts_type: str = "INTEGER",
) -> None:
    constraints = ["UNIQUE (idempotency_key)"]
    if include_event_unique:
        constraints.append("UNIQUE (event_id)")
    status_nullability = "" if status_nullable else " NOT NULL"
    connection.exec_driver_sql(
        "CREATE TABLE outbox_messages ("
        "id VARCHAR(36) NOT NULL PRIMARY KEY, event_id VARCHAR(36) NOT NULL, "
        "event_type VARCHAR(120) NOT NULL, aggregate_id VARCHAR(120) NOT NULL, "
        "idempotency_key VARCHAR(180) NOT NULL, payload JSON NOT NULL, "
        f"status VARCHAR(24){status_nullability}, attempts {attempts_type} NOT NULL, "
        "next_attempt_at DATETIME NOT NULL, lease_owner VARCHAR(80), "
        "lease_until DATETIME, last_error TEXT, trace_id VARCHAR(64), "
        "occurred_at DATETIME NOT NULL, created_at DATETIME NOT NULL, "
        f"updated_at DATETIME NOT NULL, {', '.join(constraints)})"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_outbox_messages_aggregate_id ON outbox_messages (aggregate_id)"
    )
    if include_status_index:
        connection.exec_driver_sql(
            "CREATE INDEX ix_outbox_messages_status ON outbox_messages (status)"
        )


@pytest.mark.parametrize(
    ("schema_options", "expected_detail"),
    [
        ({"include_event_unique": False}, "missing unique constraints: event_id"),
        ({"include_status_index": False}, "missing indexes: status"),
        ({"status_nullable": True}, "unexpected nullable columns: status"),
        ({"attempts_type": "TEXT"}, "unexpected column types: attempts"),
    ],
)
def test_outbox_migration_rejects_incompatible_existing_schema(
    schema_options, expected_detail
) -> None:
    database_path = Path("data") / f"migration_outbox_invalid_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260716_17")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                _create_existing_outbox_schema(connection, **schema_options)
        finally:
            engine.dispose()

        env = os.environ.copy()
        env["ENVIRONMENT"] = "test"
        env["ALEMBIC_DATABASE_URL"] = database_url
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        database_path.unlink(missing_ok=True)

    assert result.returncode != 0
    assert expected_detail in (result.stdout + result.stderr)


def test_legacy_source_cv_versions_backfill_bypasses_immutability_trigger() -> None:
    database_path = Path("data") / f"cv_backfill_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260805_62")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO source_cvs "
                    "(id, owner_id, source_platform, source_record_id, created_at) "
                    "VALUES ('scv-legacy-1', 'owner-legacy-1', 'platform', 'record', "
                    "'2026-08-05T00:00:00+00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO source_cv_versions "
                    "(id, source_cv_id, raw_text, content_hash, created_at) "
                    "VALUES ('scvv-legacy-1', 'scv-legacy-1', 'legacy raw text', "
                    "'legacy-content-hash', '2026-08-05T00:00:00+00:00')"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                version = connection.exec_driver_sql(
                    "SELECT source_version FROM source_cv_versions "
                    "WHERE id = 'scvv-legacy-1'"
                ).fetchone()
                trigger = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'source_cv_versions_reject_update'"
                ).fetchone()
            assert version is not None and version[0] == "legacy-scvv-legacy-1"
            assert trigger is not None
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_account_token_version_migration_preserves_users_and_downgrades() -> None:
    database_path = Path("data") / f"token_version_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260809_65")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO users "
                    "(id, username, hashed_password, role, is_active, created_at, updated_at) "
                    "VALUES ('token-user', 'token-user', 'test-only', 'personal_user', "
                    "1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "20260809_66")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert "token_version" in {
                column["name"] for column in inspector.get_columns("users")
            }
            with engine.connect() as connection:
                assert connection.exec_driver_sql(
                    "SELECT token_version FROM users WHERE id = 'token-user'"
                ).scalar_one() == 0
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260809_65")
        engine = create_engine(database_url)
        try:
            assert "token_version" not in {
                column["name"] for column in inspect(engine).get_columns("users")
            }
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_candidate_decision_rationale_migration_preserves_decisions() -> None:
    database_path = Path("data") / f"candidate_rationale_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260811_67")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO users "
                    "(id, username, hashed_password, role, is_active, created_at, updated_at) "
                    "VALUES ('decision-user', 'decision-user', 'test-only', "
                    "'enterprise_user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO enterprises "
                    "(id, owner_user_id, enterprise_name, status, created_at, updated_at) "
                    "VALUES ('enterprise-1', 'decision-user', 'Enterprise', 'active', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO enterprise_jobs "
                    "(id, enterprise_id, title, headcount, status, created_at, updated_at) "
                    "VALUES ('job-1', 'enterprise-1', 'Engineer', 1, 'published', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO resumes "
                    "(id, user_id, source_type, raw_text, display_name, parse_status, "
                    "created_at, updated_at) "
                    "VALUES ('resume-1', 'decision-user', 'text', '', 'Candidate', 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO candidate_decisions "
                    "(id, enterprise_job_id, resume_id, decision, decided_by, "
                    "evaluation_id, task_id, algorithm_version, created_at, updated_at) "
                    "VALUES ('decision-1', 'job-1', 'resume-1', 'fit', 'decision-user', "
                    "'eval-1', 'task-1', 'matching-v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "20260812_68")
        engine = create_engine(database_url)
        try:
            columns = {
                column["name"]
                for column in inspect(engine).get_columns("candidate_decisions")
            }
            assert {"reason_code", "reason_text"} <= columns
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT evaluation_id, task_id, algorithm_version, reason_code, reason_text "
                    "FROM candidate_decisions WHERE id = 'decision-1'"
                ).one()
                assert tuple(row) == ("eval-1", "task-1", "matching-v1", None, None)
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260811_67")
        engine = create_engine(database_url)
        try:
            columns = {
                column["name"]
                for column in inspect(engine).get_columns("candidate_decisions")
            }
            assert "reason_code" not in columns
            assert "reason_text" not in columns
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_enterprise_job_salary_unit_migration_backfills_existing_rows_and_downgrades() -> None:
    database_path = Path("data") / f"salary_unit_{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    try:
        _run_alembic(database_url, "upgrade", "20260829_79")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO users "
                    "(id, username, hashed_password, role, is_active, created_at, updated_at) "
                    "VALUES ('salary-user', 'salary-user', 'test-only', "
                    "'enterprise_user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO enterprises "
                    "(id, owner_user_id, enterprise_name, status, created_at, updated_at) "
                    "VALUES ('enterprise-salary', 'salary-user', 'Enterprise', 'active', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO enterprise_jobs "
                    "(id, enterprise_id, title, headcount, salary_min, salary_max, "
                    "status, created_at, updated_at) "
                    "VALUES ('job-salary', 'enterprise-salary', 'Engineer', 1, "
                    "15000, 25000, 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "20260830_80")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            columns = {
                column["name"]
                for column in inspector.get_columns("enterprise_jobs")
            }
            assert "salary_unit" in columns
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT salary_min, salary_max, salary_unit "
                    "FROM enterprise_jobs WHERE id = 'job-salary'"
                ).one()
                assert tuple(row) == (15000, 25000, "month")
                create_table_sql = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'enterprise_jobs'"
                ).one()
                assert "ck_enterprise_jobs_salary_unit_allowed" in create_table_sql[0]
        finally:
            engine.dispose()

        _run_alembic(database_url, "downgrade", "20260829_79")
        engine = create_engine(database_url)
        try:
            columns = {
                column["name"]
                for column in inspect(engine).get_columns("enterprise_jobs")
            }
            assert "salary_unit" not in columns
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)
