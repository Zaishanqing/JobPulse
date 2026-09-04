import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data
from app.main import app
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register_and_login(username: str, role: str = "admin") -> str:
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


def _create_python_skill(token: str) -> str:
    response = client.post(
        "/api/v1/skills",
        json={
            "skill_name": "Python",
            "category": "编程语言",
            "description": "Python 编程语言",
            "parent_skill_id": None,
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]["skill_id"]


def _create_skill(token: str, name: str, category: str = "测试分类") -> str:
    response = client.post(
        "/api/v1/skills",
        json={"skill_name": name, "category": category},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["skill_id"]


def _create_taxonomy_node(
    token: str,
    facet: str,
    code: str,
    name_zh: str,
    *,
    status: str = "active",
    parent_id: str | None = None,
) -> str:
    response = client.post(
        "/api/v1/skill-taxonomy/nodes",
        json={
            "facet": facet,
            "code": code,
            "name_zh": name_zh,
            "status": status,
            "parent_id": parent_id,
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["node_id"]


def test_create_skill_python():
    token = _register_and_login("skill_admin001")

    response = client.post(
        "/api/v1/skills",
        json={
            "skill_name": "Python",
            "category": "编程语言",
            "description": "Python 编程语言",
            "parent_skill_id": None,
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_id"]
    assert data["skill_name"] == "Python"
    assert data["category"] == "编程语言"


def test_add_alias_python_development():
    token = _register_and_login("skill_admin002")
    skill_id = _create_python_skill(token)

    response = client.post(
        f"/api/v1/skills/{skill_id}/aliases",
        json={"alias": "python开发"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_id"] == skill_id
    assert data["alias"] == "python开发"


def test_normalize_alias_returns_python():
    token = _register_and_login("skill_admin003")
    skill_id = _create_python_skill(token)
    client.post(
        f"/api/v1/skills/{skill_id}/aliases",
        json={"alias": "python开发"},
        headers=_auth_headers(token),
    )

    response = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "python开发", "context": "熟悉 python开发"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["need_review"] is False
    assert data["candidates"][0]["skill_id"] == skill_id
    assert data["candidates"][0]["skill_name"] == "Python"


def test_normalize_unknown_skill_creates_candidate():
    token = _register_and_login("skill_admin004")
    response = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "量子提示工程", "context": "新岗位技能"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["need_review"] is True
    assert data["candidate_id"]
    assert data["candidates"] == []


def test_get_normalization_candidates():
    token = _register_and_login("skill_admin005")
    client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "未知技能A", "context": "待审核"},
        headers=_auth_headers(token),
    )

    response = client.get(
        "/api/v1/skills/normalize-candidates",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["raw_skill"] == "未知技能A"
    assert data[0]["status"] == "pending"


def test_map_existing_candidate_and_normalize_again():
    token = _register_and_login("skill_admin006")
    skill_id = _create_python_skill(token)
    normalize_response = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "脚本工程能力X", "context": "候选确认测试"},
        headers=_auth_headers(token),
    )
    candidate_id = normalize_response.json()["data"]["candidate_id"]

    response = client.post(
        f"/api/v1/skills/normalize-candidates/{candidate_id}/map-existing",
        json={
            "skill_id": skill_id,
            "add_alias": True,
            "decision_reason": "属于 Python 的原始表达",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "mapped_existing"
    assert response.json()["data"]["reviewer_id"]
    assert response.json()["data"]["reviewed_at"]
    normalized_again = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "脚本工程能力X"},
        headers=_auth_headers(token),
    ).json()["data"]
    assert normalized_again["need_review"] is False
    assert normalized_again["candidates"][0]["skill_id"] == skill_id
    repeated_review = client.post(
        f"/api/v1/skills/normalize-candidates/{candidate_id}/exclude-non-skill",
        json={"decision_reason": "不应覆盖已经完成的映射"},
        headers=_auth_headers(token),
    )
    assert repeated_review.status_code == 400
    candidate = client.get(
        "/api/v1/skills/normalize-candidates",
        headers=_auth_headers(token),
    ).json()["data"][0]
    assert candidate["status"] == "mapped_existing"


def test_exclude_non_skill_candidate():
    token = _register_and_login("skill_admin007")
    normalize_response = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "不存在技能B", "context": "候选驳回测试"},
        headers=_auth_headers(token),
    )
    candidate_id = normalize_response.json()["data"]["candidate_id"]

    response = client.post(
        f"/api/v1/skills/normalize-candidates/{candidate_id}/exclude-non-skill",
        json={"decision_reason": "这是公司名称，不是技能"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "excluded_non_skill"


def test_create_new_candidate_with_required_classifications():
    token = _register_and_login("skill_admin_created_candidate001")
    headers = _auth_headers(token)
    normalized = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "新技能实体", "source_type": "manual"},
        headers=headers,
    )
    candidate_id = normalized.json()["data"]["candidate_id"]
    concept_id = _create_taxonomy_node(
        token, "concept_class", "technology", "技术实体"
    )
    technology_kind_id = _create_taxonomy_node(
        token, "technology_kind", "framework", "框架与平台"
    )
    domain_id = _create_taxonomy_node(
        token, "domain", "ai_systems", "人工智能系统"
    )

    response = client.post(
        f"/api/v1/skills/normalize-candidates/{candidate_id}/create-new",
        json={
            "skill_name": "新技能实体标准名",
            "concept_class_id": concept_id,
            "technology_kind_id": technology_kind_id,
            "domain_id": domain_id,
            "add_alias": True,
            "decision_reason": "确认纳入技能目录",
        },
        headers=headers,
    )

    assert response.status_code == 200
    created = response.json()["data"]
    assert created["status"] == "created_new"
    skills = client.get("/api/v1/skills").json()["data"]
    skill = next(item for item in skills if item["skill_name"] == "新技能实体标准名")
    classifications = client.get(
        f"/api/v1/skills/{skill['skill_id']}/classifications"
    ).json()["data"]
    assert {item["facet"] for item in classifications} == {
        "concept_class",
        "technology_kind",
        "domain",
    }
    normalized_again = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "新技能实体"},
        headers=headers,
    ).json()["data"]
    assert normalized_again["need_review"] is False
    assert normalized_again["candidates"][0]["skill_id"] == skill["skill_id"]


def test_create_new_action_rolls_back_all_writes(monkeypatch):
    from app.infrastructure.skills import SqlAlchemySkillRepository

    token = _register_and_login("skill_admin_candidate_rollback001")
    headers = _auth_headers(token)
    normalized = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "事务回滚技能", "source_type": "manual"},
        headers=headers,
    )
    candidate_id = normalized.json()["data"]["candidate_id"]
    concept_id = _create_taxonomy_node(
        token, "concept_class", "knowledge", "知识概念"
    )
    domain_id = _create_taxonomy_node(
        token, "domain", "transaction_test", "事务测试"
    )

    def fail_status_write(self, *args, **kwargs):
        raise RuntimeError("forced candidate status failure")

    monkeypatch.setattr(
        SqlAlchemySkillRepository,
        "set_candidate_status",
        fail_status_write,
    )
    with pytest.raises(RuntimeError, match="forced candidate status failure"):
        client.post(
            f"/api/v1/skills/normalize-candidates/{candidate_id}/create-new",
            json={
                "skill_name": "不应落库的技能",
                "concept_class_id": concept_id,
                "domain_id": domain_id,
                "decision_reason": "验证单事务回滚",
            },
            headers=headers,
        )

    skills = client.get("/api/v1/skills").json()["data"]
    assert all(item["skill_name"] != "不应落库的技能" for item in skills)
    candidate = client.get(
        "/api/v1/skills/normalize-candidates",
        headers=headers,
    ).json()["data"][0]
    assert candidate["status"] == "pending"
    assert candidate["reviewer_id"] is None


def test_unknown_expression_is_normalized_and_accumulated_with_evidence():
    token = _register_and_login("skill_admin_candidate_pool001")
    headers = _auth_headers(token)

    first = client.post(
        "/api/v1/skills/normalize",
        json={
            "raw_skill": "  ＡＩ   Agent  ",
            "source_type": "jd",
            "evidence": "岗位要求掌握 AI Agent",
        },
        headers=headers,
    )
    second = client.post(
        "/api/v1/skills/normalize",
        json={
            "raw_skill": "ai agent",
            "source_type": "cv",
            "evidence": "项目中开发 ai agent",
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["candidate_id"] == second.json()["data"]["candidate_id"]

    response = client.get(
        "/api/v1/skills/normalize-candidates",
        params={"status": "pending", "keyword": "AI", "source_type": "cv"},
        headers=headers,
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["raw_skill"] == "AI Agent"
    assert rows[0]["normalized_skill"] == "ai agent"
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["source_type"] == "jd"
    assert rows[0]["representative_evidence"] == "岗位要求掌握 AI Agent"
    assert {item["source_type"] for item in rows[0]["evidence_samples"]} == {
        "jd",
        "cv",
    }
    assert rows[0]["first_seen_at"]
    assert rows[0]["last_seen_at"]


def test_partial_existing_match_is_suggestion_only_and_review_is_audited():
    token = _register_and_login("skill_admin_candidate_pool002")
    headers = _auth_headers(token)
    skill_id = _create_python_skill(token)

    normalized = client.post(
        "/api/v1/skills/normalize",
        json={
            "raw_skill": "Python 开发平台",
            "context": "候选上下文",
            "source_type": "manual",
        },
        headers=headers,
    )
    assert normalized.status_code == 200
    data = normalized.json()["data"]
    assert data["need_review"] is True
    assert data["candidate_id"]
    assert data["candidates"][0]["skill_id"] == skill_id

    reviewed = client.post(
        f"/api/v1/skills/normalize-candidates/{data['candidate_id']}/defer",
        json={"decision_reason": "需要补充更多样本"},
        headers=headers,
    )
    assert reviewed.status_code == 200
    candidate = reviewed.json()["data"]
    assert candidate["status"] == "deferred"
    assert candidate["reviewer_id"]
    assert candidate["reviewed_at"]
    assert candidate["decision_reason"] == "需要补充更多样本"


def test_normalize_batch():
    token = _register_and_login("skill_admin008")
    skill_id = _create_python_skill(token)
    client.post(
        f"/api/v1/skills/{skill_id}/aliases",
        json={"alias": "python开发"},
        headers=_auth_headers(token),
    )

    response = client.post(
        "/api/v1/skills/normalize-batch",
        json={
            "items": [
                {"raw_skill": "Python"},
                {"raw_skill": "python开发"},
                {"raw_skill": "未知技能C"},
            ]
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 3
    assert data[0]["need_review"] is False
    assert data[1]["candidates"][0]["skill_name"] == "Python"
    assert data[2]["need_review"] is True


def test_merge_preview_redirects_old_skill_and_normalization():
    token = _register_and_login("skill_admin_merge_preview001")
    headers = _auth_headers(token)
    source_id = _create_skill(token, "旧数据分析技能")
    target_id = _create_skill(token, "标准数据分析技能")
    client.post(
        f"/api/v1/skills/{source_id}/aliases",
        json={"alias": "旧分析别名"},
        headers=headers,
    )
    client.post(
        f"/api/v1/skills/{target_id}/aliases",
        json={"alias": "标准分析别名"},
        headers=headers,
    )

    source_concept = _create_taxonomy_node(
        token, "concept_class", "knowledge", "知识概念"
    )
    target_concept = _create_taxonomy_node(
        token, "concept_class", "practice", "方法与实践"
    )
    source_domain = _create_taxonomy_node(
        token, "domain", "source_domain", "源领域"
    )
    target_domain = _create_taxonomy_node(
        token, "domain", "target_domain", "目标领域"
    )
    for skill_id, node_id in (
        (source_id, source_concept),
        (source_id, source_domain),
        (target_id, target_concept),
        (target_id, target_domain),
    ):
        response = client.post(
            f"/api/v1/skills/{skill_id}/classifications",
            json={"taxonomy_node_id": node_id, "is_primary": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    candidate_expressions = {}
    for source_type in ("jd", "cv", "manual", "unknown"):
        expression = f"待合并影响-{source_type}"
        normalized = client.post(
            "/api/v1/skills/normalize",
            json={"raw_skill": expression, "source_type": source_type},
            headers=headers,
        ).json()["data"]
        candidate_expressions[source_type] = expression
        mapped = client.post(
            f"/api/v1/skills/normalize-candidates/{normalized['candidate_id']}/map-existing",
            json={
                "skill_id": source_id,
                "decision_reason": "用于合并影响预览",
            },
            headers=headers,
        )
        assert mapped.status_code == 200, mapped.text

    payload = {"source_skill_id": source_id, "target_skill_id": target_id}
    preview = client.post(
        "/api/v1/skills/merge/preview",
        json=payload,
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    assert data["source"]["skill"]["skill_name"] == "旧数据分析技能"
    assert data["target"]["skill"]["skill_name"] == "标准数据分析技能"
    assert data["source"]["alias_count"] == 1
    assert data["target"]["alias_count"] == 1
    assert data["source"]["related_candidate_count"] == 4
    assert data["impact_by_source"] == {
        "jd": 1,
        "cv": 1,
        "manual": 1,
        "unknown": 1,
    }
    assert any("concept_class" in item for item in data["classification_conflicts"])
    assert any("domain" in item for item in data["classification_conflicts"])

    merged = client.post(
        "/api/v1/skills/merge",
        json=payload,
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["data"]["source_status"] == "redirected"

    old_skill = client.get(f"/api/v1/skills/{source_id}").json()["data"]
    assert old_skill["skill_name"] == "旧数据分析技能"
    assert old_skill["status"] == "redirected"
    assert old_skill["redirect_target_skill_id"] == target_id
    assert len(client.get(f"/api/v1/skills/{source_id}/aliases").json()["data"]) == 1
    assert len(
        client.get(f"/api/v1/skills/{source_id}/classifications").json()["data"]
    ) == 2

    for expression in (
        "旧数据分析技能",
        "旧分析别名",
        candidate_expressions["jd"],
    ):
        normalized = client.post(
            "/api/v1/skills/normalize",
            json={"raw_skill": expression},
            headers=headers,
        ).json()["data"]
        assert normalized["need_review"] is False
        assert normalized["candidates"][0]["skill_id"] == target_id
        assert normalized["candidates"][0]["redirected_from_skill_id"] == source_id
        assert normalized["candidates"][0]["redirected_from_skill_name"] == "旧数据分析技能"


def test_skill_catalog_draft_publish_versions_and_history_are_stable():
    token = _register_and_login("skill_admin_catalog_publish001")
    headers = _auth_headers(token)
    concept_id = _create_taxonomy_node(
        token, "concept_class", "knowledge", "知识概念"
    )
    primary_domain_id = _create_taxonomy_node(
        token, "domain", "catalog_primary", "目录主领域"
    )
    secondary_domain_id = _create_taxonomy_node(
        token, "domain", "catalog_secondary", "目录次领域"
    )
    source_id = _create_skill(token, "目录技能甲")
    target_id = _create_skill(token, "目录技能乙")
    for skill_id in (source_id, target_id):
        for node_id, is_primary in (
            (concept_id, True),
            (primary_domain_id, True),
        ):
            response = client.post(
                f"/api/v1/skills/{skill_id}/classifications",
                json={"taxonomy_node_id": node_id, "is_primary": is_primary},
                headers=headers,
            )
            assert response.status_code == 200, response.text
    client.post(
        f"/api/v1/skills/{source_id}/aliases",
        json={"alias": "目录甲别名"},
        headers=headers,
    )

    first_preview = client.get(
        "/api/v1/skills/catalog/draft",
        headers=headers,
    )
    assert first_preview.status_code == 200, first_preview.text
    first_draft = first_preview.json()["data"]
    assert first_draft["based_on_catalog_version"] is None
    assert first_draft["publishable"] is True
    assert first_draft["change_summary"]["counts"] == {
        "added_skills": 2,
        "modified_skills": 0,
        "added_aliases": 1,
        "classification_adjustments": 2,
        "merged_or_inactive": 0,
    }

    first_publish = client.post(
        "/api/v1/skills/catalog/publish",
        headers=headers,
    )
    assert first_publish.status_code == 200, first_publish.text
    version_one = first_publish.json()["data"]
    assert version_one["catalog_version"] == "skill-catalog.v1"
    assert version_one["version_number"] == 1

    new_skill_id = _create_skill(token, "目录技能丙")
    for node_id, is_primary in (
        (concept_id, True),
        (primary_domain_id, True),
    ):
        client.post(
            f"/api/v1/skills/{new_skill_id}/classifications",
            json={"taxonomy_node_id": node_id, "is_primary": is_primary},
            headers=headers,
        )
    update = client.put(
        f"/api/v1/skills/{source_id}",
        json={"skill_name": "目录技能甲修订"},
        headers=headers,
    )
    assert update.status_code == 200, update.text
    client.post(
        f"/api/v1/skills/{source_id}/aliases",
        json={"alias": "目录甲新增别名"},
        headers=headers,
    )
    client.post(
        f"/api/v1/skills/{source_id}/classifications",
        json={"taxonomy_node_id": secondary_domain_id, "is_primary": False},
        headers=headers,
    )
    merged = client.post(
        "/api/v1/skills/merge",
        json={"source_skill_id": source_id, "target_skill_id": target_id},
        headers=headers,
    )
    assert merged.status_code == 200, merged.text

    second_preview = client.get(
        "/api/v1/skills/catalog/draft",
        headers=headers,
    ).json()["data"]
    assert second_preview["based_on_catalog_version"] == "skill-catalog.v1"
    assert second_preview["publishable"] is True
    counts = second_preview["change_summary"]["counts"]
    assert counts["added_skills"] == 1
    assert counts["modified_skills"] == 1
    assert counts["added_aliases"] == 1
    assert counts["classification_adjustments"] == 2
    assert counts["merged_or_inactive"] == 1

    second_publish = client.post(
        "/api/v1/skills/catalog/publish",
        headers=headers,
    )
    assert second_publish.status_code == 200, second_publish.text
    assert second_publish.json()["data"]["catalog_version"] == "skill-catalog.v2"

    latest = client.get("/api/v1/skills/catalog/versions/latest")
    historical = client.get("/api/v1/skills/catalog/versions/skill-catalog.v1")
    assert latest.status_code == 200
    assert historical.status_code == 200
    assert latest.json()["data"]["catalog_version"] == "skill-catalog.v2"
    historical_skills = {
        item["skill_id"]: item
        for item in historical.json()["data"]["snapshot"]["skills"]
    }
    assert historical_skills[source_id]["skill_name"] == "目录技能甲"
    assert historical_skills[source_id]["status"] == "active"
    assert new_skill_id not in historical_skills

    client.put(
        f"/api/v1/skills/{target_id}",
        json={"skill_name": "发布后继续修改"},
        headers=headers,
    )
    version_two_again = client.get(
        "/api/v1/skills/catalog/versions/skill-catalog.v2"
    ).json()["data"]
    published_target = next(
        item
        for item in version_two_again["snapshot"]["skills"]
        if item["skill_id"] == target_id
    )
    assert published_target["skill_name"] == "目录技能乙"


def test_published_catalog_renormalization_complete_governance_flow():
    token = _register_and_login("skill_admin_renormalize001")
    headers = _auth_headers(token)
    concept_id = _create_taxonomy_node(
        token, "concept_class", "knowledge", "知识概念"
    )
    domain_id = _create_taxonomy_node(
        token, "domain", "renormalize_domain", "重归一化领域"
    )
    existing_id = _create_skill(token, "已有标准技能")
    for node_id in (concept_id, domain_id):
        response = client.post(
            f"/api/v1/skills/{existing_id}/classifications",
            json={"taxonomy_node_id": node_id, "is_primary": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    def unknown(raw_skill: str, source_type: str, evidence: str) -> dict:
        response = client.post(
            "/api/v1/skills/normalize",
            json={
                "raw_skill": raw_skill,
                "source_type": source_type,
                "evidence": evidence,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["need_review"] is True
        return data

    unknown("发布后识别表达", "jd", "JD 要求发布后识别表达")
    initial = client.get(
        "/api/v1/skills/normalize-candidates",
        params={"keyword": "发布后识别表达"},
        headers=headers,
    ).json()["data"][0]
    assert initial["normalization_state"] == "unresolved"
    assert initial["evidence_samples"][0]["evidence"] == "JD 要求发布后识别表达"

    mapped = unknown("既有技能旧表达", "jd", "JD 中的既有技能旧表达")
    response = client.post(
        f"/api/v1/skills/normalize-candidates/{mapped['candidate_id']}/map-existing",
        json={
            "skill_id": existing_id,
            "add_alias": True,
            "decision_reason": "确认映射至已有标准技能",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    created = unknown("新建并分类表达", "cv", "CV 项目使用新建并分类表达")
    response = client.post(
        f"/api/v1/skills/normalize-candidates/{created['candidate_id']}/create-new",
        json={
            "skill_name": "新建标准技能",
            "concept_class_id": concept_id,
            "domain_id": domain_id,
            "add_alias": True,
            "decision_reason": "确认创建并完成基本分类",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    created_id = response.json()["data"]["candidate_skill_id"]

    excluded = unknown("某某有限公司", "jd", "JD 公司抬头某某有限公司")
    response = client.post(
        f"/api/v1/skills/normalize-candidates/{excluded['candidate_id']}/exclude-non-skill",
        json={"decision_reason": "公司名不是技能"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    deferred = unknown("含义不明确表达", "cv", "CV 仅出现一次且无上下文")
    response = client.post(
        f"/api/v1/skills/normalize-candidates/{deferred['candidate_id']}/defer",
        json={"decision_reason": "Evidence 不足，暂缓判断"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    normalized_again = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "既有技能旧表达"},
        headers=headers,
    ).json()["data"]
    assert normalized_again["need_review"] is False
    assert normalized_again["candidates"][0]["skill_id"] == existing_id

    response = client.post(
        f"/api/v1/skills/{existing_id}/aliases",
        json={"alias": "发布后识别表达"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    published = client.post(
        "/api/v1/skills/catalog/publish", headers=headers
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["catalog_version"] == "skill-catalog.v1"

    rerun = client.post(
        "/api/v1/skills/normalize-candidates/re-normalize",
        headers=headers,
    )
    assert rerun.status_code == 200, rerun.text
    summary = rerun.json()["data"]
    assert summary == {
        "catalog_version": "skill-catalog.v1",
        "resolved_candidate_count": 1,
        "unresolved_candidate_count": 1,
        "excluded_non_skill_count": 1,
        "affected_jd_count": 3,
        "affected_cv_count": 2,
    }

    normalized_after_publish = client.post(
        "/api/v1/skills/normalize",
        json={"raw_skill": "发布后识别表达"},
        headers=headers,
    ).json()["data"]
    assert normalized_after_publish["need_review"] is False
    assert normalized_after_publish["candidates"][0]["skill_id"] == existing_id

    downstream = client.get("/api/v1/skills/catalog/downstream")
    assert downstream.status_code == 200, downstream.text
    projection = downstream.json()["data"]
    assert projection["catalog_version"] == "skill-catalog.v1"
    assert set(projection["resolved_skill_ids"]) == {existing_id, created_id}
    assert [item["raw_skill"] for item in projection["unresolved_candidates"]] == [
        "含义不明确表达"
    ]
    assert projection["unresolved_candidates"][0]["evidence_samples"]

    candidates = client.get(
        "/api/v1/skills/normalize-candidates", headers=headers
    ).json()["data"]
    assert all(item["catalog_version"] == "skill-catalog.v1" for item in candidates)
    assert all(item["normalized_at"] for item in candidates)
    excluded_row = next(
        item for item in candidates if item["status"] == "excluded_non_skill"
    )
    assert excluded_row["raw_skill"] == "某某有限公司"
    assert excluded_row["evidence_samples"][0]["evidence"] == "JD 公司抬头某某有限公司"


def test_get_category_tree():
    token = _register_and_login("skill_admin009")
    _create_python_skill(token)

    response = client.get("/api/v1/skill-categories/tree")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["category"] == "编程语言"
    assert data[0]["skills"][0]["skill_name"] == "Python"


def test_get_domain_tree_groups_skills_by_domain_classification():
    token = _register_and_login("skill_domain_tree")
    skill_id = _create_python_skill(token)
    node_id = _create_taxonomy_node(
        token, "domain", "software_engineering", "软件工程"
    )
    response = client.post(
        f"/api/v1/skills/{skill_id}/classifications",
        json={"taxonomy_node_id": node_id, "is_primary": True},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text

    response = client.get("/api/v1/skills/domain-tree")

    assert response.status_code == 200
    data = response.json()["data"]
    software = next(item for item in data if item["category"] == "软件工程")
    assert [skill["skill_name"] for skill in software["skills"]] == ["Python"]


def test_skill_supports_multidimensional_classifications():
    token = _register_and_login("skill_taxonomy_admin001")
    skill_id = _create_python_skill(token)
    technology_id = _create_taxonomy_node(
        token, "concept_class", "technology", "技术实体"
    )
    language_id = _create_taxonomy_node(
        token, "technology_kind", "language", "编程与查询语言"
    )
    software_id = _create_taxonomy_node(
        token, "domain", "software_engineering", "软件工程"
    )
    ai_id = _create_taxonomy_node(
        token, "domain", "ai_intelligent_systems", "人工智能与智能系统"
    )

    for node_id, is_primary in (
        (technology_id, True),
        (language_id, True),
        (software_id, True),
        (ai_id, False),
    ):
        response = client.post(
            f"/api/v1/skills/{skill_id}/classifications",
            json={"taxonomy_node_id": node_id, "is_primary": is_primary},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200, response.text

    response = client.get(f"/api/v1/skills/{skill_id}/classifications")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert {(row["facet"], row["code"]) for row in rows} == {
        ("concept_class", "technology"),
        ("technology_kind", "language"),
        ("domain", "software_engineering"),
        ("domain", "ai_intelligent_systems"),
    }
    assert [
        row["code"]
        for row in rows
        if row["facet"] == "domain" and row["is_primary"]
    ] == ["software_engineering"]


def test_classification_rejects_invalid_cross_facet_combinations():
    token = _register_and_login("skill_taxonomy_admin002")
    skill_id = _create_python_skill(token)
    knowledge_id = _create_taxonomy_node(
        token, "concept_class", "knowledge", "知识概念"
    )
    practice_id = _create_taxonomy_node(
        token, "concept_class", "practice", "方法与实践"
    )
    language_id = _create_taxonomy_node(
        token, "technology_kind", "language", "编程与查询语言"
    )

    assert client.post(
        f"/api/v1/skills/{skill_id}/classifications",
        json={"taxonomy_node_id": knowledge_id, "is_primary": True},
        headers=_auth_headers(token),
    ).status_code == 200

    duplicate_facet = client.post(
        f"/api/v1/skills/{skill_id}/classifications",
        json={"taxonomy_node_id": practice_id, "is_primary": True},
        headers=_auth_headers(token),
    )
    assert duplicate_facet.status_code == 400
    assert "concept_class" in duplicate_facet.json()["message"]

    invalid_kind = client.post(
        f"/api/v1/skills/{skill_id}/classifications",
        json={"taxonomy_node_id": language_id, "is_primary": True},
        headers=_auth_headers(token),
    )
    assert invalid_kind.status_code == 400
    assert "concept_class=technology" in invalid_kind.json()["message"]


def test_inactive_taxonomy_node_cannot_be_assigned():
    token = _register_and_login("skill_taxonomy_admin003")
    skill_id = _create_python_skill(token)
    domain_id = _create_taxonomy_node(
        token,
        "domain",
        "quantum_computing",
        "量子信息与量子计算",
    )
    update = client.put(
        f"/api/v1/skill-taxonomy/nodes/{domain_id}",
        json={"status": "inactive"},
        headers=_auth_headers(token),
    )
    assert update.status_code == 200
    assert update.json()["data"]["status"] == "inactive"

    response = client.post(
        f"/api/v1/skills/{skill_id}/classifications",
        json={"taxonomy_node_id": domain_id, "is_primary": True},
        headers=_auth_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Inactive taxonomy node cannot be assigned"
    )


def test_taxonomy_parent_must_use_same_facet_and_active_status():
    token = _register_and_login("skill_taxonomy_admin004")
    domain_id = _create_taxonomy_node(
        token, "domain", "software_engineering", "软件工程"
    )

    response = client.post(
        "/api/v1/skill-taxonomy/nodes",
        json={
            "facet": "technology_kind",
            "code": "language",
            "name_zh": "编程与查询语言",
            "parent_id": domain_id,
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Taxonomy parent must use the same facet"
    )


def test_normalization_management_requires_internal_role():
    payload = {"raw_skill": "匿名候选", "context": "不应写入"}

    assert client.post("/api/v1/skills/normalize", json=payload).status_code == 401
    assert client.post(
        "/api/v1/skills/normalize-batch",
        json={"items": [payload]},
    ).status_code == 401
    assert client.get("/api/v1/skills/normalize-candidates").status_code == 401

    personal_token = _register_and_login("skill_personal010", role="personal_user")
    headers = _auth_headers(personal_token)
    assert client.post(
        "/api/v1/skills/normalize",
        json=payload,
        headers=headers,
    ).status_code == 403
    assert client.get(
        "/api/v1/skills/normalize-candidates",
        headers=headers,
    ).status_code == 403
