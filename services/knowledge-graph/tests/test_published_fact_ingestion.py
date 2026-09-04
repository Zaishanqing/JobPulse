from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.application import ImportPublishedJDFactUseCase
from app.application.contracts import ImportPublishedJDFactCommand
from app.api.fact_mappers import published_jd_fact
from app.domain.structured_facts import PublishedFactImportResult
from app.domain.published_facts import PublishedFactValidationFacts
from app.auth import create_token, hash_password
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from app.database import Base
from app.main import app
from app.models import (
    JDDocument, PublishedFactImport, Skill, StandardPosition, User,
)
from jobgraph_contracts.published_jd import PublishedJDFactV3


def published_fact(version="2026-07-16T10:00:00+00:00"):
    evidence = {
        "source_id": "JD_AUTH_1", "quote": "Python", "start": 0, "end": 6,
        "alignment": "exact", "occurrence_index": 0,
    }
    payload = {
        "contract_version": "published-jd-fact.v3", "schema_version": "v2",
        "source_system": "main-system", "source_jd_id": "JD_AUTH_1",
        "source_fact_id": "FACT_1", "source_fact_version": version,
        "review_status": "published", "published_at": version,
        "position_fact": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "后端工程师",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件工程与研发",
            "candidate_positions": [
                {"position_code": "BACKEND_ENGINEER", "score": 0.93}
            ],
            "career_level": "mid",
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.93,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": ["r1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "skill_facts": [{"skill_id": "SK_PY"}],
        "requirement_facts": [{"requirement_id": "r1"}],
        "education_fact": None, "experience_fact": None, "industry_fact": None,
        "company_facts": [], "employment_facts": [], "evidence": [evidence],
        "extraction_fact": {
            "schema_version": "v2", "document_id": "JD_AUTH_1",
            "job_title": None, "responsibilities": [],
            "requirements": [{
                "requirement_id": "r1", "kind": "skill", "modality": "required",
                "evidence": evidence,
                "items": [{"name": "Python", "item_type": "language"}],
                "proficiency": None,
            }],
            "company_facts": [], "employment_facts": [],
        },
        "normalized_fact": {
            "schema_version": "v2", "document_id": "JD_AUTH_1",
            "job_classification": {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "source_title": "后端工程师",
                "position_code": "BACKEND_ENGINEER",
                "position_name": "后端工程师",
                "family_code": "SOFTWARE_ENGINEERING",
                "family_name": "软件工程与研发",
                "candidate_positions": [
                    {"position_code": "BACKEND_ENGINEER", "score": 0.93}
                ],
                "career_level": "mid",
                "leadership_scope": None,
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": ["software_engineering"],
                "confidence": 0.93,
                "classification_status": "resolved",
                "review_reason_codes": [],
                "evidence_refs": ["r1"],
                "classification_policy_version": "position-classifier.v3.0",
            },
            "normalized_requirements": [{
                "requirement_id": "r1", "kind": "skill",
                "normalized_skills": [{
                    "source_name": "Python", "skill_id": "SK_PY",
                    "canonical_name": "Python", "category_code": "TECH",
                    "subcategory_code": None, "resolution_status": "resolved",
                    "resolution_source": "explicit_mapping",
                }],
            }],
            "salary": None, "unresolved_items": [],
        },
        "trace_metadata": {
            "source_type": "api", "source_name": "main", "enterprise_id": "E1",
            "source_observed_at": "2026-07-29T00:00:00+00:00",
        },
        "validation_lineage": {
            "state": "present",
            "data_validation_task_id": "DVT_1",
            "validation_report_id": "DVR_1",
            "validated_bundle_snapshot_id": "VBS_1",
            "validation_policy_version": "validation-policy.v1",
            "validation_conclusion": "pass",
            "absent_reason": None,
        },
        "skill_catalog_snapshot": {
            "source": "main-system-skill-catalog",
            "catalog_version": "skill-taxonomy.v2.0.0",
            "content_hash": "a1b2c3d4" * 8,
            "effective_at": version,
            "status": "active",
        },
        "position_catalog_snapshot": {
            "source": "main-system-position-catalog",
            "catalog_version": "position-taxonomy.v3.0.0",
            "content_hash": "a1b2c3d4" * 8,
            "effective_at": version,
            "status": "active",
        },
    }
    return payload


def published_command(payload=None):
    value = payload or published_fact()
    return ImportPublishedJDFactCommand(
        published_jd_fact(PublishedJDFactV3.model_validate(value))
    )


def service_headers(db):
    user = User(
        username=app.state.settings.service_username,
        password_hash=hash_password("service-secret"), role="integration_service",
    )
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {create_token(user, app.state.settings)}"}


def test_application_use_case_runs_with_pure_fake_uow():
    class Repository:
        def load_validation_facts(self, fact, lineage):
            return PublishedFactValidationFacts(fact, None, None, lineage)

        def save_import_plan(self, plan):
            return plan.fact.source_jd_id

    class Audits:
        def record(self, _record):
            pass

    class Uow:
        committed = False
        published_facts = Repository()
        audits = Audits()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def commit(self):
            self.committed = True

    uow = Uow()
    result = ImportPublishedJDFactUseCase(lambda: uow).execute(
        published_command()
    )
    assert result.source_fact_id == "FACT_1"
    assert uow.committed is True


def test_application_import_boundary_has_no_framework_or_infrastructure_imports():
    for relative in ("app/application/contracts.py", "app/application/use_cases.py"):
        tree = ast.parse(Path(relative).read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert not any(name.startswith((
            "fastapi", "sqlalchemy", "app.api", "app.models", "app.config",
            "app.infrastructure",
        )) for name in imports)


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("contract_version"),
    lambda value: value.update(contract_version="published-jd-fact.v9"),
    lambda value: value.update(schema_version="v1"),
    lambda value: value.update(review_status="reviewed"),
])
def test_contract_rejects_missing_unknown_incompatible_and_reviewed(
    client, db, mutation,
):
    payload = published_fact()
    mutation(payload)
    response = client.post(
        "/api/v3/integrations/published-jd-facts", json=payload,
        headers=service_headers(db),
    )
    assert response.status_code == 422
    assert response.json()["message"] == "request validation failed"


def test_contract_rejects_invalid_normalized_position_domain_at_http_boundary(
    client, db
):
    payload = published_fact()
    payload["normalized_fact"]["job_classification"][
        "observed_skill_domain_codes"
    ] = ["SOFTWARE_ENGINEERING"]

    response = client.post(
        "/api/v3/integrations/published-jd-facts",
        json=payload,
        headers=service_headers(db),
    )

    assert response.status_code == 422
    assert response.json()["message"] == "request validation failed"


def test_versioned_import_is_idempotent_and_legacy_writes_are_blocked(client, db):
    headers = service_headers(db)
    payload = published_fact()
    first = client.post(
        "/api/v3/integrations/published-jd-facts", json=payload, headers=headers
    )
    second = client.post(
        "/api/v3/integrations/published-jd-facts", json=payload, headers=headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["idempotent"] is False
    assert second.json()["data"]["idempotent"] is True
    assert db.scalar(select(PublishedFactImport).where(
        PublishedFactImport.source_fact_id == "FACT_1"
    )) is not None
    document = db.scalar(select(JDDocument).where(
        JDDocument.document_id == "JD_AUTH_1"
    ))
    assert document.fact_authority == "authoritative"
    assert document.raw_text == ""
    assert document.published_at.isoformat().startswith("2026-07-29T00:00:00")
    imported = db.scalar(select(PublishedFactImport).where(
        PublishedFactImport.source_fact_id == "FACT_1"
    ))
    assert imported.published_at.isoformat().startswith("2026-07-16T10:00:00")
    overwrite = client.put(
        "/api/v1/integrations/jds/JD_AUTH_1",
        json={"raw_text": "legacy overwrite"}, headers=headers,
    )
    assert overwrite.status_code == 409
    assert overwrite.json()["details"]["error_code"] == "AUTHORITATIVE_FACT_WRITE_PROTECTED"


def test_same_source_version_with_different_content_conflicts(client, db):
    headers = service_headers(db)
    payload = published_fact()
    assert client.post(
        "/api/v3/integrations/published-jd-facts", json=payload, headers=headers
    ).status_code == 200
    changed = deepcopy(payload)
    changed["skill_facts"][0]["canonical_name"] = "Python 3"
    response = client.post(
        "/api/v3/integrations/published-jd-facts", json=changed, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["details"]["error_code"] == "PUBLISHED_FACT_CONTENT_CONFLICT"


def test_newer_then_older_fact_never_rolls_back_projection(client, db):
    headers = service_headers(db)
    newer = published_fact("2026-07-16T11:00:00+00:00")
    older = published_fact("2026-07-16T10:00:00+00:00")
    assert client.post(
        "/api/v3/integrations/published-jd-facts", json=newer, headers=headers
    ).status_code == 200
    response = client.post(
        "/api/v3/integrations/published-jd-facts", json=older, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["stale"] is True
    document = db.scalar(select(JDDocument).where(
        JDDocument.document_id == "JD_AUTH_1"
    ))
    assert document.source_fact_version == newer["source_fact_version"]
    assert db.scalars(select(PublishedFactImport)).all().__len__() == 1


def test_invalid_or_naive_fact_version_is_rejected(client, db):
    headers = service_headers(db)
    for version in ("not-a-version", "2026-07-16T10:00:00"):
        payload = published_fact()
        payload["source_fact_version"] = version
        response = client.post(
            "/api/v3/integrations/published-jd-facts", json=payload, headers=headers
        )
        assert response.status_code == 422
        assert response.json()["message"] == "request validation failed"


def test_real_uow_rolls_back_all_projection_rows_when_commit_fails(db):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    failing_session = factory()

    @event.listens_for(failing_session, "before_commit")
    def fail_commit(_session):
        raise RuntimeError("injected commit failure")

    def uow_factory():
        return SqlAlchemyUnitOfWork(lambda: failing_session, close_session=False)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        ImportPublishedJDFactUseCase(uow_factory).execute(
            published_command()
        )
    failing_session.rollback()
    assert db.scalar(select(PublishedFactImport)) is None
    assert db.scalar(select(JDDocument).where(
        JDDocument.document_id == "JD_AUTH_1"
    )) is None


def _file_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'published-facts.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _import_with_factory(factory, payload):
    return ImportPublishedJDFactUseCase(
        lambda: SqlAlchemyUnitOfWork(factory)
    ).execute(published_command(payload))


def test_same_source_version_concurrent_import_creates_one_ledger(tmp_path):
    engine, factory = _file_session_factory(tmp_path)
    payload = published_fact("2026-07-16T11:00:00+00:00")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: _import_with_factory(factory, payload), range(2)))
        assert len([item for item in results if item.idempotent is False]) == 1
        assert len([item for item in results if item.idempotent is True]) == 1
        with factory() as session:
            assert session.scalars(select(PublishedFactImport)).all().__len__() == 1
            document = session.scalar(select(JDDocument).where(
                JDDocument.document_id == "JD_AUTH_1"
            ))
            assert document.source_fact_version == payload["source_fact_version"]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_newer_and_older_concurrent_import_final_projection_is_newer(tmp_path):
    engine, factory = _file_session_factory(tmp_path)
    newer = published_fact("2026-07-16T11:00:00+00:00")
    older = published_fact("2026-07-16T10:00:00+00:00")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda payload: _import_with_factory(factory, payload), [older, newer]))
        with factory() as session:
            document = session.scalar(select(JDDocument).where(
                JDDocument.document_id == "JD_AUTH_1"
            ))
            assert document.source_fact_version == newer["source_fact_version"]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_formal_build_reads_authoritative_projection_only(client, db, monkeypatch):
    from app.domain.policies import EvidenceAligner
    from app.infrastructure.providers.normalization import Normalizer

    def forbidden(*_args, **_kwargs):
        raise AssertionError("formal graph build must not extract or normalize again")

    monkeypatch.setattr(EvidenceAligner, "align", forbidden)
    monkeypatch.setattr(Normalizer, "normalize", forbidden)
    headers = service_headers(db)
    db.add_all([
        StandardPosition(
            position_id="BACKEND_ENGINEER",
            position_code="BACKEND_ENGINEER",
            name="后端工程师",
            category_code="TECH",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
            status="active",
        ),
        Skill(
            skill_id="SK_PY", canonical_name="Python", category_code="TECH",
            status="active",
        ),
        JDDocument(document_id="LEGACY_1", raw_text="legacy"),
        JDDocument(
            document_id="OTHER_AUTH", raw_text="", source_system="other-system",
            fact_authority="authoritative", source_fact_id="OTHER_FACT",
            source_fact_version="2026-07-16T12:00:00+00:00",
        ),
    ])
    db.commit()
    assert client.post(
        "/api/v3/integrations/published-jd-facts", json=published_fact(),
        headers=headers,
    ).status_code == 200
    response = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["included_samples"] == 1
    from app.models import GraphBuildRun, GraphBuildSample
    run = db.get(GraphBuildRun, data["build_run_id"])
    samples = db.scalars(select(GraphBuildSample).where(
        GraphBuildSample.build_run_id == run.id
    )).all()
    assert run.config_snapshot["fact_source_mode"] == "authoritative_main_system"
    assert [sample.document_id for sample in samples] == ["JD_AUTH_1"]
