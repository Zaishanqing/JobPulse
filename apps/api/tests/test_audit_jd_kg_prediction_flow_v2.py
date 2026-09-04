from __future__ import annotations

import copy

import pytest

from scripts.run_audit_jd_kg_prediction_flow_v2 import (
    FlowError,
    convert_normalization,
    projection_has_available_facts,
    require_exact_published_replay,
    source_classification_is_resolved,
    validate_structural_contracts,
    ensure_taxonomy_positions,
    resolve_kg_taxonomy_positions,
)
from app.contracts.jd import JDNormalizedResult


def _source_skill(**changes):
    payload = {
        "source_name": "Python",
        "skill_id": "LANG_PYTHON",
        "canonical_name": "Python",
        "identity_resolution_status": "resolved",
        "classification_resolution_status": "resolved",
        "classifications": [
            {
                "facet": "concept_class",
                "code": "technology",
                "is_primary": True,
            },
            {
                "facet": "technology_kind",
                "code": "language",
                "is_primary": True,
            },
        ],
    }
    payload.update(changes)
    return payload


def _normalization(skill):
    return {
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "classification_status": "resolved",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端开发工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件研发",
            "observed_skill_domain_codes": ["software_engineering"],
        },
        "normalized_requirements": [
            {"requirement_id": "REQ1", "kind": "skill", "skills": [skill]}
        ],
    }


def _extraction():
    return {
        "job_title": {"value": "后端工程师"},
        "requirements": [
            {
                "requirement_id": "REQ1",
                "kind": "skill",
                "items": [{"name": "Python"}],
            }
        ],
    }


def _catalog():
    return {
        "LANG_PYTHON": {
            "skill_id": "main-python-uuid",
            "skill_name": "Python",
            "category": None,
        }
    }


def test_conversion_uses_both_v2_resolution_fields_and_main_catalog_identity():
    result = convert_normalization(
        _normalization(_source_skill()),
        _extraction(),
        "JD1",
        _catalog(),
        standard_position_id="POSITION_BACKEND",
        standard_position_code="BACKEND_ENGINEER",
        standard_position_name="后端开发工程师",
        standard_family_code="SOFTWARE_ENGINEERING",
        standard_family_name="软件研发",
        standard_skill_domain_codes=("software_engineering",),
    )

    skill = result["normalized_requirements"][0]
    assert skill["resolution_status"] == "resolved"
    assert skill["skill_id"] == "main-python-uuid"
    assert skill["source_skill_id"] == "LANG_PYTHON"
    assert skill["source_resolution_status"] == (
        "identity=resolved;classification=resolved"
    )
    assert result["job_classification"]["position_code"] == "BACKEND_ENGINEER"
    JDNormalizedResult.model_validate(result)


def test_conversion_preserves_manually_confirmed_classification_status():
    normalized = _normalization(_source_skill())
    normalized["job_classification"]["classification_status"] = (
        "manually_confirmed"
    )
    normalized["job_classification"]["confidence"] = 0.7
    result = convert_normalization(
        normalized,
        _extraction(),
        "JD1",
        _catalog(),
        standard_position_id="POSITION_BACKEND",
        standard_position_code="BACKEND_ENGINEER",
        standard_position_name="后端开发工程师",
        standard_family_code="SOFTWARE_ENGINEERING",
        standard_family_name="软件研发",
        standard_skill_domain_codes=("software_engineering",),
    )

    assert (
        result["job_classification"]["classification_status"]
        == "manually_confirmed"
    )
    JDNormalizedResult.model_validate(result)


def test_low_confidence_resolved_position_is_routed_to_review():
    normalized = _normalization(_source_skill())
    normalized["job_classification"]["confidence"] = 0.7

    result = convert_normalization(
        normalized,
        _extraction(),
        "JD1",
        _catalog(),
        standard_position_id="POSITION_BACKEND",
        standard_position_code="BACKEND_ENGINEER",
        standard_position_name="后端开发工程师",
        standard_family_code="SOFTWARE_ENGINEERING",
        standard_family_name="软件研发",
    )

    assert result["job_classification"]["classification_status"] == "catalog_gap"
    assert result["job_classification"]["position_id"] is None
    assert any(
        item["code"] == "audit_batch_unresolved_position"
        for item in result["unresolved_items"]
    )
    JDNormalizedResult.model_validate(result)

    assert not source_classification_is_resolved(
        normalized["job_classification"]
    )


def test_skill_missing_from_current_catalog_is_routed_to_review():
    result = convert_normalization(
        _normalization(_source_skill(skill_id="RETIRED_SKILL")),
        _extraction(),
        "JD1",
        _catalog(),
    )

    skill = result["normalized_requirements"][0]
    assert skill["resolution_status"] == "unresolved"
    assert skill["skill_id"] is None
    assert skill["source_skill_id"] == "RETIRED_SKILL"
    assert any(
        item["code"] == "audit_batch_unresolved_skill"
        for item in result["unresolved_items"]
    )
    JDNormalizedResult.model_validate(result)


def test_projection_accepts_evidenced_responsibility_without_skills():
    extraction = {
        "responsibilities": [{"evidence": {"quote": "负责服务开发"}}],
        "requirements": [],
    }
    normalization = {"normalized_requirements": []}
    assert projection_has_available_facts(extraction, normalization)


def test_projection_keeps_jd_usable_when_skill_is_unresolved_but_task_exists():
    extraction = {
        "responsibilities": [{"evidence": {"quote": "负责服务开发"}}],
        "requirements": [],
    }
    normalization = {
        "normalized_requirements": [
            {"resolution_status": "unresolved", "skill_id": None}
        ]
    }
    assert projection_has_available_facts(extraction, normalization)


def test_projection_accepts_evidenced_non_skill_requirement():
    extraction = {
        "responsibilities": [],
        "requirements": [
            {"kind": "experience", "evidence": {"quote": "五年经验"}}
        ],
    }
    normalization = {"normalized_requirements": []}
    assert projection_has_available_facts(extraction, normalization)


def test_projection_rejects_truly_empty_facts():
    assert not projection_has_available_facts(
        {"responsibilities": [], "requirements": []},
        {"normalized_requirements": []},
    )


def test_conversion_does_not_accept_the_removed_single_resolution_status():
    skill = _source_skill()
    skill.pop("identity_resolution_status")
    skill["resolution_status"] = "resolved"

    with pytest.raises(FlowError, match="lacks v2 identity/classification"):
        convert_normalization(
            _normalization(skill), _extraction(), "JD1", _catalog()
        )


def test_classification_unresolved_skill_stays_unresolved():
    result = convert_normalization(
        _normalization(
            _source_skill(classification_resolution_status="unresolved")
        ),
        _extraction(),
        "JD1",
        _catalog(),
    )

    skill = result["normalized_requirements"][0]
    assert skill["resolution_status"] == "unresolved"
    assert skill["skill_id"] is None


def test_structural_validation_uses_v3_classification_status():
    normalized = convert_normalization(
        _normalization(_source_skill()),
        _extraction(),
        "JD1",
        _catalog(),
    )

    validate_structural_contracts(
        {
            "schema_version": "v2",
            "document_id": "JD1",
            "job_title": {
                "value": "后端工程师",
                "evidence": {
                    "source_id": "JD1",
                    "quote": "后端工程师",
                    "start": 0,
                    "end": 5,
                    "alignment": "exact",
                },
            },
            "requirements": [],
        },
        normalized,
        "后端工程师",
    )


def test_position_identity_does_not_treat_observed_domains_as_catalog_identity():
    class FakeAPI:
        def request(self, method, path, *, token):
            assert (method, path, token) == ("GET", "/api/v1/positions", "TOKEN")
            return {
                "data": [
                    {
                        "position_id": "POSITION_BACKEND",
                        "position_code": "BACKEND_ENGINEER",
                        "position_name": "后端开发工程师",
                        "taxonomy_family_code": "SOFTWARE_ENGINEERING",
                        "taxonomy_family_name": "软件研发",
                        "skill_domain_codes": ["software_engineering"],
                    }
                ]
            }

    from types import SimpleNamespace

    records = [
        SimpleNamespace(
            classification_resolved=True,
            position_code="BACKEND_ENGINEER",
            position_name="后端开发工程师",
            family_code="SOFTWARE_ENGINEERING",
            family_name="软件研发",
            skill_domain_codes=domains,
        )
        for domains in (("software_engineering",), ("data_engineering",))
    ]

    positions = ensure_taxonomy_positions(FakeAPI(), "TOKEN", records)
    assert positions["BACKEND_ENGINEER"]["skill_domain_codes"] == [
        "software_engineering"
    ]


def test_kg_catalog_matches_taxonomy_positions_by_code_not_main_system_id():
    taxonomy_positions = {
        "BACKEND_ENGINEER": {"position_id": "main-uuid-1", "position_code": "BACKEND_ENGINEER"}
    }
    kg_positions = [
        {"position_id": "BACKEND_ENGINEER", "position_code": "BACKEND_ENGINEER"}
    ]

    result = resolve_kg_taxonomy_positions(taxonomy_positions, kg_positions)

    assert result["BACKEND_ENGINEER"]["position_id"] == "BACKEND_ENGINEER"

def test_published_fact_can_only_replay_the_exact_current_payload():
    extraction = {"schema_version": "v2", "document_id": "JD1"}
    normalization = {"schema_version": "v2", "document_id": "JD1"}
    published = {
        "workflow_status": "published",
        "extraction_result": copy.deepcopy(extraction),
        "normalized_result": copy.deepcopy(normalization),
    }
    require_exact_published_replay(published, extraction, normalization)

    changed = copy.deepcopy(normalization)
    changed["normalized_requirements"] = []
    with pytest.raises(FlowError, match="published replay differs"):
        require_exact_published_replay(published, extraction, changed)
