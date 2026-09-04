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


def _create_evidence(token: str, related_type: str = "skill") -> dict:
    response = client.post(
        "/api/v1/evidence-sources",
        json={
            "source_type": "jd",
            "source_name": "mock 招聘平台",
            "source_platform": "boss_zhipin",
            "title": "Python 工程师 JD 证据",
            "url": "https://example.com/jd/python",
            "raw_text": "Python、FastAPI、Docker 是岗位能力证据。",
            "credibility_score": 0.9,
            "related_object_type": related_type,
            "related_object_id": "skill_python",
            "enterprise_id": "enterprise-1",
            "template_cluster_id": "template-1",
            "source_version": "2026-07-10T00:00:00+00:00",
            "source_fact_id": "fact-1",
            "source_jd_id": "jd-1",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]


def _create_review_task(
    token: str,
    object_id: str,
    *,
    priority: str = "normal",
    object_type: str = "generic_review_object",
) -> dict:
    response = client.post(
        "/api/v1/review-tasks",
        json={
            "object_type": object_type,
            "object_id": object_id,
            "priority": priority,
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_create_and_get_evidence_source():
    admin_token = _register_and_login("review_admin001", "admin")
    evidence = _create_evidence(admin_token)

    response = client.get(
        f"/api/v1/evidence-sources/{evidence['evidence_id']}",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["evidence_id"] == evidence["evidence_id"]
    assert data["credibility_score"] == 0.9
    assert data["source_platform"] == "boss_zhipin"
    assert data["enterprise_id"] == "enterprise-1"
    assert data["template_cluster_id"] == "template-1"
    assert data["source_version"] == "2026-07-10T00:00:00+00:00"
    assert data["source_fact_id"] == "fact-1"
    assert data["source_jd_id"] == "jd-1"


def test_evidence_source_management_detail_requires_admin():
    admin_token = _register_and_login("review_admin001b", "admin")
    personal_token = _register_and_login("review_user001b", "personal_user")
    evidence = _create_evidence(admin_token)

    response = client.get(
        f"/api/v1/evidence-sources/{evidence['evidence_id']}",
        headers=_auth_headers(personal_token),
    )

    assert response.status_code == 403


def test_related_evidence_is_public_but_management_is_restricted():
    admin_token = _register_and_login("review_admin002", "admin")
    personal_token = _register_and_login("review_user002", "personal_user")
    evidence = _create_evidence(admin_token)

    related_response = client.get("/api/v1/skills/skill_python/evidence")
    manage_response = client.post(
        "/api/v1/evidence-sources",
        json={
            "source_type": "jd",
            "title": "普通用户不能管理证据",
            "raw_text": "mock",
        },
        headers=_auth_headers(personal_token),
    )

    assert related_response.status_code == 200
    assert related_response.json()["data"][0]["evidence_id"] == evidence["evidence_id"]
    assert manage_response.status_code == 403


def test_review_task_terminal_transition_and_history():
    reviewer_token = _register_and_login("reviewer003", "reviewer")

    create_response = client.post(
        "/api/v1/review-tasks",
        json={
            "object_type": "generic_review_object",
            "object_id": "parse_result_001",
            "priority": "high",
            "reason": "解析结果置信度较低",
        },
        headers=_auth_headers(reviewer_token),
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["data"]["task_id"]

    claim_response = client.post(
        f"/api/v1/review-tasks/{task_id}/claim",
        headers=_auth_headers(reviewer_token),
    )
    approve_response = client.post(
        f"/api/v1/review-tasks/{task_id}/approve",
        json={"review_comment": "证据充分"},
        headers=_auth_headers(reviewer_token),
    )
    reject_response = client.post(
        f"/api/v1/review-tasks/{task_id}/reject",
        json={"review_comment": "重新标记为拒绝测试"},
        headers=_auth_headers(reviewer_token),
    )
    modify_response = client.put(
        f"/api/v1/review-tasks/{task_id}/modify",
        json={
            "review_comment": "已人工修正技能",
            "modified_payload": {"required_skills": ["Python", "FastAPI"]},
        },
        headers=_auth_headers(reviewer_token),
    )
    history_response = client.get(
        f"/api/v1/review-tasks/{task_id}/history",
        headers=_auth_headers(reviewer_token),
    )

    assert claim_response.status_code == 200
    assert approve_response.json()["data"]["status"] == "approved"
    assert reject_response.status_code == 409
    assert modify_response.status_code == 409
    assert history_response.status_code == 200
    history = history_response.json()["data"]
    assert [item["action"] for item in history] == [
        "create",
        "claim",
        "approve",
    ]


def test_review_tasks_require_review_role():
    personal_token = _register_and_login("review_user004", "personal_user")

    response = client.get(
        "/api/v1/review-tasks",
        headers=_auth_headers(personal_token),
    )

    assert response.status_code == 403


def test_claimed_review_task_enforces_owner_and_supports_release():
    first_token = _register_and_login("review_owner_first", "reviewer")
    second_token = _register_and_login("review_owner_second", "reviewer")
    admin_token = _register_and_login("review_owner_admin", "admin")
    created = client.post(
        "/api/v1/review-tasks",
        json={
            "object_type": "generic_review_object",
            "object_id": "owned-review-1",
            "priority": "normal",
        },
        headers=_auth_headers(first_token),
    ).json()["data"]
    task_id = created["task_id"]

    claimed = client.post(
        f"/api/v1/review-tasks/{task_id}/claim",
        headers=_auth_headers(first_token),
    )
    forbidden = client.post(
        f"/api/v1/review-tasks/{task_id}/approve",
        headers=_auth_headers(second_token),
    )
    forced_release = client.post(
        f"/api/v1/review-tasks/{task_id}/release",
        headers=_auth_headers(admin_token),
    )
    approved = client.post(
        f"/api/v1/review-tasks/{task_id}/approve",
        headers=_auth_headers(second_token),
    )

    assert claimed.status_code == 200
    assert claimed.json()["data"]["reviewer_id"]
    assert forbidden.status_code == 409
    assert forced_release.status_code == 200
    assert forced_release.json()["data"]["status"] == "pending"
    assert forced_release.json()["data"]["reviewer_id"] is None
    assert approved.status_code == 409
    history = client.get(
        f"/api/v1/review-tasks/{task_id}/history",
        headers=_auth_headers(second_token),
    ).json()["data"]
    assert [item["action"] for item in history] == [
        "create", "claim", "release"
    ]


def test_local_batch_transition_commits_all_tasks_together():
    token = _register_and_login("review_batch_success", "reviewer")
    first = _create_review_task(token, "batch-success-1")
    second = _create_review_task(token, "batch-success-2")

    for task in (first, second):
        claimed = client.post(
            f"/api/v1/review-tasks/{task['task_id']}/claim",
            headers=_auth_headers(token),
        )
        assert claimed.status_code == 200

    response = client.post(
        "/api/v1/review-tasks/batch",
        json={
            "task_ids": [first["task_id"], second["task_id"]],
            "action": "approve",
            "reason": "统一审核通过",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["statuses"] == {
        first["task_id"]: "approved",
        second["task_id"]: "approved",
    }


def test_local_batch_transition_rolls_back_when_any_task_fails():
    token = _register_and_login("review_batch_rollback", "reviewer")
    first = _create_review_task(token, "batch-rollback-1")
    claimed = client.post(
        f"/api/v1/review-tasks/{first['task_id']}/claim",
        headers=_auth_headers(token),
    )
    assert claimed.status_code == 200

    response = client.post(
        "/api/v1/review-tasks/batch",
        json={
            "task_ids": [first["task_id"], "missing-review-task"],
            "action": "approve",
            "reason": "第二条不存在",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 404
    persisted = client.get(
        f"/api/v1/review-tasks/{first['task_id']}",
        headers=_auth_headers(token),
    ).json()["data"]
    assert persisted["status"] == "claimed"
    history = client.get(
        f"/api/v1/review-tasks/{first['task_id']}/history",
        headers=_auth_headers(token),
    ).json()["data"]
    assert [item["action"] for item in history] == ["create", "claim"]


def test_review_batch_rejects_mixed_sources_before_any_transition():
    token = _register_and_login("review_batch_mixed", "reviewer")
    local = _create_review_task(token, "batch-mixed-1")

    response = client.post(
        "/api/v1/review-tasks/batch",
        json={
            "task_ids": [local["task_id"], "kg:123"],
            "action": "approve",
            "reason": "不得跨服务混合",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 422
    persisted = client.get(
        f"/api/v1/review-tasks/{local['task_id']}",
        headers=_auth_headers(token),
    ).json()["data"]
    assert persisted["status"] == "pending"


def test_review_batch_requires_review_role():
    token = _register_and_login("review_batch_personal", "personal_user")

    response = client.post(
        "/api/v1/review-tasks/batch",
        json={
            "task_ids": ["local-task"],
            "action": "approve",
            "reason": "无审核权限",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 403


def test_main_system_review_pagination_filtering_and_summary():
    token = _register_and_login("review_queue_page", "reviewer")
    created = [
        _create_review_task(
            token,
            f"queue-{index}",
            priority="high" if index < 3 else "low",
        )
        for index in range(5)
    ]
    evidence_response = client.post(
        "/api/v1/evidence-sources",
        json={
            "source_type": "jd",
            "title": "审核列表证据",
            "raw_text": "真实证据摘要",
            "related_object_type": "generic_review_object",
            "related_object_id": "queue-0",
        },
        headers=_auth_headers(token),
    )
    assert evidence_response.status_code == 200

    first_page = client.get(
        "/api/v1/review-tasks",
        params={"source_system": "main-system", "page": 1, "page_size": 2},
        headers=_auth_headers(token),
    )
    second_page = client.get(
        "/api/v1/review-tasks",
        params={"source_system": "main-system", "page": 2, "page_size": 2},
        headers=_auth_headers(token),
    )
    high_risk = client.get(
        "/api/v1/review-tasks",
        params={
            "source_system": "main-system",
            "risk_level": "high",
            "task_kind": "generic_review_object",
            "status": "pending",
            "page_size": 10,
        },
        headers=_auth_headers(token),
    )

    first_ids = {item["task_id"] for item in first_page.json()["data"]}
    second_ids = {item["task_id"] for item in second_page.json()["data"]}
    assert first_ids.isdisjoint(second_ids)
    assert first_page.headers["X-Total-Count"] == str(len(created))
    assert second_page.headers["X-Total-Count"] == str(len(created))
    assert high_risk.headers["X-Total-Count"] == "3"
    assert len(high_risk.json()["data"]) == 3
    queue_zero = next(
        item
        for item in high_risk.json()["data"]
        if item["object_id"] == "queue-0"
    )
    assert queue_zero["risk_level"] == "high"
    assert queue_zero["evidence_count"] == 1
    assert queue_zero["review_flag_count"] == 0


def test_unified_queue_supports_value_information_sort():
    token = _register_and_login("review_value_sort", "reviewer")
    _create_review_task(token, "value-quiet-1", priority="low")
    _create_review_task(token, "value-blocking-1", priority="urgent")

    response = client.get(
        "/api/v1/review-tasks",
        params={"source_system": "main-system", "sort": "value"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["object_id"] == "value-blocking-1"
    assert data[0]["value_ranking"]["blocking_state"] is True
    assert data[0]["value_ranking"]["priority_reasons"][0] == "blocking_release"
    assert "priority_score" in data[1]["value_ranking"]


def test_rag_validate_with_and_without_evidence():
    admin_token = _register_and_login("review_admin005", "admin")
    evidence = _create_evidence(admin_token)

    valid_response = client.post(
        "/api/v1/evidence/validate",
        json={
            "text": "Python 是该岗位的重要技能。",
            "evidence_ids": [evidence["evidence_id"]],
            "claims": ["Python 是该岗位的重要技能"],
        },
        headers=_auth_headers(admin_token),
    )
    weak_response = client.post(
        "/api/v1/evidence/validate",
        json={
            "text": "缺少证据的生成内容。",
            "evidence_ids": [],
            "claims": ["缺少证据的声明"],
        },
        headers=_auth_headers(admin_token),
    )

    assert valid_response.status_code == 200
    assert valid_response.json()["data"]["valid"] is True
    assert valid_response.json()["data"]["unsupported_claims"] == []
    assert valid_response.json()["data"]["details"]["mock"] is False
    assert weak_response.status_code == 200
    assert weak_response.json()["data"]["valid"] is False
    assert weak_response.json()["data"]["coverage_score"] < 0.6


def test_rag_retrieve_generate_and_missing_evidence_are_truthful():
    admin_token = _register_and_login("review_admin005b", "admin")
    evidence = _create_evidence(admin_token)

    retrieve_response = client.post(
        "/api/v1/evidence/retrieve",
        json={"query": "Python FastAPI", "top_k": 3},
        headers=_auth_headers(admin_token),
    )
    generate_response = client.post(
        "/api/v1/evidence/generate",
        json={"prompt": "概括岗位技能", "evidence_ids": [evidence["evidence_id"]]},
        headers=_auth_headers(admin_token),
    )
    missing_response = client.post(
        "/api/v1/evidence/generate",
        json={"prompt": "不能引用不存在证据", "evidence_ids": ["missing"]},
        headers=_auth_headers(admin_token),
    )
    low_evidence_response = client.get(
        "/api/v1/evidence/low-evidence-results",
        headers=_auth_headers(admin_token),
    )

    assert retrieve_response.status_code == 200
    retrieved = retrieve_response.json()["data"]
    assert retrieved["results"][0]["evidence_id"] == evidence["evidence_id"]
    assert retrieved["implementation_status"] == "rule_based_keyword_retrieval"
    assert retrieved["mock"] is False
    assert generate_response.status_code == 200
    generated = generate_response.json()["data"]
    assert generated["implementation_status"] == (
        "database_persisted_extractive_evidence_no_llm"
    )
    assert generated["mock"] is False
    assert evidence["raw_text"] in generated["text"]
    assert missing_response.status_code == 404
    assert low_evidence_response.json()["data"] == []


def test_rag_generation_can_be_edited_confirmed_and_audited():
    admin_token = _register_and_login("rag_generation_admin", "admin")
    reviewer_token = _register_and_login("rag_generation_reviewer", "reviewer")
    evidence = _create_evidence(admin_token)
    generated = client.post(
        "/api/v1/evidence/generate",
        json={"prompt": "生成岗位摘要", "evidence_ids": [evidence["evidence_id"]]},
        headers=_auth_headers(admin_token),
    )
    generation_id = generated.json()["data"]["generation_id"]

    edited = client.put(
        f"/api/v1/evidence/generations/{generation_id}",
        json={"text": "人工编辑后的、可追溯的岗位摘要。"},
        headers=_auth_headers(admin_token),
    )
    pending = client.get(
        "/api/v1/evidence/low-evidence-results",
        headers=_auth_headers(admin_token),
    )
    confirmed = client.post(
        f"/api/v1/evidence/generations/{generation_id}/confirm",
        headers=_auth_headers(reviewer_token),
    )
    duplicate_confirm = client.post(
        f"/api/v1/evidence/generations/{generation_id}/confirm",
        headers=_auth_headers(reviewer_token),
    )
    edit_after_confirm = client.put(
        f"/api/v1/evidence/generations/{generation_id}",
        json={"text": "确认后不得覆盖"},
        headers=_auth_headers(admin_token),
    )

    assert edited.status_code == 200
    assert edited.json()["data"]["text"] == "人工编辑后的、可追溯的岗位摘要。"
    assert edited.json()["data"]["need_review"] is True
    assert pending.json()["data"][0]["generation_id"] == generation_id
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "confirmed"
    assert confirmed.json()["data"]["confirmed_by"]
    assert duplicate_confirm.status_code == 409
    assert edit_after_confirm.status_code == 409


def test_rag_requires_admin_or_developer():
    personal_token = _register_and_login("review_user006", "personal_user")

    response = client.get(
        "/api/v1/evidence/low-evidence-results",
        headers=_auth_headers(personal_token),
    )

    assert response.status_code == 403
