from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.runtime_database import Base, engine, reset_database_data
from app.integrations.base import IntegrationInputError, IntegrationUnavailableError
from app.integrations.local import LocalFileStorage
from app.integrations.registry import get_integration_registry, reset_integration_registry
from app.main import app
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    storage_root = Path("data") / f"integration_storage_{uuid4().hex}"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(storage_root))
    reset_integration_registry()
    reset_database_data()
    yield
    reset_database_data()
    reset_integration_registry()
    shutil.rmtree(storage_root, ignore_errors=True)


def _admin_headers() -> dict[str, str]:
    create_internal_user("integration_admin", "admin")
    client.post(
        "/api/v1/auth/register",
        json={"role": "admin", "username": "integration_admin", "password": "password123"},
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "integration_admin", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_registry_exposes_all_required_capabilities_with_truthful_statuses():
    statuses = get_integration_registry().statuses()

    assert set(statuses) == {
        "llm",
        "ocr",
        "document_parser",
        "embedding",
        "vector_store",
        "task_queue",
        "file_storage",
        "evidence_retriever",
        "trend_crawler",
    }
    assert statuses["llm"]["enabled"] is False
    assert statuses["ocr"]["implementation_status"] == "disabled_no_external_service"
    assert statuses["embedding"]["implementation_status"] == "rule_based_deterministic"
    assert statuses["vector_store"]["persistent"] is False
    assert statuses["task_queue"]["implementation_status"] == "database_persisted_sync_executor"


def test_disabled_external_providers_fail_with_typed_traceable_errors():
    registry = get_integration_registry()

    with pytest.raises(IntegrationUnavailableError) as llm_error:
        registry.llm.generate("hello")
    with pytest.raises(IntegrationUnavailableError) as ocr_error:
        registry.ocr.extract_text(b"image", "image/png")
    with pytest.raises(IntegrationUnavailableError) as crawler_error:
        registry.trend_crawler.fetch({"url": "https://example.invalid"})

    assert llm_error.value.as_dict()["capability"] == "llm"
    assert ocr_error.value.as_dict()["provider"] == "disabled"
    assert crawler_error.value.as_dict()["retryable"] is False


def test_document_embedding_and_vector_local_fallbacks_are_deterministic():
    registry = get_integration_registry()
    text = registry.document_parser.extract_text("岗位技能".encode(), "text/plain")
    first_vector = registry.embedding.embed(text)
    second_vector = registry.embedding.embed(text)
    registry.vector_store.upsert("doc-1", first_vector, {"kind": "test"})

    assert text == "岗位技能"
    assert first_vector == second_vector
    assert len(first_vector) == settings.EMBEDDING_DIMENSION
    assert registry.vector_store.search(second_vector, top_k=1)[0]["object_id"] == "doc-1"
    with pytest.raises(IntegrationUnavailableError):
        registry.document_parser.extract_text(b"pdf", "application/pdf")


def test_task_storage_and_evidence_local_adapters_have_stable_contracts():
    registry = get_integration_registry()
    result = registry.task_queue.execute("sum", {"values": [1, 2]}, lambda payload: sum(payload["values"]))
    storage = registry.file_storage
    storage.save("evidence.txt", b"Python RAG evidence")
    retrieved = registry.evidence_retriever.retrieve(
        "Python RAG",
        [
            {"id": "one", "text": "Python RAG evidence"},
            {"id": "two", "text": "unrelated"},
        ],
    )

    assert result == 3
    assert storage.read("evidence.txt") == b"Python RAG evidence"
    assert retrieved[0]["id"] == "one"
    storage.delete("evidence.txt")
    with pytest.raises(IntegrationInputError):
        storage.read("evidence.txt")
    with pytest.raises(IntegrationInputError):
        LocalFileStorage("data").save("../escape.txt", b"bad")


def test_system_status_reports_adapter_truth_and_redacts_database_path():
    response = client.get("/api/v1/system/status", headers=_admin_headers())
    database = client.get("/api/v1/system/status/databases", headers=_admin_headers())

    assert response.status_code == 200
    assert len(response.json()["data"]["capabilities"]) == 9
    assert response.json()["data"]["components"]["vector_db"]["provider"] == "memory"
    assert database.json()["data"]["database_url"] == "sqlite:///<redacted>"
    assert "test.db" not in database.text
