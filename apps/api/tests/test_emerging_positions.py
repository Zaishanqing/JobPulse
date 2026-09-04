import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import SessionLocal, reset_database_data
from app.main import app
from app.models.predicted_position import PredictedPosition
from app.models.skill import Skill
from app.models.standard_position import StandardPosition
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database(monkeypatch):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient

    def discovery_response(self, payload):
        snapshots = sorted(payload["snapshots"], key=lambda item: item["jd_id"])
        digest = "-".join(item["jd_id"] for item in snapshots)
        sources = sorted({item.get("source_name") or "unknown" for item in snapshots})
        dimensions = {
            "cluster_growth_rate": 0.5,
            "skill_combo_novelty": 0.8,
            "source_diversity": min(1.0, len(sources) / 3),
            "industry_spread": 0.5,
            "distance_from_existing_positions": 0.8,
            "sample_size_penalty": 0.0,
            "single_platform_noise_penalty": -0.08 if len(sources) == 1 else 0.0,
        }
        seven_dimensions = {
            name: {
                "raw_value": {"fixture": True},
                "normalized_value": 0.8,
                "weight": weight,
                "contribution": round(0.8 * weight, 6),
                "business_meaning": name,
            }
            for name, weight in {
                "growth": 0.18,
                "cross_window_persistence": 0.16,
                "enterprise_coverage": 0.12,
                "source_diversity": 0.12,
                "standard_position_distance": 0.18,
                "evidence_quality": 0.12,
                "result_stability": 0.12,
            }.items()
        }
        evidence = [
            {
                "source_jd_id": item["jd_id"],
                "original_text_snippet": item.get("raw_text") or item["title"],
                "field_type": "responsibility",
                "data_source": item.get("source_name") or "unknown",
                "window_id": f"w{index % 3 + 1}",
                "locator": {
                    "source_fact_id": item.get("source_fact_id") or item["jd_id"],
                    "source_fact_version": item.get("source_fact_version") or "1",
                    "structured_path": "$.raw_text",
                },
            }
            for index, item in enumerate(snapshots)
        ]
        responsibility = "负责基于真实 JD 证据的 RAG 应用交付"
        skills = [{"raw_skill": "RAG"}, {"raw_skill": "Python"}]
        field_evidence = {
            "position_summary": {"content": responsibility, "items": [{"content": responsibility, "evidence": evidence[:1]}]},
            "core_responsibilities": {"content": [responsibility], "items": [{"content": responsibility, "evidence": evidence[:1]}]},
            "required_skills": {
                "content": skills,
                "items": [
                    {"content": "RAG", "evidence": evidence[:1]},
                    {"content": "Python", "evidence": evidence[:1]},
                ],
            },
            "distinguishing_features": {"content": ["RAG"], "items": [{"content": "RAG", "evidence": evidence[:1]}]},
            "representative_enterprises": {"content": {"未标注企业": len(snapshots)}, "items": [{"content": "未标注企业", "evidence": evidence[:1]}]},
            "growth_trajectory": {
                "content": [
                    {"window_id": "w1", "member_count": 1},
                    {"window_id": "w2", "member_count": 1},
                    {"window_id": "w3", "member_count": max(1, len(snapshots) - 2)},
                ],
                "items": [{"content": "w1", "evidence": evidence[:1]}],
            },
        }
        return {
            "run_id": f"run-{digest}",
            "status": "succeeded",
            "algorithm_version": "tfidf-svd-skill-agglomerative-v1",
            "clusters": [
                {
                    "cluster_id": f"cluster-{digest}",
                    "cluster_name": "RAG / Python 向量技能组合岗位簇",
                    "sample_count": len(snapshots),
                    "core_skills": [{"raw_skill": "RAG"}, {"raw_skill": "Python"}],
                    "representative_titles": [item["title"] for item in snapshots],
                    "representative_jd_ids": [item["jd_id"] for item in snapshots],
                    "stability_score": 0.9,
                    "growth_score": 0.5,
                    "distance_from_existing_positions": 0.8,
                    "emergence_assessment": {
                        "germination_score": 0.75,
                        "score_dimensions": dimensions,
                        "level": "high_potential",
                        "qualified_as_emerging": True,
                        "decision_reason": "formal evidence gates satisfied",
                        "evidence_package": {
                            "formula_version": "emergence-index-v4-seven-dimensions",
                            "sample_count": len(snapshots),
                            "source_count": len(sources),
                            "sources": sources,
                            "growth": {"method": "observed_cluster_share_change_v1"},
                            "weights": {
                                "cluster_growth_rate": 0.22,
                                "skill_combo_novelty": 0.22,
                                "source_diversity": 0.16,
                                "industry_spread": 0.14,
                                "distance_from_existing_positions": 0.26,
                            },
                            "emergence_index": {
                                "semantics": "composite ranking index, not a probability",
                                "dimensions": seven_dimensions,
                                "total_score": 0.75,
                            },
                        },
                    },
                    "generated_definition": {
                        "position_name": "大模型应用开发工程师",
                        "position_summary": responsibility,
                        "core_responsibilities": [responsibility],
                        "required_skills": skills,
                        "bonus_skills": [],
                        "industry_scenarios": ["智能客服"],
                        "distinguishing_features": ["RAG"],
                        "representative_enterprises": {"未标注企业": len(snapshots)},
                        "growth_trajectory": field_evidence["growth_trajectory"]["content"],
                        "field_evidence": field_evidence,
                    },
                }
            ],
        }

    monkeypatch.setattr(EmergingDiscoveryClient, "create_run", discovery_response)
    reset_database_data()
    with SessionLocal() as session:
        session.add_all(
            [
                Skill(
                    id=skill_id,
                    skill_name=name,
                    category=category,
                )
                for skill_id, name, category in (
                    ("skill_python", "Python", "programming_language"),
                    ("skill_rag", "RAG", "methodology"),
                    ("skill_docker", "Docker", "tool"),
                    ("skill_multi_agent", "多智能体", "methodology"),
                )
            ]
        )
        session.commit()
    yield
    reset_database_data()


def _register_and_login(username: str, role: str) -> str:
    create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={
            "role": role,
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["data"]["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _discovery_request() -> dict[str, str]:
    return {
        "algorithm": "emerge_v3_2",
        "time_window_start": "2026-06-01",
        "time_window_end": "2026-08-31",
    }


def test_formal_frozen_discovery_dataset_runs_without_reextracting():
    token = _register_and_login("frozen_discovery_dataset", "admin")
    response = client.post(
        "/api/v1/position-clusters/tasks",
        json={
            "algorithm": "emerge_v3_2",
            "dataset_id": "d5-short-window-main-v1-37585b4079dd",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    task = response.json()["data"]
    assert task["status"] == "completed"
    assert task["created_count"] >= 1
    assert task["input_payload"]["dataset_id"] == "d5-short-window-main-v1-37585b4079dd"


def _create_and_parse_jd(
    token: str,
    title: str,
    raw_text: str,
    *,
    source_name: str = "emerging-discovery-full-temporal-v1:招聘市场样本",
) -> str:
    response = client.post(
        "/api/v1/jds/text",
        json={
            "source_type": "market_crawl",
            "source_name": source_name,
            "enterprise_id": None,
            "title": title,
            "raw_text": raw_text,
            "publish_date": "2026-07-01",
            "url": "",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    jd_id = response.json()["data"]["jd_id"]

    response = client.post(
        f"/api/v1/jds/{jd_id}/parse",
        json={"model": "default", "extraction_mode": "rule"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return jd_id


def _prepare_llm_cluster(admin_token: str) -> str:
    jd_ids = []
    jd_ids.append(_create_and_parse_jd(
        admin_token,
        "大模型应用开发工程师",
        "负责 Python RAG 应用开发，建设 Agent 工作流和企业知识库。",
    ))
    jd_ids.append(_create_and_parse_jd(
        admin_token,
        "AI Agent 开发工程师",
        "负责 LLM Agent 编排、RAG 检索增强和 Docker 部署。",
    ))
    jd_ids.append(_create_and_parse_jd(
        admin_token,
        "大模型平台应用工程师",
        "围绕大模型应用、RAG 和多智能体协作完成业务系统交付。",
    ))
    for jd_id in jd_ids:
        _approve_jd(admin_token, jd_id)

    response = client.post(
        "/api/v1/position-clusters/tasks",
        json=_discovery_request(),
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    task_data = response.json()["data"]
    assert task_data["status"] == "completed"
    assert task_data["created_count"] >= 1

    task_response = client.get(
        f"/api/v1/position-clusters/tasks/{task_data['task_id']}",
        headers=_auth_headers(admin_token),
    )
    assert task_response.status_code == 200

    response = client.get(
        "/api/v1/position-clusters",
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    clusters = response.json()["data"]
    llm_cluster = next(cluster for cluster in clusters if "向量技能组合" in cluster["cluster_name"])
    return llm_cluster["cluster_id"]


def _approve_jd(token: str, jd_id: str) -> None:
    parse_result = client.get(
        f"/api/v1/jds/{jd_id}/parse-result",
        headers=_auth_headers(token),
    ).json()["data"]
    with SessionLocal() as session:
        position = (
            session.query(StandardPosition)
            .filter(
                StandardPosition.position_code == "LLM_APPLICATION_ENGINEER"
            )
            .one_or_none()
        )
        if position is None:
            position = StandardPosition(
                position_code="LLM_APPLICATION_ENGINEER",
                position_name="大模型应用开发工程师",
                taxonomy_family_code="AI_ENGINEERING",
                taxonomy_family_name="人工智能工程",
                taxonomy_version="position-taxonomy.v3.0.0",
                sample_support_status="sufficient",
                status="existing",
            )
            session.add(position)
            session.commit()
        position_id = position.id
    mapped = client.post(
        (
            f"/api/v1/jd-parse-results/{parse_result['parse_result_id']}"
            "/position-catalog-mapping"
        ),
        json={
            "target_position_id": position_id,
            "technology_focus_codes": ["LLM", "RAG"],
        },
        headers=_auth_headers(token),
    )
    assert mapped.status_code == 200
    response = client.post(
        f"/api/v1/jds/{jd_id}/parse-result/confirm",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    response = client.post(
        f"/api/v1/jds/{jd_id}/parse-result/publish",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200


def test_emerging_discovery_runtime_does_not_call_main_embedding_provider(monkeypatch):
    from app.integrations.registry import get_integration_registry

    def forbidden_embedding(_text):
        raise AssertionError("main embedding provider must not execute discovery")

    monkeypatch.setattr(get_integration_registry().embedding, "embed", forbidden_embedding)
    token = _register_and_login("embedding-boundary-admin", "admin")
    assert _prepare_llm_cluster(token)


def test_discovery_service_unavailable_returns_explicit_error(monkeypatch):
    from app.core.config import settings
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
    from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError

    token = _register_and_login("discovery_unavailable", "admin")
    jd_id = _create_and_parse_jd(token, "大模型应用开发工程师", "RAG Agent Python")
    _approve_jd(token, jd_id)
    monkeypatch.setattr(settings, "EMERGING_DISCOVERY_ENABLED", True)

    def unavailable(self, payload):
        raise EmergingDiscoveryError("Emerging discovery service is unavailable")

    monkeypatch.setattr(EmergingDiscoveryClient, "create_run", unavailable)
    response = client.post(
        "/api/v1/position-clusters/tasks",
        json=_discovery_request(),
        headers=_auth_headers(token),
    )
    assert response.status_code == 503
    assert response.json()["details"]["error_code"] == "emerging_discovery_unavailable"


def test_remote_discovery_result_is_projected_without_main_database_coupling(monkeypatch):
    from app.core.config import settings
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient

    token = _register_and_login("discovery_projection", "admin")
    jd_id = _create_and_parse_jd(token, "大模型应用开发工程师", "RAG Agent Python")
    _approve_jd(token, jd_id)
    monkeypatch.setattr(settings, "EMERGING_DISCOVERY_ENABLED", True)

    def succeeded(self, payload):
        assert payload["snapshots"][0]["review_status"] == "published"
        assert payload["snapshots"][0]["consumption_path"] == "published"
        return {
            "run_id": "run-immutable-1",
            "status": "succeeded",
            "algorithm_version": "fake-cluster-v1",
            "clusters": [
                {
                    "cluster_id": "remote-cluster-1",
                    "cluster_name": "大模型应用开发相关岗位簇",
                    "sample_count": 1,
                    "core_skills": [{"raw_skill": "RAG"}],
                    "representative_titles": ["大模型应用开发工程师"],
                    "representative_jd_ids": [jd_id],
                    "stability_score": 0.8,
                    "growth_score": 0.82,
                    "distance_from_existing_positions": 0.78,
                    "emergence_assessment": {
                        "germination_score": 0.71,
                        "score_dimensions": {"cluster_growth_rate": 0.82},
                        "level": "emerging",
                        "qualified_as_emerging": True,
                        "evidence_package": {"formula_version": "v1_evidence_weighted"},
                    },
                    "generated_definition": {
                        "position_name": "大模型应用开发工程师",
                        "required_skills": [{"raw_skill": "RAG"}],
                    },
                }
            ],
        }

    monkeypatch.setattr(EmergingDiscoveryClient, "create_run", succeeded)
    response = client.post(
        "/api/v1/position-clusters/tasks",
        json=_discovery_request(),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    task = response.json()["data"]
    assert task["result_payload"]["discovery_run_id"] == "run-immutable-1"
    clusters = client.get("/api/v1/position-clusters", headers=_auth_headers(token)).json()["data"]
    assert clusters[0]["discovery_run_id"] == "run-immutable-1"


def test_create_llm_jds_and_start_cluster_task():
    admin_token = _register_and_login("emerging_admin001", "admin")

    cluster_id = _prepare_llm_cluster(admin_token)

    assert cluster_id
    with SessionLocal() as db:
        assert db.query(PredictedPosition).count() == 0


def test_get_position_cluster_list_and_detail():
    admin_token = _register_and_login("emerging_admin002", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)

    list_response = client.get(
        "/api/v1/position-clusters",
        headers=_auth_headers(admin_token),
    )
    detail_response = client.get(
        f"/api/v1/position-clusters/{cluster_id}",
        headers=_auth_headers(admin_token),
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["cluster_id"] == cluster_id
    assert detail["sample_count"] == 3
    assert any(skill["raw_skill"] == "RAG" for skill in detail["core_skills"])
    assert detail["concept_note"]


def test_create_emerging_position_from_cluster():
    admin_token = _register_and_login("emerging_admin003", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)

    response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["emerging_id"]
    assert data["cluster_id"] == cluster_id
    assert data["status"] == "draft"
    assert data["concept_type"] == "emerging_position"


def test_calculate_germination_score():
    admin_token = _register_and_login("emerging_admin004", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    create_response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )
    emerging_id = create_response.json()["data"]["emerging_id"]

    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/germination-score",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["germination_score"] > 0
    assert "cluster_growth_rate" in data["score_dimensions"]
    assert "sample_size_penalty" in data["score_dimensions"]


def test_germination_score_explains_evidence_and_single_platform_noise():
    admin_token = _register_and_login("emerging_evidence_admin", "admin")
    personal_token = _register_and_login("emerging_evidence_personal", "personal_user")
    cluster_id = _prepare_llm_cluster(admin_token)
    emerging_id = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    ).json()["data"]["emerging_id"]

    calculated = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/germination-score",
        headers=_auth_headers(admin_token),
    )
    assert calculated.status_code == 200
    data = calculated.json()["data"]
    assert data["dimensions"] == data["score_dimensions"]
    assert data["score_dimensions"]["single_platform_noise_penalty"] < 0
    assert data["evidence_summary"]["source_count"] == 1
    assert data["evidence_summary"]["growth"]["method"] == "observed_cluster_share_change_v1"
    assert data["level"] in {"watchlist", "emerging", "high_potential"}
    assert isinstance(data["qualified_as_emerging"], bool)
    assert data["formula_version"] == "emergence-index-v4-seven-dimensions"

    public_detail = client.get(
        f"/api/v1/emerging-positions/{emerging_id}/germination-score",
        headers=_auth_headers(personal_token),
    )
    assert public_detail.status_code == 200
    assert public_detail.json()["data"]["evidence_summary"]["sample_count"] == 3


def test_germination_score_uses_editable_weight_config():
    admin_token = _register_and_login("emerging_config_admin", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    emerging_id = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    ).json()["data"]["emerging_id"]

    updated = client.put(
        "/api/v1/emerging-positions/score-config",
        headers=_auth_headers(admin_token),
        json={"growth": 0.0, "novelty": 0.0, "diversity": 0.0, "industry_spread": 0.0, "distance": 0.0},
    )
    assert updated.status_code == 200

    calculated = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/germination-score",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    assert calculated["weights"]["cluster_growth_rate"] == 0.22
    assert calculated["germination_score"] == 0.75


def test_generate_emerging_position_definition():
    admin_token = _register_and_login("emerging_admin005", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    create_response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )
    emerging_id = create_response.json()["data"]["emerging_id"]

    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/generate-definition",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["position_name"] == "大模型应用开发工程师"
    assert any("RAG" in item for item in data["core_responsibilities"])
    assert data["industry_scenarios"] == ["智能客服"]
    assert data["definition_version_id"]
    assert data["generation_mode"] == "rule_based_evidence_only"
    assert data["evidence_ids"] == data["evidence_jd_ids"]

    openapi = client.get("/openapi.json").json()
    response_ref = openapi["paths"][
        "/api/v1/emerging-positions/{emerging_id}/generate-definition"
    ]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    envelope_name = response_ref.rsplit("/", 1)[-1]
    data_ref = openapi["components"]["schemas"][envelope_name]["properties"]["data"]["$ref"]
    payload_name = data_ref.rsplit("/", 1)[-1]
    properties = openapi["components"]["schemas"][payload_name]["properties"]
    assert "generation_mode" in properties
    assert "evidence_ids" in properties


def test_formal_generation_includes_evidence_bound_industry_scenarios():
    admin_token = _register_and_login("formal_generation_scenarios", "admin")
    imported = client.post(
        "/api/v1/emerging-positions/import-formal-experiment",
        headers=_auth_headers(admin_token),
    )
    assert imported.status_code == 200
    candidates = client.get(
        "/api/v1/emerging-positions",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    candidate = next(
        item for item in candidates if item["position_name"] == "系统架构专家(图形系统)"
    )

    generated = client.post(
        f"/api/v1/emerging-positions/{candidate['emerging_id']}/generate-definition",
        headers=_auth_headers(admin_token),
    )

    assert generated.status_code == 200
    data = generated.json()["data"]
    assert data["industry_scenarios"] == [
        "图形系统架构与功能研发",
        "图形系统性能优化与疑难攻关",
    ]
    scenario_evidence = data["field_evidence"]["industry_scenarios"]["items"]
    assert all(item["evidence"] for item in scenario_evidence)
    summary = data["field_evidence"]["position_summary"]["content"]
    assert isinstance(summary, str)
    assert "聚焦于" in summary
    assert "主要负责" in summary
    assert "主要负责负责" not in summary
    assert "核心技能" in summary
    assert all(item["support_jd_count"] >= 1 for item in data["required_skills"])
    assert all(item["support_source_count"] >= 1 for item in data["required_skills"])
    assert all(item["evidence"] for item in data["required_skills"])


def test_definition_versions_are_persisted_and_selection_restores_snapshot():
    admin_token = _register_and_login("emerging_versions", "admin")
    personal_token = _register_and_login("emerging_versions_personal", "personal_user")
    cluster_id = _prepare_llm_cluster(admin_token)
    emerging_id = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    ).json()["data"]["emerging_id"]

    first = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/generate-definition",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    first_version_id = first["definition_version_id"]
    client.put(
        f"/api/v1/emerging-positions/{emerging_id}",
        headers=_auth_headers(admin_token),
        json={
            "position_name": "普通后端工程师",
            "required_skills": [{"raw_skill": "Python"}],
        },
    )
    second = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/generate-definition",
        headers=_auth_headers(admin_token),
    ).json()["data"]

    listed = client.get(
        f"/api/v1/emerging-positions/{emerging_id}/definition-versions",
        headers=_auth_headers(admin_token),
    )
    assert listed.status_code == 200
    versions = listed.json()["data"]
    assert len(versions) == 3
    assert [version["selected"] for version in versions] == [False, False, True]
    assert versions[-1]["version_id"] == second["definition_version_id"]
    assert versions[-1]["snapshot"]["position_summary"] == "负责基于真实 JD 证据的 RAG 应用交付"

    selected = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/definition-versions/{first_version_id}/select",
        headers=_auth_headers(admin_token),
    )
    assert selected.status_code == 200
    selected_data = selected.json()["data"]
    assert selected_data["selected"] is True
    assert selected_data["definition"]["position_name"] == first["position_name"]
    assert selected_data["implementation_status"] == "database_persisted_definition_snapshot"
    refreshed = client.get(
        f"/api/v1/emerging-positions/{emerging_id}",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    assert refreshed["position_name"] == first["position_name"]

    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/definition-versions/not-found/select",
        headers=_auth_headers(admin_token),
    ).status_code == 404
    assert client.get(
        f"/api/v1/emerging-positions/{emerging_id}/definition-versions",
        headers=_auth_headers(personal_token),
    ).status_code == 403


def test_edit_emerging_position_definition():
    admin_token = _register_and_login("emerging_admin006", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    create_response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )
    emerging_id = create_response.json()["data"]["emerging_id"]

    response = client.put(
        f"/api/v1/emerging-positions/{emerging_id}",
        json={
            "position_name": "企业级 RAG 应用开发工程师",
            "core_responsibilities": ["负责企业级 RAG 应用设计与落地"],
            "field_evidence": {
                "position_summary": {"content": "负责企业级 RAG 应用的设计、交付与持续优化。"}
            },
        },
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["position_name"] == "企业级 RAG 应用开发工程师"
    assert data["core_responsibilities"] == ["负责企业级 RAG 应用设计与落地"]
    refreshed = client.get(
        f"/api/v1/emerging-positions/{emerging_id}",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    assert refreshed["position_name"] == "企业级 RAG 应用开发工程师"
    assert refreshed["core_responsibilities"] == ["负责企业级 RAG 应用设计与落地"]
    assert refreshed["field_evidence"]["position_summary"]["content"] == (
        "负责企业级 RAG 应用的设计、交付与持续优化。"
    )
    versions = client.get(
        f"/api/v1/portal/admin/emerging-positions/{emerging_id}/definition-versions",
        headers=_auth_headers(admin_token),
    ).json()["data"]
    assert versions[0]["snapshot"]["position_name"] == "企业级 RAG 应用开发工程师"
    assert versions[0]["implementation_status"] == "database_persisted_definition_snapshot"


def test_publish_emerging_position():
    admin_token = _register_and_login("emerging_admin007", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    create_response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )
    emerging_id = create_response.json()["data"]["emerging_id"]

    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish",
        headers=_auth_headers(admin_token),
    ).status_code == 409
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/submit-review",
        headers=_auth_headers(admin_token),
    ).status_code == 200
    reviewed = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/review",
        json={"conclusion": "approved", "reason": "证据完整，批准演示发布"},
        headers=_auth_headers(admin_token),
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["data"]["review_history"][0]["reviewer"]

    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "published"
    assert response.json()["data"]["published_snapshot"]["definition"]


def test_promote_to_standard_position_creates_position():
    admin_token = _register_and_login("emerging_admin008", "admin")
    cluster_id = _prepare_llm_cluster(admin_token)
    create_response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(admin_token),
    )
    emerging_id = create_response.json()["data"]["emerging_id"]
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/submit-review",
        headers=_auth_headers(admin_token),
    ).status_code == 200
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/review",
        json={"conclusion": "approved", "reason": "批准"},
        headers=_auth_headers(admin_token),
    ).status_code == 200
    client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish",
        headers=_auth_headers(admin_token),
    )

    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/promote-to-position",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    standard_position = response.json()["data"]["standard_position"]
    assert standard_position["standard_position_id"]
    assert standard_position["source_emerging_position_id"] == emerging_id
    assert standard_position["status"] == "existing"


def test_review_rejects_unbound_edits_and_approved_edit_requires_reapproval():
    token = _register_and_login("emerging_review_gate", "admin")
    cluster_id = _prepare_llm_cluster(token)
    created = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}",
        headers=_auth_headers(token),
    ).json()["data"]
    emerging_id = created["emerging_id"]
    client.post(
        f"/api/v1/emerging-positions/{emerging_id}/submit-review",
        headers=_auth_headers(token),
    )
    invalid = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/review",
        json={
            "conclusion": "approved",
            "reason": "尝试加入无证据职责",
            "core_responsibilities": ["模型自行补充且无法定位的职责"],
        },
        headers=_auth_headers(token),
    )
    assert invalid.status_code == 409
    assert "core_responsibilities" in str(invalid.json())

    approved = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/review",
        json={"conclusion": "approved", "reason": "原始定义证据完整"},
        headers=_auth_headers(token),
    )
    assert approved.status_code == 200
    changed = client.put(
        f"/api/v1/emerging-positions/{emerging_id}",
        json={"position_name": "经人工修改的新名称"},
        headers=_auth_headers(token),
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["status"] == "pending_review"
    assert client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish",
        headers=_auth_headers(token),
    ).status_code == 409
