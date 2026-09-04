import pytest
from fastapi.testclient import TestClient

from app.domain.text_cleaning import clean_jd_text
from tests.runtime_database import reset_database_data, Base, engine
from app.main import app
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _headers(username: str, role: str) -> dict:
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
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    if role == "enterprise_user":
        response = client.post(
            "/api/v1/enterprises",
            headers=headers,
            json={"enterprise_name": f"{username} enterprise"},
        )
        assert response.status_code == 200
    return headers


def test_plain_text_resume_file_is_really_extracted_and_can_be_parsed():
    headers = _headers("extract_resume_text", "personal_user")
    raw_text = "真实文本简历：Python FastAPI Docker"
    uploaded = client.post(
        "/api/v1/resumes/file",
        headers=headers,
        files={"file": ("resume.txt", raw_text.encode(), "text/plain")},
    )

    assert uploaded.status_code == 200
    data = uploaded.json()["data"]
    assert data["raw_text"] == raw_text
    assert data["input_extraction_status"] == "completed"
    assert data["input_provider"] == "plain_text_local"
    assert data["implementation_status"] == "adapter_extracted_input"
    parsed = client.post(f"/api/v1/resumes/{data['resume_id']}/parse", headers=headers)
    assert parsed.status_code == 200
    assert parsed.json()["data"]["canonical_status"] == "succeeded"


def test_unsupported_resume_document_fails_truthfully_but_manual_edit_remains():
    headers = _headers("extract_resume_pdf", "personal_user")
    uploaded = client.post(
        "/api/v1/resumes/file",
        headers=headers,
        files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert uploaded.status_code == 200
    data = uploaded.json()["data"]
    assert data["raw_text"] == ""
    assert data["parse_status"] == "failed"
    assert data["input_extraction_status"] == "failed"
    assert data["input_error_code"] == "IntegrationUnavailableError"
    assert "占位文本" not in str(data)
    assert client.post(f"/api/v1/resumes/{data['resume_id']}/parse", headers=headers).status_code == 409

    manual = client.put(
        f"/api/v1/resumes/{data['resume_id']}/parse-result",
        headers=headers,
        json={
            "skills": [{"raw_skill": "Python", "normalized_skill_id": "skill_python"}],
            "parse_confidence": 1.0,
            "need_review": False,
        },
    )
    assert manual.status_code == 200
    assert manual.json()["data"]["skills"][0]["raw_skill"] == "Python"


def test_disabled_ocr_resume_and_jd_image_never_create_placeholder_text():
    personal = _headers("extract_resume_image", "personal_user")
    enterprise = _headers("extract_jd_image", "enterprise_user")
    resume = client.post(
        "/api/v1/resumes/image",
        headers=personal,
        files={"file": ("resume.png", b"fake-png", "image/png")},
    )
    jd = client.post(
        "/api/v1/jds/image?title=Image%20JD",
        headers=enterprise,
        files={"file": ("jd.png", b"fake-png", "image/png")},
    )

    assert resume.status_code == 200
    assert jd.status_code == 200
    for data in (resume.json()["data"], jd.json()["data"]):
        assert data["raw_text"] == ""
        assert data["parse_status"] == "failed"
        assert data["input_provider"] == "disabled"
        assert data["input_error_code"] == "IntegrationUnavailableError"
        assert "占位文本" not in str(data)


def test_jd_text_file_uses_parser_and_failed_pdf_can_be_manually_corrected():
    headers = _headers("extract_jd_file", "enterprise_user")
    raw_text = "岗位职责：Java Spring Boot 后端开发"
    text_jd = client.post(
        "/api/v1/jds/file",
        headers=headers,
        files={"file": ("jd.txt", raw_text.encode(), "text/plain")},
        data={"title": "Java Engineer"},
    )
    assert text_jd.status_code == 200
    assert text_jd.json()["data"]["raw_text"] == clean_jd_text(raw_text)
    assert text_jd.json()["data"]["input_extraction_status"] == "completed"

    pdf_jd = client.post(
        "/api/v1/jds/file",
        headers=headers,
        files={"file": ("jd.pdf", b"%PDF fake", "application/pdf")},
        data={"title": "PDF Engineer"},
    )
    assert pdf_jd.status_code == 200
    failed = pdf_jd.json()["data"]
    assert failed["raw_text"] == ""
    assert failed["parse_status"] == "failed"
    assert client.post(
        f"/api/v1/jds/{failed['jd_id']}/parse",
        headers=headers,
        json={"extraction_mode": "rule"},
    ).status_code == 409

    edited = client.put(
        f"/api/v1/jds/{failed['jd_id']}/raw",
        headers=headers,
        json={"raw_text": "人工补录：Python RAG 岗位"},
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["input_extraction_status"] == "manually_edited"
    assert edited.json()["data"]["input_error_code"] is None
    assert client.post(
        f"/api/v1/jds/{failed['jd_id']}/parse",
        headers=headers,
        json={"extraction_mode": "rule"},
    ).status_code == 200
