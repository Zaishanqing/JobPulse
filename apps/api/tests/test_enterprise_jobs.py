import pytest
from dataclasses import replace
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, SessionLocal
from app.main import app
from app.infrastructure.recruitment import SqlAlchemyRecruitmentUnitOfWork
from app.models.enterprise_job import EnterpriseJob
from app.models.outbox_message import OutboxMessage
from app.profile_index_events import PROFILE_INDEX_EVENT_TYPE, profile_index_event, tenant_ref
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


def _enterprise_payload() -> dict:
    return {
        "enterprise_name": "示例科技有限公司",
        "industry": "人工智能",
        "scale": "100-500人",
        "location": "武汉",
        "description": "专注大模型应用开发",
    }


def _job_payload(enterprise_id: str) -> dict:
    return {
        "enterprise_id": enterprise_id,
        "title": "大模型应用开发工程师",
        "standard_position_id": "pos_llm_app",
        "jd_text": "岗位职责：负责 RAG 应用开发。",
        "headcount": 3,
        "location": "武汉",
        "employment_type": "full_time",
        "salary_min": 15000,
        "salary_max": 25000,
        "salary_unit": "month",
        "status": "draft",
    }


def _create_enterprise(token: str) -> str:
    response = client.post(
        "/api/v1/enterprises",
        json=_enterprise_payload(),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]["enterprise_id"]


def _create_job(token: str, enterprise_id: str) -> str:
    response = client.post(
        "/api/v1/enterprise-jobs",
        json=_job_payload(enterprise_id),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]["enterprise_job_id"]


def test_enterprise_user_creates_enterprise_profile():
    token = _register_and_login("enterprise001", "enterprise_user")

    response = client.post(
        "/api/v1/enterprises",
        json=_enterprise_payload(),
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["enterprise_id"]
    assert payload["data"]["enterprise_name"] == "示例科技有限公司"
    assert payload["data"]["status"] == "active"


def test_enterprise_user_creates_enterprise_job():
    token = _register_and_login("enterprise002", "enterprise_user")
    enterprise_id = _create_enterprise(token)

    response = client.post(
        "/api/v1/enterprise-jobs",
        json=_job_payload(enterprise_id),
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["enterprise_id"] == enterprise_id
    assert payload["data"]["title"] == "大模型应用开发工程师"
    assert payload["data"]["salary_min"] == 15000
    assert payload["data"]["salary_max"] == 25000
    assert payload["data"]["salary_unit"] == "month"
    assert payload["data"]["status"] == "draft"


def test_enterprise_job_round_trips_requirement_graph():
    token = _register_and_login("enterprise_graph", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    payload = _job_payload(enterprise_id)
    payload["requirement_graph"] = {
        "graph_version": "requirement-graph.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-1",
                "group_type": "must",
                "priority": "required",
                "children": [
                    {
                        "node_type": "requirement_ref",
                        "ref_id": "req-python",
                        "aspect": "Python",
                    }
                ],
                "evidence": {
                    "source_id": "jd:block:1",
                    "quote": "Must know Python",
                    "start": 0,
                    "end": 15,
                    "alignment": "exact",
                },
                "confidence": 0.9,
            }
        ],
        "unresolved_items": [],
    }

    response = client.post(
        "/api/v1/enterprise-jobs",
        json=payload,
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    graph = response.json()["data"]["requirement_graph"]
    assert graph["graph_version"] == "requirement-graph.v1"
    assert graph["groups"][0]["requirement_group_id"] == "group-1"


def test_get_enterprise_job_list():
    token = _register_and_login("enterprise003", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)

    response = client.get(
        "/api/v1/enterprise-jobs",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    jobs = response.json()["data"]
    assert len(jobs) == 1
    assert jobs[0]["enterprise_job_id"] == job_id


def test_update_enterprise_job_content():
    token = _register_and_login("enterprise004", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)

    response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}",
        json={"title": "RAG 应用开发工程师", "headcount": 4},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "RAG 应用开发工程师"
    assert data["headcount"] == 4


def test_publish_pause_resume_cancel_enterprise_job():
    token = _register_and_login("enterprise005", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)

    publish_response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/publish",
        headers=_auth_headers(token),
    )
    pause_response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/pause",
        headers=_auth_headers(token),
    )
    resume_response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/resume",
        headers=_auth_headers(token),
    )
    cancel_response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/cancel",
        headers=_auth_headers(token),
    )

    assert publish_response.status_code == 200
    assert publish_response.json()["data"]["status"] == "published"
    assert pause_response.status_code == 200
    assert pause_response.json()["data"]["status"] == "paused"
    assert resume_response.status_code == 200
    assert resume_response.json()["data"]["status"] == "published"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == "cancelled"


def test_enterprise_job_update_rejects_illegal_status_transition():
    token = _register_and_login("enterprise_illegal_transition", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)
    headers = _auth_headers(token)

    response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}",
        json={"status": "paused"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["message"] == (
        "Invalid enterprise job status transition: draft -> paused"
    )
    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}", headers=headers
    ).json()["data"]["status"] == "draft"


def test_cancelled_enterprise_job_cannot_resume():
    token = _register_and_login("enterprise_cancelled_terminal", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)
    headers = _auth_headers(token)
    assert client.put(
        f"/api/v1/enterprise-jobs/{job_id}/cancel", headers=headers
    ).status_code == 200

    response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/resume", headers=headers
    )

    assert response.status_code == 422
    assert response.json()["message"] == (
        "Invalid enterprise job status transition: cancelled -> published"
    )
    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}", headers=headers
    ).json()["data"]["status"] == "cancelled"


def test_enterprise_job_publish_skips_vector_event_when_index_is_disabled():
    original = app.state.container
    app.state.container = replace(
        original,
        recruitment=replace(
            original.recruitment,
            jobs=replace(original.recruitment.jobs, vector_index_enabled=False),
        ),
    )
    try:
        token = _register_and_login("enterprise_index_event", "enterprise_user")
        enterprise_id = _create_enterprise(token)
        job_id = _create_job(token, enterprise_id)

        response = client.put(
            f"/api/v1/enterprise-jobs/{job_id}/publish",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        with SessionLocal() as session:
            assert session.query(OutboxMessage).filter(
                OutboxMessage.event_type == PROFILE_INDEX_EVENT_TYPE
            ).count() == 0
    finally:
        app.state.container = original


def test_business_rollback_removes_profile_change_and_outbox_together():
    token = _register_and_login("enterprise_index_rollback", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)
    with SessionLocal() as session:
        original_title = session.get(EnterpriseJob, job_id).title
        original_outbox_count = session.query(OutboxMessage).count()

    with pytest.raises(RuntimeError, match="force rollback"):
        with SqlAlchemyRecruitmentUnitOfWork(SessionLocal) as uow:
            uow.jobs.update(job_id, {"title": "must rollback"})
            uow.add_outbox(
                profile_index_event(
                    vector_event_type="position_profile_updated",
                    entity_type="position",
                    entity_id=job_id,
                    tenant=tenant_ref(enterprise_id),
                    target_type="enterprise_job",
                )
            )
            raise RuntimeError("force rollback")

    with SessionLocal() as session:
        assert session.get(EnterpriseJob, job_id).title == original_title
        assert session.query(OutboxMessage).count() == original_outbox_count


def test_update_enterprise_job_headcount():
    token = _register_and_login("enterprise006", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)

    response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/headcount",
        json={"headcount": 5, "reason": "业务扩张"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enterprise_job_id"] == job_id
    assert data["old_headcount"] == 3
    assert data["new_headcount"] == 5


def test_set_enterprise_job_skill_weights():
    token = _register_and_login("enterprise007", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)

    response = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
        json={
            "weights": [
                {"skill_id": "skill_python", "weight": 0.25, "is_required": True},
                {"skill_id": "skill_rag", "weight": 0.35, "is_required": True},
                {"skill_id": "skill_docker", "weight": 0.1, "is_bonus": True},
            ]
        },
        headers=_auth_headers(token),
    )
    get_response = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["updated_count"] == 3
    assert get_response.status_code == 200
    assert len(get_response.json()["data"]) == 3


def test_required_and_bonus_skill_endpoints_persist_and_are_idempotent():
    token = _register_and_login("enterprise_skill_flags", "enterprise_user")
    enterprise_id = _create_enterprise(token)
    job_id = _create_job(token, enterprise_id)
    client.put(
        f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
        headers=_auth_headers(token),
        json={
            "weights": [
                {"skill_id": "skill_python", "weight": 0.25},
                {"skill_id": "skill_docker", "weight": 0.1, "is_bonus": True},
            ]
        },
    )

    required = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/required-skills",
        headers=_auth_headers(token),
        json={"required_skills": ["skill_python", {"skill_id": "skill_rag"}]},
    )
    bonus = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/bonus-skills",
        headers=_auth_headers(token),
        json={"skills": ["skill_docker"]},
    )
    repeated = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/required-skills",
        headers=_auth_headers(token),
        json={"required_skills": ["skill_python", "skill_rag", "skill_rag"]},
    )
    stored = client.get(
        f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
        headers=_auth_headers(token),
    ).json()["data"]

    assert required.status_code == 200
    assert bonus.status_code == 200
    assert repeated.status_code == 200
    assert required.json()["data"]["implementation_status"] == "database_persisted_skill_classification"
    assert {item["skill_id"] for item in repeated.json()["data"]["required_skills"]} == {
        "skill_python",
        "skill_rag",
    }
    assert len(stored) == 3
    by_id = {item["skill_id"]: item for item in stored}
    assert by_id["skill_python"]["is_required"] is True
    assert by_id["skill_rag"]["is_required"] is True
    assert by_id["skill_docker"]["is_bonus"] is True


def test_skill_classification_validates_input_and_enterprise_ownership():
    owner = _register_and_login("enterprise_skill_owner", "enterprise_user")
    other = _register_and_login("enterprise_skill_other", "enterprise_user")
    enterprise_id = _create_enterprise(owner)
    _create_enterprise(other)
    job_id = _create_job(owner, enterprise_id)

    invalid = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/required-skills",
        headers=_auth_headers(owner),
        json={"required_skills": "skill_python"},
    )
    forbidden = client.put(
        f"/api/v1/enterprise-jobs/{job_id}/bonus-skills",
        headers=_auth_headers(other),
        json={"bonus_skills": ["skill_docker"]},
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403


def test_enterprise_cannot_read_or_mutate_another_enterprise_job():
    owner = _register_and_login("enterprise_job_scope_owner", "enterprise_user")
    other = _register_and_login("enterprise_job_scope_other", "enterprise_user")
    owner_enterprise = _create_enterprise(owner)
    _create_enterprise(other)
    job_id = _create_job(owner, owner_enterprise)
    owner_headers = _auth_headers(owner)
    other_headers = _auth_headers(other)

    listed = client.get("/api/v1/enterprise-jobs", headers=other_headers)
    requests = [
        client.get(f"/api/v1/enterprise-jobs/{job_id}", headers=other_headers),
        client.put(
            f"/api/v1/enterprise-jobs/{job_id}",
            headers=other_headers,
            json={"title": "越权修改"},
        ),
        client.put(
            f"/api/v1/enterprise-jobs/{job_id}/publish", headers=other_headers
        ),
        client.put(
            f"/api/v1/enterprise-jobs/{job_id}/headcount",
            headers=other_headers,
            json={"headcount": 9},
        ),
        client.get(
            f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
            headers=other_headers,
        ),
        client.put(
            f"/api/v1/enterprise-jobs/{job_id}/skill-weights",
            headers=other_headers,
            json={"weights": []},
        ),
        client.post(
            f"/api/v1/enterprise-jobs/{job_id}/skill-weights/reset",
            headers=other_headers,
        ),
        client.delete(f"/api/v1/enterprise-jobs/{job_id}", headers=other_headers),
    ]

    assert all(item["enterprise_job_id"] != job_id for item in listed.json()["data"])
    assert [response.status_code for response in requests] == [403] * len(requests)
    assert client.get(
        f"/api/v1/enterprise-jobs/{job_id}", headers=owner_headers
    ).status_code == 200


def test_personal_user_cannot_create_enterprise_job():
    personal_token = _register_and_login("personal001", "personal_user")

    response = client.post(
        "/api/v1/enterprise-jobs",
        json=_job_payload("enterprise_fake"),
        headers=_auth_headers(personal_token),
    )

    assert response.status_code == 403
    assert response.json()["code"] == 403


def test_admin_can_view_all_enterprise_jobs():
    enterprise_token = _register_and_login("enterprise008", "enterprise_user")
    admin_token = _register_and_login("admin001", "admin")
    enterprise_id = _create_enterprise(enterprise_token)
    job_id = _create_job(enterprise_token, enterprise_id)

    response = client.get(
        "/api/v1/enterprise-jobs",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["enterprise_job_id"] == job_id


def test_personal_user_reads_only_published_jobs_through_public_contract():
    enterprise_token = _register_and_login("enterprise_public_jobs", "enterprise_user")
    personal_token = _register_and_login("personal_public_jobs", "personal_user")
    enterprise_id = _create_enterprise(enterprise_token)
    draft_id = _create_job(enterprise_token, enterprise_id)
    published_payload = _job_payload(enterprise_id)
    published_payload.update(
        {
            "title": "Published RAG Engineer",
            "status": "published",
            "requirement_graph": {
                "graph_version": "requirement-graph.v1",
                "status": "complete",
                "groups": [],
                "unresolved_items": [],
            },
        }
    )
    published = client.post(
        "/api/v1/enterprise-jobs",
        headers=_auth_headers(enterprise_token),
        json=published_payload,
    ).json()["data"]

    response = client.get(
        "/api/v1/published-enterprise-jobs",
        headers=_auth_headers(personal_token),
    )

    assert response.status_code == 200
    jobs = response.json()["data"]
    assert [item["enterprise_job_id"] for item in jobs] == [
        published["enterprise_job_id"]
    ]
    public_job = jobs[0]
    assert public_job["enterprise_name"] == "示例科技有限公司"
    assert public_job["status"] == "published"
    assert public_job["jd_text"] == "岗位职责：负责 RAG 应用开发。"
    assert "enterprise_id" not in public_job
    assert "standard_position_id" not in public_job
    assert "requirement_graph" not in public_job
    assert "created_at" not in public_job
    assert client.get(
        f"/api/v1/published-enterprise-jobs/{draft_id}",
        headers=_auth_headers(personal_token),
    ).status_code == 404


def test_published_job_detail_stays_separate_from_enterprise_management_reads():
    enterprise_token = _register_and_login("enterprise_public_detail", "enterprise_user")
    personal_token = _register_and_login("personal_public_detail", "personal_user")
    admin_token = _register_and_login("admin_public_detail", "admin")
    enterprise_id = _create_enterprise(enterprise_token)
    job_id = _create_job(enterprise_token, enterprise_id)
    assert client.put(
        f"/api/v1/enterprise-jobs/{job_id}/publish",
        headers=_auth_headers(enterprise_token),
    ).status_code == 200

    detail = client.get(
        f"/api/v1/published-enterprise-jobs/{job_id}",
        headers=_auth_headers(personal_token),
    )
    personal_management = client.get(
        "/api/v1/enterprise-jobs", headers=_auth_headers(personal_token)
    )
    owner_management = client.get(
        f"/api/v1/enterprise-jobs/{job_id}",
        headers=_auth_headers(enterprise_token),
    )
    admin_management = client.get(
        "/api/v1/enterprise-jobs", headers=_auth_headers(admin_token)
    )
    admin_public = client.get(
        "/api/v1/published-enterprise-jobs", headers=_auth_headers(admin_token)
    )

    assert detail.status_code == 200
    assert detail.json()["data"]["enterprise_job_id"] == job_id
    assert personal_management.status_code == 403
    assert owner_management.status_code == 200
    assert owner_management.json()["data"]["enterprise_id"] == enterprise_id
    assert admin_management.status_code == 200
    assert admin_management.json()["data"][0]["enterprise_job_id"] == job_id
    assert admin_public.status_code == 403
