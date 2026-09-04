from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domain.profiles import CVMatchProfile, Evidence, PositionMatchProfile
from app.domain.semantic_fragments import (
    CV_FRAGMENT_TYPES,
    POSITION_FRAGMENT_TYPES,
    SemanticFragmentationViolation,
    fragment_cv_profile,
    fragment_position_profile,
    plan_fragment_version_change,
)
from app.domain.vector_contracts import SemanticFragment


def test_cv_fragmentation_is_deterministic_evidence_bound_and_pii_free(cv_payload) -> None:
    profile = CVMatchProfile.model_validate(cv_payload)

    first = fragment_cv_profile(profile, tenant_ref="tenant-a")
    repeated = fragment_cv_profile(profile, tenant_ref="tenant-a")

    assert first == repeated
    assert first
    assert tuple(item.sequence for item in first) == tuple(range(len(first)))
    assert len({item.evidence_ref.source_id for item in first}) == len(first)
    assert {item.fragment_type for item in first} <= CV_FRAGMENT_TYPES
    assert all(item.source_profile_id for item in first)
    assert all(item.normalized_text == item.evidence_ref.quote for item in first)


def test_position_fragmentation_skips_facts_without_evidence(position_payload) -> None:
    profile = PositionMatchProfile.model_validate(position_payload)

    fragments = fragment_position_profile(profile, tenant_ref="tenant-a")

    assert {item.fragment_type for item in fragments} <= POSITION_FRAGMENT_TYPES
    assert "responsibility" in {item.fragment_type for item in fragments}
    assert "required_skill_context" in {item.fragment_type for item in fragments}
    assert not any(item.normalized_text == "SQL" for item in fragments)
    assert not any(item.normalized_text == "3 years" for item in fragments)

    enterprise_fragments = fragment_position_profile(
        profile, tenant_ref="tenant-a", target_type="enterprise_job"
    )
    assert enterprise_fragments
    assert {item.target_type for item in enterprise_fragments} == {"enterprise_job"}


def test_profile_versions_coexist_and_only_changed_fragments_are_replaced(cv_payload) -> None:
    previous_payload = deepcopy(cv_payload)
    previous_payload["source_version"] = "cv.v1"
    previous = fragment_cv_profile(
        CVMatchProfile.model_validate(previous_payload), tenant_ref="tenant-a"
    )

    current_payload = deepcopy(previous_payload)
    current_payload["source_version"] = "cv.v2"
    current_payload["skills"][1]["evidence_refs"][0]["quote"] = "Advanced SQL"
    current_payload["profile_version"] = None
    current = fragment_cv_profile(
        CVMatchProfile.model_validate(current_payload), tenant_ref="tenant-a"
    )
    plan = plan_fragment_version_change(previous, current)

    assert {item.source_version for item in previous} == {"cv.v1"}
    assert {item.source_version for item in current} == {"cv.v2"}
    assert len(plan.current) == 1
    assert len(plan.superseded) == 1
    assert len(plan.unchanged) == len(previous) - 1
    assert plan.current[0].normalized_text == "Advanced SQL"


def test_fragment_length_limit_is_fail_closed(cv_payload) -> None:
    payload = deepcopy(cv_payload)
    payload["skills"][0]["evidence_refs"][0]["quote"] = "x" * 1001
    profile = CVMatchProfile.model_validate(payload)

    with pytest.raises(SemanticFragmentationViolation) as rejected:
        fragment_cv_profile(profile, tenant_ref="tenant-a")

    assert rejected.value.code == "SEMANTIC_FRAGMENT_TOO_LONG"


def test_fragment_type_must_belong_to_its_profile_side() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        SemanticFragment(
            tenant_ref="tenant-a",
            fragment_id="fragment:invalid:1",
            source_type="cv",
            target_type="candidate_cv",
            source_id="cv:opaque-1",
            source_version="cv.v1",
            source_profile_id="a" * 64,
            fragment_type="responsibility",
            normalized_text="Build services",
            evidence_ref=Evidence(source_id="cv:evidence:1", quote="Build services"),
            language="en",
            sequence=0,
            taxonomy_version="taxonomy.v1",
        )
