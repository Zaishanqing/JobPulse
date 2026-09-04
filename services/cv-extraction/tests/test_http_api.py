from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault(
    "CV_EXTRACTION_INTERNAL_TOKEN",
    "test-cv-extraction-token-with-at-least-32-characters",
)

from fastapi.testclient import TestClient

from api.application import (
    VALIDATION_POLICY_VERSION,
    CVExtractionApplicationService,
    CVExtractionDocument,
)
from api.config import Settings
from api.main import build_app, create_app

TOKEN = "test-cv-extraction-token-with-at-least-32-characters"


class FakeService:
    def extract(self, document):
        return {
            "contract_version": "cv-extraction-http.v1",
            "document_id": document.document_id,
            "execution": {
                "provider": "test",
                "model": "test-model",
                "prompt_version": "test-prompt",
                "schema_version": "test-schema",
                "normalization_version": "test-normalization",
            "taxonomy_version": "skill-taxonomy-snapshot.v1",
                "latency_ms": 1,
            },
            "extraction_result": {
                "document_id": document.document_id,
                "education": [],
                "work_experience": [],
                "project_experience": [],
                "skills": [],
                "languages": [],
                "certificates": [],
                "awards": [],
                "self_evaluation": [],
            },
            "normalized_result": {
                "document_id": document.document_id,
                "normalized_skills": [],
                "unresolved_items": [],
            },
            "review_flags": [],
        }

    def extract_batch(self, documents):
        return [self.extract(document) for document in documents]

    def extract_v2(self, document):
        result = self.extract(document)
        result["contract_version"] = "cv-extraction-http.v2"
        result["skill_taxonomy"] = {
            "schema_version": "skill-taxonomy-projection.v1",
            "taxonomy_version": "skill-taxonomy-snapshot.v1",
            "skills": [],
        }
        return result

    def extract_v3(self, document):
        result = self.extract_v2(document)
        result["contract_version"] = "cv-extraction-http.v3"
        result["normalized_result"]["position_classifications"] = []
        return result


def _client() -> TestClient:
    settings = Settings(CV_EXTRACTION_INTERNAL_TOKEN=TOKEN)
    return TestClient(create_app(settings, FakeService()))


def test_health_and_readiness_are_public_but_extraction_requires_token():
    client = _client()
    assert client.get("/health").status_code == 200
    assert client.get("/readiness").json()["status"] == "ready"
    payload = {"document_id": "cv-1", "raw_text": "Python 开发工程师"}
    assert client.post("/api/v1/cv-extractions", json=payload).status_code == 404
    v2 = client.post(
        "/api/v2/cv-extractions",
        json=payload,
        headers={"X-Internal-Token": TOKEN},
    )
    assert v2.status_code == 200
    assert v2.json()["data"]["contract_version"] == "cv-extraction-http.v2"
    v3 = client.post(
        "/api/v3/cv-extractions",
        json=payload,
        headers={"X-Internal-Token": TOKEN},
    )
    assert v3.status_code == 200
    assert v3.json()["data"]["contract_version"] == "cv-extraction-http.v3"


def test_unconfigured_runtime_is_rejected_during_application_startup(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.delenv("CV_EXTRACTION_INTERNAL_TOKEN")

    with pytest.raises(ValidationError), TestClient(build_app()):
        pass


def test_provider_key_is_not_required_for_health_startup(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    settings = Settings(CV_EXTRACTION_INTERNAL_TOKEN=TOKEN)

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/readiness").status_code == 200


def test_v1_batch_formal_endpoint_is_removed():
    response = _client().post(
        "/api/v1/cv-extractions/batch",
        json={
            "documents": [
                {"document_id": "cv-1", "raw_text": "Python"},
                {"document_id": "cv-2", "raw_text": "Java"},
            ]
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == 404


def test_application_reports_execution_metadata_from_settings():
    settings = Settings(
        CV_EXTRACTION_INTERNAL_TOKEN=TOKEN,
        PROVIDER="provider-under-test",
        MODEL="model-under-test",
        PROMPT_VERSION="prompt-under-test",
        SCHEMA_VERSION="2.4",
        NORMALIZATION_VERSION="2.0",
    )

    class ResultModel:
        def __init__(self, payload):
            self.payload = payload
            self.normalized_skills = []

        def model_dump(self, *, mode):
            assert mode == "json"
            return self.payload

    class Pipeline:
        skill_taxonomy = {"nodes": [], "skills": {}}

        def extract_one(self, *, document_id, raw_text, progress_callback=None):
            assert raw_text == "Python"
            return SimpleNamespace(
                extraction=ResultModel(
                    {
                        "document_id": document_id,
                        "education": [],
                        "work_experience": [],
                        "project_experience": [],
                        "skills": [],
                        "languages": [],
                        "certificates": [],
                        "awards": [],
                        "self_evaluation": [],
                    }
                ),
                normalized=ResultModel(
                    {
                        "document_id": document_id,
                        "normalized_skills": [],
                        "unresolved_items": [],
                    }
                ),
                review_flags=(),
            )

    service = CVExtractionApplicationService(
        settings, pipeline_factory=lambda: Pipeline()
    )
    result = service.extract_v2(CVExtractionDocument("cv-settings", "Python"))

    assert result["execution"]["provider"] == "provider-under-test"
    assert result["execution"]["model"] == "model-under-test"
    assert result["execution"]["prompt_version"] == "prompt-under-test"
    assert result["execution"]["schema_version"] == "2.4"
    assert result["execution"]["normalization_version"] == "2.0"
    assert result["execution"]["taxonomy_version"] == "skill-taxonomy-snapshot.v1"
    assert isinstance(result["execution"]["latency_ms"], int)


def test_application_reuses_completed_v2_checkpoint(tmp_path):
    settings = Settings(
        CV_EXTRACTION_INTERNAL_TOKEN=TOKEN,
        CV_EXTRACTION_CHECKPOINT_PATH=str(tmp_path / "checkpoints.sqlite3"),
    )
    calls = 0

    class ResultModel:
        def __init__(self, payload):
            self.payload = payload
            self.normalized_skills = []

        def model_dump(self, *, mode):
            assert mode == "json"
            return self.payload

    class Pipeline:
        skill_taxonomy = {"nodes": [], "skills": {}}

        def extract_one(self, *, document_id, raw_text, progress_callback=None):
            nonlocal calls
            calls += 1
            if progress_callback is not None:
                progress_callback({"stage": "semantic_validating", "percent": 0.76})
            return SimpleNamespace(
                extraction=ResultModel(
                    {
                        "document_id": document_id,
                        "education": [],
                        "work_experience": [],
                        "project_experience": [],
                        "skills": [],
                        "languages": [],
                        "certificates": [],
                        "awards": [],
                        "self_evaluation": [],
                    }
                ),
                normalized=ResultModel(
                    {
                        "document_id": document_id,
                        "normalized_skills": [],
                        "unresolved_items": [],
                    }
                ),
                review_flags=(),
            )

    service = CVExtractionApplicationService(
        settings,
        pipeline_factory=lambda: Pipeline(),
    )
    document = CVExtractionDocument("cv-checkpoint", "Python")

    first = service.extract_v2(document)
    second = service.extract_v2(document)

    assert calls == 1
    assert second == first


def test_checkpoint_fingerprint_includes_validation_policy_version(tmp_path):
    settings = Settings(
        CV_EXTRACTION_INTERNAL_TOKEN=TOKEN,
        CV_EXTRACTION_CHECKPOINT_PATH=str(tmp_path / "checkpoints.sqlite3"),
    )
    service = CVExtractionApplicationService(settings)

    assert (
        service._checkpoint_fingerprint()["validation_policy_version"]
        == VALIDATION_POLICY_VERSION
    )


def test_v3_application_classifies_expected_role():
    raw_text = "期望职位：后端开发工程师"
    evidence = {
        "source_id": "src_0001",
        "quote": "期望职位:后端开发工程师",
        "start": 0,
        "end": len(raw_text),
        "alignment": "exact",
        "occurrence_index": 0,
    }

    class ResultModel:
        def __init__(self, payload):
            self.payload = payload
            self.normalized_skills = []

        def model_dump(self, *, mode):
            assert mode == "json"
            return self.payload

    class Pipeline:
        skill_taxonomy = {"nodes": [], "skills": {}}

        def extract_one(self, *, document_id, raw_text, progress_callback=None):
            return SimpleNamespace(
                extraction=ResultModel(
                    {
                        "document_id": document_id,
                        "personal_info": {
                            "expected_position": "后端开发工程师",
                            "evidence": evidence,
                            "field_evidence": [
                                {
                                    "field_name": "expected_position",
                                    "evidence": evidence,
                                }
                            ],
                        },
                        "education": [],
                        "work_experience": [],
                        "project_experience": [],
                        "skills": [],
                        "languages": [],
                        "certificates": [],
                        "awards": [],
                        "self_evaluation": [],
                    }
                ),
                normalized=ResultModel(
                    {
                        "document_id": document_id,
                        "normalized_skills": [],
                        "unresolved_items": [],
                    }
                ),
                review_flags=(),
            )

    class PositionClassifier:
        profiles = []

        def classify(self, profiles):
            self.profiles = profiles
            return {
                profile["document_id"]: {
                    "classification_status": "resolved"
                }
                for profile in profiles
            }

        def materialize(self, decision, *, source_title):
            return {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "source_title": source_title,
                "position_id": None,
                "position_code": "BACKEND_ENGINEER",
                "position_name": "后端开发工程师",
                "family_code": "SOFTWARE_ENGINEERING",
                "family_name": "软件研发",
                "candidate_positions": [
                    {
                        "position_code": "BACKEND_ENGINEER",
                        "score": 0.91,
                    }
                ],
                "career_level": "mid",
                "leadership_scope": "none",
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": [],
                "confidence": 0.91,
                "classification_status": "resolved",
                "review_reason_codes": [],
                "evidence_refs": ["src_0001"],
                "classification_policy_version": "position-classifier.v3.0",
            }

    classifier = PositionClassifier()
    service = CVExtractionApplicationService(
        Settings(CV_EXTRACTION_INTERNAL_TOKEN=TOKEN),
        pipeline_factory=lambda: Pipeline(),
        position_classifier=classifier,
    )

    result = service.extract_v3(
        CVExtractionDocument("cv-position", raw_text)
    )

    roles = result["normalized_result"]["position_classifications"]
    assert len(roles) == 1
    assert roles[0]["role_kind"] == "expected"
    assert (
        roles[0]["job_classification"]["position_code"]
        == "BACKEND_ENGINEER"
    )
    assert classifier.profiles[0]["available_evidence_refs"] == ["src_0001"]


def test_pipeline_uses_bounded_provider_timeout(monkeypatch):
    captured: dict[str, object] = {}

    class Pipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("api.application.CVExtractionPipeline", Pipeline)
    settings = Settings(
        CV_EXTRACTION_INTERNAL_TOKEN=TOKEN,
        CV_EXTRACTION_API_TIMEOUT_SECONDS=45,
    )

    CVExtractionApplicationService(settings)._build_pipeline()

    assert captured["api_timeout_seconds"] == 45
    assert captured["parallel_section_extraction"] is True
