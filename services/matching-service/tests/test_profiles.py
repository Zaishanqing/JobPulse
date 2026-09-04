from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.profiles import CVMatchProfile, PositionMatchProfile


def test_cv_schema_preserves_evidence_versions_and_capability_contract(cv_payload):
    profile = CVMatchProfile.model_validate(cv_payload)

    assert profile.match_features[0].evidence_refs[0].quote == "Python"
    assert profile.match_features[0].taxonomy_version == "taxonomy-2026-07"
    assert profile.capability_profiles[0].declared_level == "proficient"
    assert profile.capability_profiles[0].demonstrated_level == "working"
    assert profile.capability_evidence_links[0].derivation_version == (
        "capability-verification.v1"
    )


def test_position_schema_preserves_evidence_taxonomy_graph_and_trend(position_payload):
    profile = PositionMatchProfile.model_validate(position_payload)

    assert profile.evidence_refs[0].source_id == "jd:block:1"
    assert profile.taxonomy_version == "taxonomy-2026-07"
    assert profile.graph_version == "graph-42"
    assert profile.trend_context is not None
    assert profile.trend_context.trend_version == "trend.v1"


def test_profiles_are_immutable(cv_payload):
    profile = CVMatchProfile.model_validate(cv_payload)

    with pytest.raises(ValidationError):
        profile.cv_id = "another"  # type: ignore[misc]


def test_extra_fields_are_rejected_by_domain_schema(cv_payload):
    cv_payload["email"] = "not-part-of-contract"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CVMatchProfile.model_validate(cv_payload)


def test_pii_values_are_rejected_by_domain_schema(position_payload):
    position_payload["evidence_refs"][0]["quote"] = "candidate@example.com"

    with pytest.raises(ValidationError, match="PII is forbidden"):
        PositionMatchProfile.model_validate(position_payload)


def test_position_profile_rejects_unknown_requirement_graph_reference(position_payload):
    position_payload["required_skills"][0]["requirement_id"] = "req-python"
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-root",
                "group_type": "must",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-123"}
                ],
                "evidence": position_payload["evidence_refs"][0],
                "confidence": 0.9,
            }
        ],
        "unresolved_items": [],
    }

    with pytest.raises(ValidationError, match="unknown requirements"):
        PositionMatchProfile.model_validate(position_payload)


def test_position_profile_accepts_mapped_responsibility_reference(position_payload):
    position_payload["core_responsibilities"] = ["Build backend services"]
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-responsibility",
                "group_type": "must",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "responsibility:1"}
                ],
                "evidence": position_payload["evidence_refs"][0],
                "confidence": 0.9,
            }
        ],
        "unresolved_items": [],
    }

    profile = PositionMatchProfile.model_validate(position_payload)
    assert profile.requirement_graph is not None
    assert profile.requirement_graph.groups[0].children[0].ref_id == "responsibility:1"


def test_matching_evidence_and_graph_remain_immutable(position_payload):
    position_payload["required_skills"][0]["requirement_id"] = "req-python"
    position_payload["core_responsibilities"] = ["Build backend services"]
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-root",
                "group_type": "and",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-python"},
                    {"node_type": "requirement_ref", "ref_id": "responsibility:1"},
                ],
                "evidence": position_payload["evidence_refs"][0],
                "confidence": 0.9,
            }
        ],
        "unresolved_items": [],
    }
    profile = PositionMatchProfile.model_validate(position_payload)

    with pytest.raises(ValidationError):
        profile.evidence_refs[0].quote = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        profile.requirement_graph.groups[0].children[0].ref_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        profile.requirement_graph.groups[0].children = ()  # type: ignore[misc]


def test_cv_linkage_cannot_cross_documents(cv_payload):
    cv_payload["match_features"][0]["document_id"] = "cv_other"

    with pytest.raises(ValidationError, match="must match cv_id"):
        CVMatchProfile.model_validate(cv_payload)
