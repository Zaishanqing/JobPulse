from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import Field, ValidationError

from tests.runtime_database import SessionLocal, reset_database_data
from app.main import app
from app.models.skill import Skill
from app.models.standard_position import StandardPosition
from app.infrastructure.jd_schema import (
    build_schema_bundle,
    edit_schema_bundle,
    load_schema_bundle,
    persist_schema_bundle,
)
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_extraction_postprocessor import model_output_to_v2_contract
from app.infrastructure.jd_pipeline import normalize_document, validate_document_publishable
from app.api.contracts.jd.evidence import StrictModel
from app.api.contracts.jd.extraction_registry import (
    CURRENT_EXTRACTION_VERSION,
    register_extraction_contract,
    validate_extraction,
)
from app.api.contracts.jd.extraction_model_output import (
    ModelCandidateRequirement,
    ModelEvidence,
    ModelExtractionOutput,
)
from app.infrastructure.jd_extraction_mapper import (
    domain_to_extraction,
    extraction_to_domain,
    register_extraction_mapper,
)
from tests.user_factory import create_internal_user


client = TestClient(app)
V3_TEST_VERSION = "v3_test"


class SimulatedV3Extraction(StrictModel):
    schema_version: Literal["v3_test"] = "v3_test"
    document_id: str
    title: dict | None = None
    duties: list[dict] = Field(default_factory=list)
    candidate_requirements: list[dict] = Field(default_factory=list)
    company: list[dict] = Field(default_factory=list)
    employment: list[dict] = Field(default_factory=list)
    source_metadata: dict | None = None  # optional V3-only field


def _v3_to_domain(contract):
    payload = contract.model_dump(mode="json")
    v2_payload = {
        "schema_version": "v2",
        "document_id": payload["document_id"],
        "job_title": payload["title"],
        "responsibilities": payload["duties"],
        "requirements": payload["candidate_requirements"],
        "company_facts": payload["company"],
        "employment_facts": payload["employment"],
    }
    document = extraction_to_domain(v2_payload, "v2")
    return replace(
        document,
        contract_version=V3_TEST_VERSION,
        payload={"source_metadata": payload.get("source_metadata")},
    )


def _v3_from_domain(document):
    v2_document = replace(document, contract_version="v2", payload={})
    payload = domain_to_extraction(v2_document, "v2").model_dump(mode="json")
    return SimulatedV3Extraction.model_validate(
        {
            "document_id": payload["document_id"],
            "title": payload["job_title"],
            "duties": payload["responsibilities"],
            "candidate_requirements": payload["requirements"],
            "company": payload["company_facts"],
            "employment": payload["employment_facts"],
            "source_metadata": document.payload.get("source_metadata"),
        }
    )


def _register_simulated_v3() -> None:
    register_extraction_contract(V3_TEST_VERSION, SimulatedV3Extraction)
    register_extraction_mapper(
        V3_TEST_VERSION, to_domain=_v3_to_domain, from_domain=_v3_from_domain
    )


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    with SessionLocal() as session:
        session.add(
            Skill(
                id="catalog-python",
                skill_name="Python",
                category="programming_language",
            )
        )
        session.commit()
    _register_simulated_v3()
    yield
    reset_database_data()


def _login() -> dict[str, str]:
    username = "schema_layering_enterprise"
    create_internal_user(username, "enterprise_user")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "enterprise_user", "username": username,
            "password": "password123", "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    created = client.post(
        "/api/v1/enterprises",
        json={
            "enterprise_name": "分层测试企业", "industry": "软件", "scale": "1-20人",
            "location": "武汉", "description": "schema layering",
        },
        headers=headers,
    )
    assert created.status_code == 200
    return headers


def _internal_headers(username: str, role: str) -> dict[str, str]:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_registry_reads_legacy_unversioned_v2_without_data_loss():
    bundle = build_schema_bundle(
        "legacy-jd", "招聘岗位：Python 后端开发工程师。负责 Python 服务开发。", "兜底"
    )
    stored = persist_schema_bundle(bundle)
    legacy = dict(stored.extraction_payload)
    legacy.pop("schema_version")

    contract = validate_extraction(legacy)
    restored = extraction_to_domain(legacy)

    assert CURRENT_EXTRACTION_VERSION == "v2"
    assert contract.schema_version == "v2"
    assert restored.document_id == "legacy-jd"
    assert restored.requirements == bundle.document.requirements


def test_model_output_cannot_supply_ids_or_positions_and_python_postprocesses_exactly():
    with pytest.raises(ValidationError):
        ModelEvidence.model_validate(
            {"source_id": "jd-model", "quote": "Python", "start": 0, "end": 6}
        )
    output = ModelExtractionOutput(
        document_id="jd-model",
        requirements=[
            ModelCandidateRequirement(
                kind="skill",
                modality="required",
                evidence=ModelEvidence(source_id="jd-model", quote="Python"),
                payload={"items": [{"name": "Python", "item_type": "programming_language"}]},
            )
        ],
    )

    contract = model_output_to_v2_contract(output, "要求掌握 Python 开发")

    requirement = contract.requirements[0]
    assert requirement.requirement_id
    assert requirement.evidence.start == 5
    assert requirement.evidence.end == 11
    assert requirement.evidence.alignment == "exact"


def test_orm_api_and_export_do_not_import_model_output_or_v2_compat_module():
    project_root = Path(__file__).resolve().parents[1]
    boundary_files = (
        project_root / "app/models/jd_parse_result.py",
        project_root / "app/schemas/jd.py",
        project_root / "app/infrastructure/jd_export_serialization.py",
    )
    for path in boundary_files:
        source = path.read_text(encoding="utf-8")
        assert "model_output_v2" not in source
        assert "from app.api.contracts.jd" not in source


def test_optional_field_and_renamed_v3_use_only_registry_mapper_changes():
    v2_bundle = build_schema_bundle(
        "evolving-jd",
        "招聘岗位：Python 后端开发工程师。岗位职责：负责 Python FastAPI 服务开发。",
        "兜底",
    )
    v2_stored = persist_schema_bundle(v2_bundle)
    v3_document = replace(
        v2_bundle.document,
        contract_version=V3_TEST_VERSION,
        payload={"source_metadata": {"channel": "simulation"}},
    )
    v3_payload = domain_to_extraction(v3_document, V3_TEST_VERSION).model_dump(mode="json")

    v3_bundle = edit_schema_bundle(
        v3_payload,
        v2_stored.normalization_payload,
        schema_version=V3_TEST_VERSION,
        normalization_schema_version="v2",
    )
    v3_stored = persist_schema_bundle(v3_bundle)
    v2_restored = load_schema_bundle(
        v2_stored.extraction_payload, v2_stored.normalization_payload
    )
    v3_restored = load_schema_bundle(
        v3_stored.extraction_payload,
        v3_stored.normalization_payload,
        schema_version=V3_TEST_VERSION,
        normalization_schema_version="v2",
    )

    assert v3_stored.schema_version == V3_TEST_VERSION
    assert "duties" in v3_stored.extraction_payload
    assert "responsibilities" not in v3_stored.extraction_payload
    assert v3_stored.extraction_payload["source_metadata"] == {"channel": "simulation"}
    assert v2_restored.document.contract_version == "v2"
    assert v3_restored.document.contract_version == V3_TEST_VERSION
    assert v2_restored.document.responsibilities == v3_restored.document.responsibilities
    assert normalize_document(v3_restored.document).items
    validate_document_publishable(v3_restored.document)
    exported = OpenPyxlJDExporter().export(
        v3_restored.document, v3_restored.normalization
    )
    assert "responsibilities" in exported.worksheets


def test_v3_field_change_does_not_break_api_review_publish_or_export_state_machine():
    headers = _login()
    reviewer_headers = _internal_headers("schema_layering_reviewer", "reviewer")
    publisher_headers = _internal_headers("schema_layering_admin", "admin")
    created = client.post(
        "/api/v1/jds/text",
        json={
            "title": "Python 后端开发工程师",
            "raw_text": "招聘岗位：Python 后端开发工程师。岗位职责：负责 Python 服务开发。",
        },
        headers=headers,
    )
    jd_id = created.json()["data"]["jd_id"]
    client.post(
        f"/api/v1/jds/{jd_id}/parse",
        headers=headers,
        json={"extraction_mode": "rule"},
    )
    current = client.get(
        f"/api/v1/jds/{jd_id}/parse-result", headers=headers
    ).json()["data"]
    v2_document = extraction_to_domain(current["extraction_result"])
    v3_document = replace(
        v2_document,
        contract_version=V3_TEST_VERSION,
        payload={"source_metadata": {"optional": True}},
    )
    v3_payload = domain_to_extraction(v3_document, V3_TEST_VERSION).model_dump(mode="json")

    edited = client.put(
        f"/api/v1/jds/{jd_id}/parse-result",
        json={"extraction_result": v3_payload},
        headers=headers,
    )
    with SessionLocal() as session:
        position = StandardPosition(
            position_code="BACKEND_ENGINEER",
            position_name="后端开发工程师",
            taxonomy_family_code="SOFTWARE_ENGINEERING",
            taxonomy_family_name="软件研发",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
            status="existing",
        )
        session.add(position)
        session.commit()
        position_id = position.id
    mapped = client.post(
        (
            f"/api/v1/jd-parse-results/{current['parse_result_id']}"
            "/position-catalog-mapping"
        ),
        json={"target_position_id": position_id},
        headers=reviewer_headers,
    )
    reviewed = client.post(
        f"/api/v1/jds/{jd_id}/parse-result/confirm", headers=reviewer_headers
    )
    published = client.post(
        f"/api/v1/jds/{jd_id}/parse-result/publish", headers=publisher_headers
    )
    exported = client.get(
        f"/api/v1/jds/{jd_id}/parse-result/export", headers=headers
    )

    assert edited.status_code == 200
    assert mapped.status_code == 200
    assert edited.json()["data"]["schema_version"] == V3_TEST_VERSION
    assert "duties" in edited.json()["data"]["extraction_result"]
    assert reviewed.json()["data"]["workflow_status"] == "reviewed"
    assert published.json()["data"]["workflow_status"] == "published"
    assert exported.status_code == 200
    assert exported.json()["data"]["filename"].endswith("_v3_test.xlsx")
