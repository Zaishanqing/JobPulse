import os
import json
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.sqlalchemy.fact_mappers import load_structured_extraction
from app.database import Base
from scripts.schema_fingerprint import describe_schema


ROOT = Path(__file__).parents[1]


def _alembic(url: str, *arguments: str, expect_success: bool = True):
    environment = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments], cwd=ROOT,
        env=environment, capture_output=True, text=True,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


def _columns(connection, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(connection).get_columns(table)}


def _foreign_keys(connection, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item["constrained_columns"])
        for item in sa.inspect(connection).get_foreign_keys(table)
    }


def _unique_columns(connection, table: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(connection)
    result = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table)
    }
    result.update(
        tuple(item["column_names"])
        for item in inspector.get_indexes(table)
        if item.get("unique")
    )
    return result


def test_0001_is_frozen_at_its_historical_boundary_and_0002_owns_its_delta(tmp_path):
    url = f"sqlite:///{tmp_path / 'historical-boundary.db'}"
    _alembic(url, "upgrade", "0001_initial")

    engine = create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert not inspector.has_table("extracted_job_titles")
        assert _columns(connection, "graph_versions") == {
            "id", "created_at", "position_id", "version_number", "snapshot",
            "algorithm_version", "normalization_map_version",
            "rollback_from_version_id", "published_by",
        }
        assert _foreign_keys(connection, "graph_versions") == {
            ("position_id",), ("rollback_from_version_id",),
        }
        assert not _unique_columns(connection, "graph_versions")
        assert _columns(connection, "graph_build_runs").isdisjoint({
            "base_version_id", "active_draft_key",
        })
        assert _columns(connection, "unresolved_normalization_items").isdisjoint({
            "reviewer_id", "reviewed_at", "review_reason",
        })
        assert _columns(connection, "position_skill_supports").isdisjoint({
            "source_requirement_id", "extraction_record_id",
        })
        assert _foreign_keys(connection, "position_skill_supports") == {
            ("build_run_id",),
        }
        assert _columns(connection, "position_skill_relation_drafts").isdisjoint({
            "status", "revision",
        })
        assert _columns(connection, "review_tasks").isdisjoint({"build_run_id"})
        assert _columns(connection, "normalized_skill_records").isdisjoint({
            "resolution_source",
        })
        assert not connection.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1"
        )).first()
    engine.dispose()

    _alembic(url, "upgrade", "0002_trusted_graph_workflow")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert {
            "build_run_id", "version_name", "content_hash", "published_at",
        } <= _columns(connection, "graph_versions")
        build_column = next(
            item for item in sa.inspect(connection).get_columns("graph_versions")
            if item["name"] == "build_run_id"
        )
        assert build_column["nullable"] is True
        assert {("build_run_id",), ("published_by",)} <= _foreign_keys(
            connection, "graph_versions"
        )
        assert {
            ("position_id", "version_number"),
            ("position_id", "version_name"),
        } <= _unique_columns(connection, "graph_versions")
        assert "enterprise_name" in _columns(connection, "jd_documents")
        assert {
            "reviewer_id", "reviewed_at", "review_reason",
        } <= _columns(connection, "unresolved_normalization_items")
    engine.dispose()

    source = (ROOT / "alembic" / "versions" / "0001_initial.py").read_text()
    assert "Base.metadata" not in source
    assert "from app" not in source


def test_old_chain_upgrade_and_fixed_baseline_have_identical_schema(tmp_path):
    old = f"sqlite:///{tmp_path / 'old.db'}"
    upgraded = f"sqlite:///{tmp_path / 'upgraded.db'}"
    baseline = f"sqlite:///{tmp_path / 'baseline.db'}"
    _alembic(old, "upgrade", "head")
    _alembic(upgraded, "upgrade", "0004_graph_version_database_immutability")
    _alembic(upgraded, "upgrade", "head")
    _alembic(baseline, "-c", "alembic_baseline.ini", "upgrade", "head")
    _alembic(baseline, "stamp", "--purge", "0013_k0_governance_effects")
    _alembic(baseline, "upgrade", "head")
    values = [describe_schema(url) for url in (old, upgraded, baseline)]
    assert values[0] == values[1] == values[2]
    assert sorted(item["name"] for item in values[0]["$triggers"]) == sorted([
        "trg_build_input_watermarks_reject_delete",
        "trg_build_input_watermarks_reject_update",
        "trg_dependency_analysis_runs_reject_delete",
        "trg_dependency_analysis_runs_reject_update",
        "trg_dependency_candidates_reject_delete",
        "trg_dependency_candidates_reject_update",
        "trg_dependency_review_decisions_reject_delete",
        "trg_dependency_review_decisions_reject_update",
        "trg_effective_mapping_records_reject_delete",
        "trg_effective_mapping_records_reject_update",
        "trg_extraction_payload_reject_update",
        "trg_graph_version_dependencies_reject_delete",
        "trg_graph_version_dependencies_reject_update",
        "trg_graph_versions_reject_delete", "trg_graph_versions_reject_update",
        "trg_mapping_review_decisions_reject_delete",
        "trg_mapping_review_decisions_reject_update",
        "trg_projection_manifests_reject_delete",
        "trg_projection_manifests_reject_update",
        "trg_published_fact_lineages_reject_delete",
        "trg_published_fact_lineages_reject_update",
        "trg_published_fact_release_links_reject_delete",
        "trg_published_fact_release_links_reject_update",
        "trg_relation_claims_reject_delete",
        "trg_relation_claims_reject_update",
        "trg_release_import_batches_reject_delete",
        "trg_release_import_batches_reject_update",
        "trg_release_import_items_reject_delete",
        "trg_release_import_items_reject_update",
    ])
    assert all(item["sql"] for item in values[0]["$triggers"])
    engine = create_engine(baseline)
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT version_num FROM alembic_version"
        )).scalar_one() == "0026_review_decision_effects"
    engine.dispose()
    assert all(
        schema[table]["primary_key"]
        for schema in values
        for table in schema
        if not table.startswith("$")
    )


def test_head_matches_current_orm_schema_fingerprint(tmp_path):
    migrated = f"sqlite:///{tmp_path / 'migrated.db'}"
    orm = f"sqlite:///{tmp_path / 'orm.db'}"
    _alembic(migrated, "upgrade", "head")
    engine = create_engine(orm)
    Base.metadata.create_all(engine)
    engine.dispose()

    migrated_schema = describe_schema(migrated)
    orm_schema = describe_schema(orm)
    migrated_tables = {
        name: value for name, value in migrated_schema.items()
        if not name.startswith("$")
    }
    orm_tables = {
        name: value for name, value in orm_schema.items()
        if not name.startswith("$")
    }
    assert migrated_tables == orm_tables


def test_relation_insight_columns_are_migrated(tmp_path):
    url = f"sqlite:///{tmp_path / 'relation-insights.db'}"
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    try:
        columns = {
            item["name"]
            for item in inspect(engine).get_columns(
                "position_skill_relation_drafts"
            )
        }
        assert {"statistics", "explanation"} <= columns
    finally:
        engine.dispose()


def test_0010_is_forward_only_and_preserves_lineage_on_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'forward-only.db'}"
    _alembic(url, "upgrade", "0008_graph_edit_concurrency")
    _alembic(url, "upgrade", "0010_published_fact_lineage")
    _alembic(url, "upgrade", "0010_published_fact_lineage")

    engine = create_engine(url)
    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        assert inspector.has_table("published_fact_imports")
        document_columns = {item["name"] for item in inspector.get_columns("jd_documents")}
        assert {
            "source_system", "fact_authority", "source_fact_id",
            "source_fact_version", "source_schema_version", "content_hash",
        } <= document_columns
        connection.execute(text(
            "INSERT INTO jd_documents "
            "(id, document_id, raw_text, source_type, source_credibility, "
            "is_synthetic, created_at, source_system, fact_authority, "
            "source_fact_id, source_fact_version, source_schema_version, content_hash) "
            "VALUES "
            "(1, 'JD_FORWARD_ONLY', '', 'authoritative_import', 1.0, 0, "
            "CURRENT_TIMESTAMP, 'main-system', 'authoritative', 'FACT_FORWARD', "
            "'1', 'v2', :hash)"
        ), {"hash": "f" * 64})
        connection.execute(text(
            "INSERT INTO published_fact_imports "
            "(id, created_at, source_system, source_fact_id, source_fact_version, "
            "source_schema_version, content_hash, document_id, published_at, payload) "
            "VALUES "
            "(1, CURRENT_TIMESTAMP, 'main-system', 'FACT_FORWARD', '1', "
            "'v2', :hash, 'JD_FORWARD_ONLY', CURRENT_TIMESTAMP, '{}')"
        ), {"hash": "f" * 64})
        connection.execute(text(
            "INSERT INTO published_fact_lineages "
            "(id, created_at, published_fact_import_id, lineage_fingerprint, "
            "validation_conclusion, catalog_status) VALUES "
            "(1, CURRENT_TIMESTAMP, 1, :fingerprint, 'pass', 'active')"
        ), {"fingerprint": "a" * 64})
    engine.dispose()


    failed = _alembic(
        url, "downgrade", "0009_authoritative_published_facts",
        expect_success=False,
    )
    assert failed.returncode != 0
    assert "Migration 0010 is forward-only and cannot be downgraded" in (
        failed.stderr + failed.stdout
    )

    engine = create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert inspector.has_table("published_fact_imports")
        assert inspector.has_table("published_fact_lineages")
        document_columns = {item["name"] for item in inspector.get_columns("jd_documents")}
        assert "fact_authority" in document_columns
        assert connection.execute(text(
            "SELECT fact_authority FROM jd_documents WHERE document_id = 'JD_FORWARD_ONLY'"
        )).scalar_one() == "authoritative"
        assert connection.execute(text(
            "SELECT COUNT(*) FROM published_fact_imports "
            "WHERE source_fact_id = 'FACT_FORWARD'"
        )).scalar_one() == 1
        assert connection.execute(text(
            "SELECT COUNT(*) FROM published_fact_lineages "
            "WHERE lineage_fingerprint = :fingerprint"
        ), {"fingerprint": "a" * 64}).scalar_one() == 1
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE published_fact_lineages "
                "SET catalog_status = 'inactive' WHERE id = 1"
            ))
        assert connection.execute(text(
            "SELECT version_num FROM alembic_version"
        )).scalar_one() == "0010_published_fact_lineage"
    engine.dispose()


def test_0011_is_forward_only_and_installs_innovation_guards(tmp_path):
    url = f"sqlite:///{tmp_path / 'innovation-forward-only.db'}"
    _alembic(url, "upgrade", "0011_traceskill_innovation_planes")
    engine = create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert {
            "build_input_watermarks",
            "relation_claims",
            "mapping_candidates",
            "mapping_review_decisions",
            "dependency_analysis_runs",
            "dependency_candidates",
            "projection_manifests",
        } <= set(inspector.get_table_names())
    engine.dispose()
    failed = _alembic(
        url, "downgrade", "0010_published_fact_lineage", expect_success=False
    )
    assert failed.returncode != 0
    assert "Migration 0011 is forward-only and cannot be downgraded" in (
        failed.stderr + failed.stdout
    )


def test_0009_postgresql_offline_sql_can_be_generated():
    result = _alembic(
        "postgresql://user:password@localhost/jobgraph",
        "upgrade",
        "0008_graph_edit_concurrency:0009_authoritative_published_facts",
        "--sql",
    )
    assert "CREATE TABLE published_fact_imports" in result.stdout
    assert "ALTER TABLE jd_documents ADD COLUMN source_system" in result.stdout


def test_0010_postgresql_offline_sql_contains_lineage_guards():
    result = _alembic(
        "postgresql://user:password@localhost/jobgraph",
        "upgrade",
        "0009_authoritative_published_facts:0010_published_fact_lineage",
        "--sql",
    )
    assert "CREATE TABLE published_fact_lineages" in result.stdout
    assert "reject_published_fact_lineage_mutation" in result.stdout
    assert "trg_published_fact_lineages_reject_update" in result.stdout
    assert "trg_published_fact_lineages_reject_delete" in result.stdout


def test_0005_sample_data_upgrade_preserves_structured_edits_and_audit_payload(tmp_path):
    path = tmp_path / "sample-upgrade.db"
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0005_core_relationship_constraints")
    audit_payload = {
        "document_id": "LEGACY1",
        "job_title": {
            "text": "后端工程师",
            "evidence": {"source_id": "LEGACY1", "quote": "后端工程师"},
        },
        "responsibilities": [{
            "requirement_id": "t1", "text": "开发服务",
            "evidence": {"source_id": "LEGACY1", "quote": "开发服务"},
        }],
        "requirements": [{
            "requirement_id": "r1", "kind": "skill", "modality": "required",
            "evidence": {"source_id": "LEGACY1", "quote": "熟悉 Python"},
            "items": [{"name": "AuditSkill"}],
        }],
        "company_facts": [{
            "fact_id": "c1", "text": "科技企业",
            "evidence": {"source_id": "LEGACY1", "quote": "科技企业"},
        }],
        "employment_facts": [{
            "fact_id": "e1", "fact_type": "location", "text": "上海",
            "evidence": {"source_id": "LEGACY1", "quote": "上海"},
        }],
    }
    structured = {
        **audit_payload["requirements"][0],
        "items": [{"name": "StructuredSkill"}],
    }
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO jd_documents "
            "(id, document_id, raw_text, source_type, source_credibility, "
            "is_synthetic, created_at) VALUES "
            "(1, 'LEGACY1', '后端工程师 熟悉 Python', 'legacy', 1.0, 0, CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO jd_extraction_records "
            "(id, document_id, payload, status, confirmed, created_at) VALUES "
            "(1, 'LEGACY1', :payload, 'aligned', 1, CURRENT_TIMESTAMP)"
        ), {"payload": json.dumps(audit_payload, ensure_ascii=False)})
        connection.execute(text(
            "INSERT INTO extracted_candidate_requirements "
            "(id, document_id, requirement_id, kind, modality, payload, created_at) "
            "VALUES (1, 'LEGACY1', 'r1', 'skill', 'required', :payload, CURRENT_TIMESTAMP)"
        ), {"payload": json.dumps(structured, ensure_ascii=False)})
        connection.execute(text(
            "INSERT INTO extraction_evidence "
            "(id, document_id, owner_type, owner_ref, quote, alignment, created_at) "
            "VALUES (1, 'LEGACY1', 'skill', 'r1', '熟悉 Python', 'unresolved', CURRENT_TIMESTAMP)"
        ))
    engine.dispose()
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    with Session(engine) as session:
        result = load_structured_extraction(session, "LEGACY1")
        assert result.job_title.text == "后端工程师"
        assert result.responsibilities[0].text == "开发服务"
        assert result.requirements[0].items[0].name == "StructuredSkill"
        assert result.company_facts[0].text == "科技企业"
        assert result.employment_facts[0].text == "上海"
        stored_audit = session.execute(text(
            "SELECT payload FROM jd_extraction_records WHERE id = 1"
        )).scalar_one()
        assert json.loads(stored_audit)["requirements"][0]["items"][0]["name"] == "AuditSkill"
        with pytest.raises(IntegrityError):
            session.execute(text(
                "UPDATE jd_extraction_records SET payload = '{}' WHERE id = 1"
            ))
            session.commit()
        session.rollback()
    engine.dispose()

def test_0008_repairs_duplicate_and_null_version_build_links(tmp_path):
    path = tmp_path / "duplicate-publications.db"
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0007_resolution_source")
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("graph_versions") as batch:
            batch.alter_column(
                "build_run_id", existing_type=sa.Integer(), nullable=True
            )
        connection.execute(text(
            "INSERT INTO standard_positions "
            "(id, position_id, name, category_code, status, created_at) VALUES "
            "(1, 'POS_MIGRATION', '迁移岗位', 'TECH', 'active', CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO graph_build_runs "
            "(id, position_id, status, config_snapshot, summary, created_at) VALUES "
            "(1, 'POS_MIGRATION', 'succeeded', '{}', '{}', CURRENT_TIMESTAMP)"
        ))
        for version_id, build_run_id in ((1, 1), (2, 1), (3, None)):
            connection.execute(text(
                "INSERT INTO graph_versions "
                "(id, position_id, build_run_id, version_number, version_name, "
                "snapshot, content_hash, algorithm_version, normalization_map_version, "
                "published_at, created_at) VALUES "
                "(:id, 'POS_MIGRATION', :build, :number, :name, '{}', :hash, "
                "'rule-v1', 'map-v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "id": version_id,
                "build": build_run_id,
                "number": version_id,
                "name": f"v{version_id}",
                "hash": str(version_id) * 64,
            })
    engine.dispose()

    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT id, build_run_id FROM graph_versions ORDER BY id"
        )).all()
        assert rows[0].build_run_id == 1
        assert len({row.build_run_id for row in rows}) == 3
        assert all(row.build_run_id is not None for row in rows)
        assert connection.execute(text(
            "SELECT COUNT(*) FROM graph_build_runs WHERE status = 'published'"
        )).scalar_one() == 3
        build_column = next(
            item for item in sa.inspect(connection).get_columns("graph_versions")
            if item["name"] == "build_run_id"
        )
        assert build_column["nullable"] is False
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO graph_versions "
                "(position_id, build_run_id, version_number, version_name, snapshot, "
                "source_version, algorithm_version, normalization_map_version, "
                "published_at, created_at) VALUES "
                "('POS_MIGRATION', NULL, 4, 'v4', '{}', :hash, 'rule-v1', "
                "'map-v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"hash": "source-v4"})
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE graph_versions SET version_name = 'changed' WHERE id = 1"
            ))
    engine.dispose()
