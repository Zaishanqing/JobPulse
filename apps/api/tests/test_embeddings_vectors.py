import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import Base, engine, reset_database_data
from app.integrations.registry import reset_integration_registry
from app.main import app
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    reset_integration_registry()
    reset_database_data()
    yield
    reset_database_data()
    reset_integration_registry()


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
    return {"Authorization": f"Bearer {token}"}


def _create_jd(headers: dict) -> str:
    response = client.post(
        "/api/v1/jds/text",
        headers=headers,
        json={
            "title": "Python RAG Engineer",
            "raw_text": "Python FastAPI RAG vector retrieval",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["jd_id"]


def test_embedding_generation_upserts_actual_vector_and_searches_by_type():
    admin = _headers("embedding_admin", "admin")
    jd_id = _create_jd(admin)

    generated = client.post(f"/api/v1/embeddings/jds/{jd_id}", headers=admin)
    assert generated.status_code == 200
    data = generated.json()["data"]
    assert data["object_id"] == jd_id
    assert data["dimension"] == 16
    assert data["embedding_provider"] == "deterministic_local"
    assert data["vector_store_provider"] == "memory"
    assert data["persistent"] is False
    assert data["canonical_status"] == "succeeded"
    assert data["implementation_status"] == "deterministic_local_embedding_in_memory_vector"

    searched = client.post(
        "/api/v1/vectors/search/jds",
        headers=admin,
        json={"query": "Python RAG Engineer", "top_k": 5},
    )
    assert searched.status_code == 200
    search_data = searched.json()["data"]
    assert search_data["persistent"] is False
    assert search_data["results"]
    assert search_data["results"][0]["metadata"] == {
        "object_type": "jd",
        "object_id": jd_id,
    }


def test_embedding_generation_rejects_unknown_or_empty_source():
    admin = _headers("embedding_missing", "admin")
    missing = client.post("/api/v1/embeddings/jds/not-found", headers=admin)
    relation = client.post("/api/v1/embeddings/relations/not-found", headers=admin)

    assert missing.status_code == 404
    assert relation.status_code == 404


def test_similarity_is_deterministic_and_validates_two_inputs():
    admin = _headers("similarity_admin", "admin")
    identical = client.post(
        "/api/v1/vectors/similarity/skill-combo",
        headers=admin,
        json={"skills_a": ["Python", "RAG"], "skills_b": ["Python", "RAG"]},
    )
    different = client.post(
        "/api/v1/vectors/similarity/position-relation",
        headers=admin,
        json={"source": "Python backend", "target": "Kubernetes platform"},
    )
    invalid = client.post(
        "/api/v1/vectors/similarity/skill-combo",
        headers=admin,
        json={"left": "only one side"},
    )

    assert identical.status_code == 200
    assert identical.json()["data"]["similarity"] == 1.0
    assert identical.json()["data"]["implementation_status"] == "deterministic_local_cosine_similarity"
    assert different.status_code == 200
    assert -1 <= different.json()["data"]["similarity"] <= 1
    assert invalid.status_code == 422


def test_vector_search_validation_and_resume_role_matrix():
    admin = _headers("vector_admin", "admin")
    personal = _headers("vector_personal", "personal_user")
    enterprise = _headers("vector_enterprise", "enterprise_user")

    assert client.post(
        "/api/v1/vectors/search/jds", headers=admin, json={"query": ""}
    ).status_code == 422
    assert client.post(
        "/api/v1/vectors/search/jds",
        headers=admin,
        json={"query": "Python", "top_k": 101},
    ).status_code == 422
    assert client.post(
        "/api/v1/vectors/search/resumes", headers=personal, json={"query": "Python"}
    ).status_code == 403
    allowed = client.post(
        "/api/v1/vectors/search/resumes", headers=enterprise, json={"query": "Python"}
    )
    assert allowed.status_code == 200
    assert allowed.json()["data"]["results"] == []
