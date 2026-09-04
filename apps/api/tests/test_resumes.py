import uuid

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from app.main import app
from app.models.matching_service_reference import MatchingServiceReference
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.resume_skill import ResumeSkill
from app.models.source_cv import CVExtractionTask, SourceCV, SourceCVVersion
from app.models.task_record import TaskRecord
from app.models.outbox_message import OutboxMessage
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


def _resume_text() -> str:
    return "参与岗位能力图谱系统，使用 Python、FastAPI、RAG、Neo4j 和 Docker 完成后端服务。"


def _create_resume(token: str) -> str:
    response = client.post(
        "/api/v1/resumes/text",
        json={"raw_text": _resume_text()},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()["data"]["resume_id"]


def test_personal_user_create_text_resume():
    token = _register_and_login("resume_user001", "personal_user")

    response = client.post(
        "/api/v1/resumes/text",
        json={"raw_text": _resume_text()},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume_id"]
    assert data["source_type"] == "text"
    assert data["parse_status"] == "pending"
    assert data["display_name"] == "文本简历"
    assert data["original_filename"] is None


def test_personal_user_can_rename_resume():
    token = _register_and_login("resume_rename001", "personal_user")
    resume_id = _create_resume(token)

    response = client.patch(
        f"/api/v1/resumes/{resume_id}",
        json={"display_name": "大模型应用开发简历"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["display_name"] == "大模型应用开发简历"
    listed = client.get("/api/v1/resumes/me", headers=_auth_headers(token))
    assert listed.json()["data"][0]["display_name"] == "大模型应用开发简历"


def test_start_resume_parse():
    token = _register_and_login("resume_user002", "personal_user")
    resume_id = _create_resume(token)

    response = client.post(
        f"/api/v1/resumes/{resume_id}/parse",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume_id"] == resume_id
    assert data["status"] == "completed"
    assert data["parse_result"]["parse_confidence"] == 0.7
    assert data["parse_result"]["projects"] == []
    with SessionLocal() as session:
        result_id = data["parse_result"]["parse_result_id"]
        assert session.get(ResumeParseResult, result_id) is not None
        task = session.get(TaskRecord, data["task_id"])
        assert task is not None
        assert task.task_type == "resume_parse"
        assert task.result_reference == f"resume_parse_result:{result_id}"


def test_get_resume_parse_result():
    token = _register_and_login("resume_user003", "personal_user")
    resume_id = _create_resume(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=_auth_headers(token))

    response = client.get(
        f"/api/v1/resumes/{resume_id}/parse-result",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume_id"] == resume_id
    assert any(skill["raw_skill"] == "Python" for skill in data["skills"])
    assert any(skill["raw_skill"] == "FastAPI" for skill in data["skills"])


def test_edit_resume_parse_result():
    token = _register_and_login("resume_user004", "personal_user")
    resume_id = _create_resume(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=_auth_headers(token))

    response = client.put(
        f"/api/v1/resumes/{resume_id}/parse-result",
        json={
            "projects": [
                {
                    "project_name": "岗位能力图谱系统",
                    "description": "负责简历解析模块",
                    "skills": ["Python"],
                }
            ],
            "parse_confidence": 0.95,
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["projects"][0]["project_name"] == "岗位能力图谱系统"
    assert data["parse_confidence"] == 0.95
    assert data["need_review"] is False


def test_confirm_resume_parse_result():
    token = _register_and_login("resume_user005", "personal_user")
    resume_id = _create_resume(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=_auth_headers(token))

    response = client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["need_review"] is False


def test_generate_skill_profile():
    token = _register_and_login("resume_user006", "personal_user")
    resume_id = _create_resume(token)
    headers = _auth_headers(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers)
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm", headers=headers
    ).status_code == 200

    response = client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume_id"] == resume_id
    assert any(skill["raw_skill"] == "Python" for skill in data["skills"])


def test_generate_skill_profile_rejects_unconfirmed_parse_result():
    token = _register_and_login("resume_user_need_review", "personal_user")
    resume_id = _create_resume(token)
    headers = _auth_headers(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers)

    response = client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )

    assert response.status_code == 409
    with SessionLocal() as session:
        assert (
            session.query(ResumeSkill)
            .filter(ResumeSkill.resume_id == resume_id)
            .count()
            == 0
        )
        assert not session.query(OutboxMessage).filter(
            OutboxMessage.event_type == "cv_profile_updated"
        ).all()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "skills",
            [
                {
                    "raw_skill": "Java",
                    "normalized_skill_id": "skill_java",
                    "confidence": 0.95,
                    "evidence": "manual edit",
                }
            ],
        ),
        (
            "projects",
            [
                {
                    "project_name": "Java service",
                    "description": "A revised project",
                    "skills": ["Java"],
                }
            ],
        ),
    ],
)
def test_profile_affecting_parse_edits_clear_derived_skills(field, value):
    token = _register_and_login(f"resume_invalidate_{field}", "personal_user")
    resume_id = _create_resume(token)
    headers = _auth_headers(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers)
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm", headers=headers
    ).status_code == 200
    generated = client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )
    assert generated.status_code == 200

    updated = client.put(
        f"/api/v1/resumes/{resume_id}/parse-result",
        json={field: value},
        headers=headers,
    )

    assert updated.status_code == 200
    with SessionLocal() as session:
        assert (
            session.query(ResumeSkill)
            .filter(ResumeSkill.resume_id == resume_id)
            .count()
            == 0
        )


def test_confidence_only_parse_edit_preserves_derived_skills():
    token = _register_and_login("resume_keep_profile", "personal_user")
    resume_id = _create_resume(token)
    headers = _auth_headers(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers)
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm", headers=headers
    ).status_code == 200
    generated = client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )
    original_ids = {
        item["resume_skill_id"] for item in generated.json()["data"]["skills"]
    }

    updated = client.put(
        f"/api/v1/resumes/{resume_id}/parse-result",
        json={"parse_confidence": 0.99},
        headers=headers,
    )
    retained = client.get(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )

    assert updated.status_code == retained.status_code == 200
    assert {
        item["resume_skill_id"] for item in retained.json()["data"]["skills"]
    } == original_ids


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("personal_user", 200),
        ("admin", 200),
        ("developer", 200),
        ("reviewer", 403),
    ],
)
def test_resume_parse_result_write_authorization(role, expected_status):
    owner_token = _register_and_login(f"resume_owner_for_{role}", "personal_user")
    resume_id = _create_resume(owner_token)
    client.post(
        f"/api/v1/resumes/{resume_id}/parse",
        headers=_auth_headers(owner_token),
    )
    actor_token = (
        owner_token
        if role == "personal_user"
        else _register_and_login(f"resume_writer_{role}", role)
    )

    response = client.put(
        f"/api/v1/resumes/{resume_id}/parse-result",
        json={"parse_confidence": 0.91},
        headers=_auth_headers(actor_token),
    )

    assert response.status_code == expected_status


def test_reviewer_can_read_but_cannot_run_resume_write_operations():
    owner_token = _register_and_login("resume_reviewer_owner", "personal_user")
    reviewer_token = _register_and_login("resume_read_only_reviewer", "reviewer")
    resume_id = _create_resume(owner_token)
    owner_headers = _auth_headers(owner_token)
    reviewer_headers = _auth_headers(reviewer_token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=owner_headers)

    assert client.get(
        f"/api/v1/resumes/{resume_id}",
        headers=reviewer_headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse",
        headers=reviewer_headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm",
        headers=reviewer_headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=reviewer_headers,
    ).status_code == 403
    assert client.delete(
        f"/api/v1/resumes/{resume_id}",
        headers=reviewer_headers,
    ).status_code == 403


def test_get_skill_profile():
    token = _register_and_login("resume_user007", "personal_user")
    resume_id = _create_resume(token)
    headers = _auth_headers(token)
    client.post(f"/api/v1/resumes/{resume_id}/parse", headers=headers)
    assert client.post(
        f"/api/v1/resumes/{resume_id}/parse-result/confirm", headers=headers
    ).status_code == 200
    client.post(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )

    response = client.get(
        f"/api/v1/resumes/{resume_id}/skill-profile",
        headers=headers,
    )

    assert response.status_code == 200
    assert len(response.json()["data"]["skills"]) >= 1


def test_enterprise_user_cannot_view_other_resume_detail():
    personal_token = _register_and_login("resume_user008", "personal_user")
    enterprise_token = _register_and_login("resume_enterprise008", "enterprise_user")
    resume_id = _create_resume(personal_token)

    response = client.get(
        f"/api/v1/resumes/{resume_id}",
        headers=_auth_headers(enterprise_token),
    )

    assert response.status_code == 403
    assert response.json()["code"] == 403


def test_admin_can_view_resume_detail():
    personal_token = _register_and_login("resume_user009", "personal_user")
    admin_token = _register_and_login("resume_admin009", "admin")
    resume_id = _create_resume(personal_token)

    response = client.get(
        f"/api/v1/resumes/{resume_id}",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["resume_id"] == resume_id


def test_delete_resume_clears_match_references_and_detaches_cv_task():
    token = _register_and_login("resume_delete010", "personal_user")
    resume_id = _create_resume(token)
    with SessionLocal() as session:
        resume = session.get(Resume, resume_id)
        source = SourceCV(
            id=str(uuid.uuid4()),
            owner_id=resume.user_id,
            source_platform="test-resume-delete",
            source_record_id=f"test:{resume_id}",
        )
        version = SourceCVVersion(
            id=str(uuid.uuid4()),
            source_cv_id=source.id,
            raw_text=resume.raw_text or "test resume",
            source_version="1",
        )
        session.add(source)
        session.flush()
        session.add(version)
        session.flush()
        task = CVExtractionTask(
            id=str(uuid.uuid4()),
            source_cv_version_id=version.id,
            request_id=f"test:delete:{resume_id}",
            status="succeeded",
            resume_id=resume_id,
        )
        reference = MatchingServiceReference(
            task_id=f"task-delete-{resume_id}",
            evaluation_id=f"eval-delete-{resume_id}",
            user_id=resume.user_id,
            tenant_id=f"personal:{resume.user_id}",
            resume_id=resume_id,
            position_id="position-1",
            provider="matching-service",
            status="current",
            idempotency_key=f"idem-delete-{resume_id}",
            access_scope=f"user:{resume.user_id}",
            source_version="cv=cv-v1|position=position-v1",
            cv_profile_version="cv-v1",
            position_profile_version="position-v1",
            taxonomy_version="taxonomy-v1",
            graph_version="graph-v1",
            algorithm_version="matching-v1",
        )
        session.add(task)
        session.add(reference)
        session.commit()
        task_id = task.id

    response = client.delete(
        f"/api/v1/resumes/{resume_id}",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["deleted"] is True
    listed = client.get("/api/v1/resumes/me", headers=_auth_headers(token))
    assert all(item["resume_id"] != resume_id for item in listed.json()["data"])
    with SessionLocal() as session:
        assert session.get(Resume, resume_id) is None
        assert (
            session.query(MatchingServiceReference)
            .filter_by(resume_id=resume_id)
            .count()
            == 0
        )
        # CV 抽取任务及其快照是审计链记录,删除简历后保留但解除关联。
        task = session.get(CVExtractionTask, task_id)
        assert task is not None
        assert task.resume_id is None
