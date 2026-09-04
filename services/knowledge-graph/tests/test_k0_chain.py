from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.fact_mappers import published_jd_fact
from app.application.contracts import ImportPublishedJDFactCommand, ImportReleaseCommand
from app.application.lineage_mapper import map_published_fact_lineage
from app.application.use_cases import ImportReleaseUseCase
from app.infrastructure.release_package import load_release_package
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.sqlalchemy.innovation_repository import SqlAlchemyInnovationRepository
from app.application.errors import ConflictError
from app.models import (
    DependencyAnalysisRunRecord,
    DependencyCandidateRecord,
    GraphBuildRun,
    GraphVersion,
    GraphVersionDependencyRecord,
    PublishedFactLineageRecord,
    ReleaseImportBatch,
    ReleaseImportItem,
    Skill,
    SkillClassification,
    SkillTaxonomyNode,
    StandardPosition,
)
from jobgraph_contracts.published_jd import build_published_jd_fact_v3
from tests.factories import (
    approve_build_tasks,
    prepare_catalog,
    prepare_jd,
    valid_build,
)
from tests.test_published_fact_ingestion import published_fact, service_headers


def v3_fact():
    payload = published_fact()
    payload["validation_lineage"] = {
        "state": "present",
        "data_validation_task_id": "DVT_K0",
        "validation_report_id": "DVR_K0",
        "validated_bundle_snapshot_id": "VBS_K0",
        "validation_policy_version": "validation-policy.k0",
        "validation_conclusion": "pass",
        "absent_reason": None,
    }
    payload["skill_catalog_snapshot"] = {
        "source": "main-system-skill-catalog",
        "catalog_version": "catalog-v2",
        "content_hash": "a1b2c3d4" * 8,
        "effective_at": "2026-07-16T09:00:00+00:00",
        "status": "active",
    }
    payload["position_catalog_snapshot"] = {
        "source": "main-system-position-catalog",
        "catalog_version": "position-taxonomy.v3.0.0",
        "content_hash": "a1b2c3d4" * 8,
        "effective_at": "2026-07-16T09:00:00+00:00",
        "status": "active",
    }
    return build_published_jd_fact_v3(payload)


def test_v3_http_import_persists_validated_lineage(client, db):
    headers = {
        **service_headers(db),
        "X-Main-User-Id": "publisher-k0",
        "X-Main-User-Role": "admin",
    }
    response = client.post(
        "/api/v3/integrations/published-jd-facts",
        json=v3_fact().model_dump(mode="json"),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["contract_version"] == "published-jd-fact.v3"
    lineage = db.scalar(select(PublishedFactLineageRecord))
    assert lineage is not None
    assert lineage.validation_report_id == "DVR_K0"
    assert lineage.catalog_source == "main-system-skill-catalog"


def _release_command(package):
    commands = []
    for body in package.facts:
        validation = body.validation_lineage.model_dump(mode="json")
        validation.pop("state")
        validation.pop("absent_reason")
        commands.append(
            ImportPublishedJDFactCommand(
                published_jd_fact(body),
                map_published_fact_lineage(
                    validation=validation,
                    catalog=body.skill_catalog_snapshot.model_dump(mode="json"),
                ),
            )
        )
    return ImportReleaseCommand(
        package.manifest, package.manifest_hash, tuple(commands)
    )


def test_release_package_is_verified_and_imported_idempotently(tmp_path: Path, db):
    fact = v3_fact()
    compressed = gzip.compress(
        (fact.model_dump_json() + "\n").encode("utf-8"), mtime=0
    )
    artifact_path = tmp_path / "facts.jsonl.gz"
    artifact_path.write_bytes(compressed)
    manifest = {
        "release_schema_version": "kg-release-manifest.v1",
        "release_id": "release-k0-1",
        "created_at": "2026-07-16T11:00:00+00:00",
        "producer": {"application": "k0-test", "git_commit": "abc123"},
        "mode": "full",
        "parent_release_id": None,
        "observation_window": {
            "start": "2026-07-16T00:00:00+00:00",
            "end": "2026-07-16T23:59:59+00:00",
        },
        "artifacts": [
            {
                "artifact_type": "published-jd-facts",
                "contract_version": "published-jd-fact.v3",
                "path": "facts.jsonl.gz",
                "record_count": 1,
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    package = load_release_package(tmp_path)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    use_case = ImportReleaseUseCase(lambda: SqlAlchemyUnitOfWork(factory))
    first = use_case.execute(_release_command(package))
    second = use_case.execute(_release_command(package))
    assert first.idempotent is False
    assert second.idempotent is True
    assert db.scalar(select(ReleaseImportBatch)).release_id == "release-k0-1"
    assert db.scalar(select(ReleaseImportItem)).source_fact_id == "FACT_1"


def test_stable_skill_relation_snapshot_filters_required_compatibility_view(
    client, db, auth_headers
):
    admin = auth_headers()
    build = valid_build(client, db, admin, doc_id="K0_RELATION", modality="bonus")
    published = client.post(
        f"/api/v1/graph/build-runs/{build['build_run_id']}/publish",
        json={},
        headers=admin,
    )
    assert published.status_code == 200, published.text
    headers = {
        **service_headers(db),
        "X-Main-User-Id": "matching-k0",
        "X-Main-User-Role": "developer",
    }
    snapshot = client.get(
        "/api/v1/integrations/positions/BACKEND_ENGINEER/skill-relations",
        headers=headers,
    )
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["data"]["contract_version"] == "skill-relation-snapshot.v1"
    assert snapshot.json()["data"]["relations"][0]["primary_modality"] == "bonus"
    references = client.get(
        "/api/v1/integrations/position-references", headers=admin
    )
    assert references.json()["data"][0]["required_skills"] == []


def test_v2_skill_relation_snapshot_allows_skill_without_domain(
    client, db, auth_headers
):
    admin = auth_headers()
    prepare_catalog(db)
    skill = db.scalar(select(Skill).where(Skill.skill_id == "SKILL_PYTHON"))
    skill.category_code = None
    skill.taxonomy_version = "skill-taxonomy-catalog-current"
    concept = SkillTaxonomyNode(
        facet="concept_class",
        code="technology",
        name_zh="技术实体",
        name_en="Technology",
    )
    kind = SkillTaxonomyNode(
        facet="technology_kind",
        code="language",
        name_zh="编程语言",
        name_en="Language",
    )
    db.add_all([concept, kind])
    db.flush()
    db.add_all(
        [
            SkillClassification(
                skill_id=skill.skill_id,
                taxonomy_node_id=node.id,
                facet=node.facet,
                is_primary=True,
            )
            for node in (concept, kind)
        ]
    )
    db.commit()
    prepare_jd(client, admin, doc_id="K0_NO_DOMAIN")
    response = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=admin
    )
    assert response.status_code == 200, response.text
    build = response.json()["data"]
    approve_build_tasks(client, build["build_run_id"], admin)
    published = client.post(
        f"/api/v1/graph/build-runs/{build['build_run_id']}/publish",
        json={},
        headers=admin,
    )
    assert published.status_code == 200, published.text

    headers = {
        **service_headers(db),
        "X-Main-User-Id": "matching-k0",
        "X-Main-User-Role": "developer",
    }
    snapshot = client.get(
        "/api/v2/integrations/positions/BACKEND_ENGINEER/skill-relations",
        headers=headers,
    )
    assert snapshot.status_code == 200, snapshot.text
    relation = snapshot.json()["data"]["relations"][0]
    assert snapshot.json()["data"]["contract_version"] == (
        "skill-relation-snapshot.v2"
    )
    assert relation["taxonomy_version"] == "skill-taxonomy-catalog-current"
    assert {item["facet"] for item in relation["classifications"]} == {
        "concept_class",
        "technology_kind",
    }
    assert "category_code" not in relation


def test_dependency_review_is_frozen_only_after_complete_review(
    client, db, auth_headers, users
):
    db.add(StandardPosition(position_id="POS_DEP", name="依赖测试", category_code="T"))
    db.add_all(
        [
            Skill(skill_id="SK_A", canonical_name="A", category_code="T"),
            Skill(skill_id="SK_B", canonical_name="B", category_code="T"),
            Skill(skill_id="SK_C", canonical_name="C", category_code="T"),
        ]
    )
    db.flush()
    run = GraphBuildRun(
        position_id="POS_DEP", status="completed", config_snapshot={}, summary={}
    )
    db.add(run)
    db.flush()
    analysis = DependencyAnalysisRunRecord(
        build_run_id=run.id,
        policy_hash="1" * 64,
        policy={},
        status="completed",
        summary={"candidate_count": 2, "rejected_count": 0},
    )
    db.add(analysis)
    db.flush()
    candidates = [
        DependencyCandidateRecord(
            analysis_run_id=analysis.id,
            prerequisite_skill_id="SK_A",
            advanced_skill_id=advanced,
            metrics={
                "dependency_score": 0.5,
                "probability_prerequisite_given_advanced": 0.8,
                "probability_advanced_given_prerequisite": 0.3,
                "joint_support": 3,
                "source_diversity": 2,
                "enterprise_diversity": 2,
                "maximum_enterprise_share": 0.5,
                "bootstrap_lower": 0.2,
                "bootstrap_upper": 0.7,
                "stable_slices": ["2026-07"],
            },
            evidence_ids=[1],
            claim_kind="inferred_candidate",
        )
        for advanced in ("SK_B", "SK_C")
    ]
    db.add_all(candidates)
    db.flush()
    version = GraphVersion(
        position_id="POS_DEP",
        build_run_id=run.id,
        version_number=1,
        version_name="v1",
        snapshot={},
        source_version="graph-source-v1",
        algorithm_version="k0",
        normalization_map_version="k0",
        published_by=users["admin"].id,
    )
    db.add(version)
    db.commit()
    reviewer = auth_headers("reviewer")
    first = client.post(
        f"/api/v1/innovation/dependency-candidates/{candidates[0].id}/review",
        json={
            "decision": "accept",
            "reason": "evidence accepted",
            "policy_version": "dependency-review.k0",
        },
        headers=reviewer,
    )
    assert first.status_code == 200, first.text
    repository = SqlAlchemyInnovationRepository(db)
    with pytest.raises(ConflictError) as exc:
        repository.freeze_reviewed_dependencies(run.id, version.id)
    assert exc.value.error_code == "DEPENDENCY_REVIEW_INCOMPLETE"
    second = client.post(
        f"/api/v1/innovation/dependency-candidates/{candidates[1].id}/review",
        json={
            "decision": "reject",
            "reason": "direction is not stable",
            "policy_version": "dependency-review.k0",
        },
        headers=reviewer,
    )
    assert second.status_code == 200, second.text
    assert repository.freeze_reviewed_dependencies(run.id, version.id) == 1
    db.commit()
    frozen = db.scalars(select(GraphVersionDependencyRecord)).all()
    assert len(frozen) == 1
    assert frozen[0].claim_kind == "reviewed"
