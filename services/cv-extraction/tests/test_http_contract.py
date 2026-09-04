from __future__ import annotations

import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault(
    "CV_EXTRACTION_INTERNAL_TOKEN",
    "test-cv-extraction-token-with-at-least-32-characters",
)

import pytest
from fastapi.testclient import TestClient
from jobgraph_contracts.deepseek import (
    DeepSeekAuthError,
    DeepSeekConnectionError,
    DeepSeekModelNotFoundError,
    DeepSeekRateLimitError,
    DeepSeekServerError,
    DeepSeekTimeoutError,
    InvalidJSONError,
    MissingAPIKeyError,
)
from pydantic import ValidationError

from api.config import Settings
from api.main import create_app
from jobgraph_contracts.cv_extraction_http import (
    CVExtractionResponseV2,
    CVExtractionResponseV3,
    parse_cv_extraction_response,
)
from src.evidence import validate_evidence_alignment
from src.exceptions import EvidenceAlignmentError

TOKEN = "test-cv-extraction-token-with-at-least-32-characters"


def _minimal_payload(document_id: str = "doc-1") -> dict:
    return {
        "contract_version": "cv-extraction-http.v2",
        "document_id": document_id,
        "execution": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "prompt_version": "cv-prompt.v1",
            "schema_version": "2.4",
            "normalization_version": "2.0",
            "taxonomy_version": "skill-taxonomy-snapshot.v1",
            "latency_ms": 12,
        },
        "extraction_result": {
            "document_id": document_id,
            "education": [],
            "work_experience": [],
            "project_experience": [],
            "skills": [
                {
                    "item_id": "skill-1",
                    "name": "Python",
                    "item_type": "programming_language",
                    "proficiency": "proficient",
                    "evidence": {
                        "source_document_id": document_id,
                        "source_id": "src_0001",
                        "quote": "Python",
                        "start": 0,
                        "end": 6,
                        "alignment": "exact",
                        "occurrence_index": 0,
                    },
                }
            ],
            "languages": [],
            "certificates": [],
            "awards": [],
            "self_evaluation": [],
        },
        "normalized_result": {
            "document_id": document_id,
            "normalized_skills": [],
            "unresolved_items": [],
        },
        "review_flags": [],
        "skill_taxonomy": {
            "schema_version": "skill-taxonomy-projection.v1",
            "taxonomy_version": "skill-taxonomy-snapshot.v1",
            "skills": [],
        },
    }


def test_http_contract_preserves_publications_patents_and_research_outputs():
    payload = _minimal_payload()
    evidence = {
        "source_document_id": "doc-1",
        "source_id": "src_0002",
        "quote": "论文 A 已接收；专利 B 已授权；开源数据集 C",
        "start": 0,
        "end": 25,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    payload["extraction_result"].update(
        {
            "publications": [
                {
                    "entry_id": "pub_001",
                    "title": "论文 A",
                    "author_order": 1,
                    "status": "accepted",
                    "year": 2025,
                    "evidence": evidence,
                }
            ],
            "patents": [
                {
                    "entry_id": "patent_001",
                    "title": "专利 B",
                    "status": "granted",
                    "inventor_order": 2,
                    "year": 2024,
                    "evidence": evidence,
                }
            ],
            "research_outputs": [
                {
                    "entry_id": "research_001",
                    "name": "开源数据集 C",
                    "output_type": "dataset",
                    "evidence": evidence,
                }
            ],
        }
    )

    parsed = CVExtractionResponseV2.model_validate(payload)

    assert parsed.extraction_result.publications[0].status == "accepted"
    assert parsed.extraction_result.publications[0].author_order == 1
    assert parsed.extraction_result.publications[0].year == 2025
    assert parsed.extraction_result.patents[0].status == "granted"
    assert parsed.extraction_result.patents[0].inventor_order == 2
    assert parsed.extraction_result.research_outputs[0].output_type == "dataset"


def _resolved_position_classification() -> dict:
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "source_title": "后端开发工程师",
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


def _minimal_v3_payload(document_id: str = "doc-1") -> dict:
    payload = _minimal_payload(document_id)
    payload["contract_version"] = "cv-extraction-http.v3"
    payload["normalized_result"]["position_classifications"] = [
        {
            "feature_id": "role_personal_info_expected_position",
            "source_object_id": "personal_info",
            "source_scope": "personal_info.expected_position",
            "role_kind": "expected",
            "job_classification": _resolved_position_classification(),
        }
    ]
    return payload


def test_v2_response_contract_includes_taxonomy_latency_and_document_evidence():
    parsed = parse_cv_extraction_response(_minimal_payload())
    assert isinstance(parsed, CVExtractionResponseV2)
    assert parsed.execution.taxonomy_version == "skill-taxonomy-snapshot.v1"
    assert parsed.execution.latency_ms == 12
    assert (
        parsed.extraction_result.skills[0].evidence.source_document_id
        == "doc-1"
    )


def test_v2_response_contract_preserves_normalization_provenance():
    payload = _minimal_payload()
    payload["normalized_result"]["normalized_skills"] = [
        {
            "source_item_id": "skill-1",
            "source_scope": "skills",
            "source_name": "Python",
            "skill_id": "LANG_PYTHON",
            "canonical_name": "Python",
            "category_code": "programming_language",
            "resolution_status": "resolved",
            "normalization_confidence": 1.0,
            "resolution_source": "canonical_name",
        }
    ]

    parsed = parse_cv_extraction_response(payload)

    skill = parsed.normalized_result.normalized_skills[0]
    assert skill.normalization_confidence == 1.0
    assert skill.resolution_source == "canonical_name"


def test_v2_response_contract_rejects_resolved_skill_without_provenance():
    payload = _minimal_payload()
    payload["normalized_result"]["normalized_skills"] = [
        {
            "source_item_id": "skill-1",
            "source_scope": "skills",
            "source_name": "Python",
            "skill_id": "LANG_PYTHON",
            "canonical_name": "Python",
            "category_code": "programming_language",
            "resolution_status": "resolved",
        }
    ]

    with pytest.raises(ValidationError):
        parse_cv_extraction_response(payload)


def test_v3_response_contract_preserves_role_position_classification():
    parsed = parse_cv_extraction_response(_minimal_v3_payload())

    assert isinstance(parsed, CVExtractionResponseV3)
    role = parsed.normalized_result.position_classifications[0]
    assert role.role_kind == "expected"
    assert role.job_classification.position_code == "BACKEND_ENGINEER"


def test_v3_rejects_unresolved_classification_with_bound_position():
    payload = _minimal_v3_payload()
    classification = payload["normalized_result"][
        "position_classifications"
    ][0]["job_classification"]
    classification["classification_status"] = "catalog_gap"
    classification["review_reason_codes"] = ["CATALOG_GAP"]

    with pytest.raises(ValidationError):
        parse_cv_extraction_response(payload)


def test_exact_evidence_alignment_is_validated_against_raw_text():
    payload = _minimal_payload()
    validate_evidence_alignment(
        payload["extraction_result"],
        document_id="doc-1",
        raw_text="Python developer",
        source_blocks=[
            {
                "source_id": "src_0001",
                "text": "Python developer",
                "start": 0,
                "end": 15,
            }
        ],
    )


def test_occurrence_index_is_reproduced_inside_the_source_block():
    payload = _minimal_payload()
    evidence = payload["extraction_result"]["skills"][0]["evidence"]
    evidence["source_id"] = "src_0002"
    evidence["start"] = 7
    evidence["end"] = 13
    validate_evidence_alignment(
        payload["extraction_result"],
        document_id="doc-1",
        raw_text="Python\nPython developer",
        source_blocks=[
            {"source_id": "src_0001", "text": "Python", "start": 0, "end": 6},
            {
                "source_id": "src_0002",
                "text": "Python developer",
                "start": 7,
                "end": 22,
            },
        ],
    )


def test_evidence_offset_out_of_bounds_is_rejected():
    payload = _minimal_payload()
    evidence = payload["extraction_result"]["skills"][0]["evidence"]
    evidence["start"] = 100
    evidence["end"] = 106
    with pytest.raises(EvidenceAlignmentError):
        validate_evidence_alignment(
            payload["extraction_result"],
            document_id="doc-1",
            raw_text="Python developer",
            source_blocks=[
                {
                    "source_id": "src_0001",
                    "text": "Python developer",
                    "start": 0,
                    "end": 15,
                }
            ],
        )


def test_model_response_schema_mismatch_is_rejected():
    payload = _minimal_payload()
    del payload["skill_taxonomy"]
    with pytest.raises(ValidationError):
        parse_cv_extraction_response(payload)


def test_provider_unavailable_returns_stable_error_code():
    class UnavailableService:
        def extract_v2(self, document):
            raise RuntimeError("upstream timeout")

    client = TestClient(create_app(Settings(CV_EXTRACTION_INTERNAL_TOKEN=TOKEN), UnavailableService()))
    response = client.post(
        "/api/v2/cv-extractions",
        json={"document_id": "doc-1", "raw_text": "Python"},
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CV_EXTRACTION_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("exception", "status_code", "code"),
    [
        (MissingAPIKeyError("missing"), 503, "CV_EXTRACTION_API_KEY_MISSING"),
        (DeepSeekAuthError("auth"), 502, "CV_EXTRACTION_AUTH_FAILED"),
        (
            DeepSeekModelNotFoundError("model not available", model="deepseek-v4-flash"),
            502,
            "CV_EXTRACTION_MODEL_NOT_AVAILABLE",
        ),
        (DeepSeekRateLimitError("slow down"), 429, "CV_EXTRACTION_RATE_LIMITED"),
        (DeepSeekTimeoutError("slow"), 504, "CV_EXTRACTION_PROVIDER_TIMEOUT"),
        (
            DeepSeekConnectionError("dns failed", reason="dns"),
            503,
            "CV_EXTRACTION_PROVIDER_CONNECTION_FAILED",
        ),
        (InvalidJSONError("bad json"), 502, "CV_EXTRACTION_PROVIDER_INVALID_RESPONSE"),
        (DeepSeekServerError("5xx"), 502, "CV_EXTRACTION_PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_error_mapping_returns_specific_domain_codes(
    exception,
    status_code,
    code,
):
    class FailingService:
        def extract_v2(self, document):
            raise exception

    client = TestClient(create_app(Settings(CV_EXTRACTION_INTERNAL_TOKEN=TOKEN), FailingService()))
    response = client.post(
        "/api/v2/cv-extractions",
        json={"document_id": "doc-1", "raw_text": "Python"},
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code


def test_evidence_error_message_never_contains_raw_text():
    payload = _minimal_payload()
    evidence = payload["extraction_result"]["skills"][0]["evidence"]
    evidence["start"] = 100
    evidence["end"] = 106
    with pytest.raises(EvidenceAlignmentError) as exc:
        validate_evidence_alignment(
            payload["extraction_result"],
            document_id="doc-1",
            raw_text="private-candidate-phone-number",
            source_blocks=[
                {
                    "source_id": "src_0001",
                    "text": "private-candidate-phone-number",
                    "start": 0,
                    "end": 30,
                }
            ],
        )
    assert "private-candidate-phone-number" not in str(exc.value)
