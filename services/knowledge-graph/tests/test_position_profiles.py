from sqlalchemy import select

from app.models import GraphVersion
from jobgraph_contracts.position_profile import PositionProfileV3
from tests.factories import approve_build_tasks, prepare_catalog, prepare_jd


def _publish(client, db, headers):
    prepare_catalog(db)
    raw_texts = {
        "JD1": (
            "后端工程师\n熟悉 Python\n负责服务开发\n本科及以上\n"
            "负责系统架构设计与微服务治理\n负责数据库分库分表与读写分离"
        ),
        "JD2": (
            "后端工程师\n熟悉 Python\n负责服务开发\n本科及以上\n"
            "负责前端页面开发与交互体验优化\n负责接口联调与文档维护"
        ),
        "JD3": (
            "后端工程师\n熟悉 Python\n负责服务开发\n本科及以上\n"
            "负责大数据平台建设与实时计算\n负责数据质量监控与治理"
        ),
    }
    for doc_id in ("JD1", "JD2", "JD3"):
        prepare_jd(
            client,
            headers,
            doc_id=doc_id,
            raw_text=raw_texts[doc_id],
        )
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    response = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "profile contract"},
        headers=headers,
    )
    assert response.status_code == 200
    return db.scalar(select(GraphVersion))


def test_position_profile_v3_and_frozen_dependencies(client, db, auth_headers):
    headers = auth_headers()
    version = _publish(client, db, headers)

    response = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"contract_version": "position-profile.v3"},
        headers=headers,
    )
    assert response.status_code == 200
    profile = PositionProfileV3.model_validate(response.json()["data"])
    assert profile.position_id == "BACKEND_ENGINEER"
    assert profile.position_code == "BACKEND_ENGINEER"
    assert profile.classification_status == "resolved"
    assert profile.sample_support_status == "sufficient"
    assert profile.profile_state == "published"
    assert profile.skill_relations[0].skill_id == "SKILL_PYTHON"
    assert profile.evidence_summary[0].quote == "熟悉 Python"
    assert profile.graph_version_id == version.id
    assert profile.graph_version == version.version_name
    assert profile.dependencies.build_config_version == version.build_config_version
    assert profile.dependencies.mapping_snapshot_version == version.mapping_snapshot_version
    assert profile.dependencies.source_time_window == {
        "start": None,
        "end": None,
    }
    assert response.headers["etag"]

    db.refresh(version)
    assert version.build_config_version == profile.dependencies.build_config_version
    assert version.normalization_algorithm_version == "deterministic-normalization-v1"


def test_position_profile_v3_accepts_numeric_source_sample_count():
    profile = PositionProfileV3.model_validate(
        {
            "contract_version": "position-profile.v3",
            "position_id": "BACKEND_ENGINEER",
            "position_name": "Backend Engineer",
            "graph_version": "graph-v1",
            "profile_state": "published",
            "taxonomy_version": "taxonomy-v1",
            "responsibilities": [],
            "requirements": [],
            "skill_relations": [],
            "evidence_summary": [],
            "quality": {},
            "graph_version_id": 1,
            "published_at": None,
            "dependencies": {
                "published_fact_versions": [],
                "skill_catalog_version": "catalog-v1",
                "mapping_snapshot_version": "mapping-v1",
                "normalization_algorithm_version": "normalization-v1",
                "build_config_version": "build-v1",
                "source_time_window": {"start": None, "end": None, "sample_count": 101},
            },
            "position_code": "BACKEND_ENGINEER",
            "classification_status": "resolved",
            "sample_support_status": "sufficient",
        }
    )

    assert profile.dependencies.source_time_window["sample_count"] == 101


def test_batch_profiles_evidence_pagination_compression_and_etag(
    client, db, auth_headers
):
    headers = {**auth_headers(), "Accept-Encoding": "gzip"}
    version = _publish(client, db, headers)
    body = {
        "position_ids": ["BACKEND_ENGINEER", "POS_MISSING"],
        "contract_version": "position-profile.v3",
        "graph_version_ids": {"BACKEND_ENGINEER": version.id},
        "view": "published",
        "draft_ids": {},
        "page": 1,
        "page_size": 2,
    }
    response = client.post("/api/v1/position-profiles/batch", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["data"]["total"] == 2
    assert response.json()["data"]["missing_position_ids"] == ["POS_MISSING"]

    cached = client.post(
        "/api/v1/position-profiles/batch",
        json=body,
        headers={**headers, "If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304

    evidence = client.post(
        "/api/v1/position-profiles/evidence/batch", json=body, headers=headers
    ).json()["data"]
    assert evidence["items"][0]["evidence_summary"][0]["quote"] == "熟悉 Python"

    skill_relations = client.post(
        "/api/v1/skill-relations/batch",
        json={"skill_ids": ["SKILL_PYTHON"]},
        headers=headers,
    )
    assert skill_relations.status_code == 200
    assert skill_relations.json()["data"] == {
        "graph_version": "kg-published-current",
        "relations": [],
    }
    assert skill_relations.headers["etag"]


def test_dependency_change_publishes_a_new_version_without_mutating_old(
    client, db, auth_headers
):
    headers = auth_headers()
    first = _publish(client, db, headers)
    original_dependency = first.build_config_version

    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={"minimum_effective_weight": 0.2},
        headers=headers,
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    published = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "new build dependency"},
        headers=headers,
    ).json()["data"]
    second = db.get(GraphVersion, published["version_id"])

    assert second.id != first.id
    assert second.build_config_version != original_dependency
    db.refresh(first)
    assert first.build_config_version == original_dependency


def test_draft_profile_requires_explicit_draft_id(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]

    implicit = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"view": "draft"},
        headers=headers,
    )
    assert implicit.status_code == 422
    explicit = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"view": "draft", "draft_id": build["build_run_id"]},
        headers=headers,
    )
    assert explicit.status_code == 200
    assert explicit.json()["data"]["profile_state"] == "draft"
