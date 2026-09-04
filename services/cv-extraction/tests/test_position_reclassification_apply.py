from __future__ import annotations

from src.models import CVMatchFeatureResult, Evidence, MatchFeature


def _ambiguous_role_feature() -> MatchFeature:
    return MatchFeature(
        feature_id="mf_cv_001_role_expected_position",
        document_id="cv_001",
        side="cv",
        feature_type="role",
        source_object_id="expected_position",
        source_scope="personal_info:expected_position",
        canonical_id=None,
        canonical_name=None,
        raw_text="算法工程师",
        vector_text="期望岗位：算法工程师",
        structured_values={
            "role_kind": "expected",
            "classification_schema_version": "job-position-classification.v3",
            "position_code": None,
            "family_code": None,
            "family_name": None,
        },
        resolution_status="ambiguous",
        evidence_refs=[Evidence(source_id="src_001", quote="算法工程师")],
        taxonomy_version="2.0",
        derivation_version="test",
    )


def test_ambiguous_role_structured_null_identity_is_valid() -> None:
    """V3 contract: non-resolved roles must bind null position identity."""
    result = CVMatchFeatureResult(
        document_id="cv_001",
        as_of_date="2026-08-19",
        taxonomy_version="2.0",
        derivation_version="test",
        features=[_ambiguous_role_feature()],
    )

    assert result.features[0].resolution_status == "ambiguous"
    assert result.features[0].structured_values["position_code"] is None
    assert result.features[0].structured_values["family_code"] is None
    assert result.features[0].structured_values["family_name"] is None
