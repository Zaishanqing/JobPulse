from __future__ import annotations

import pytest

from app.domain.emerging_position import (
    EmergingCandidate,
    EmergingPositionStatus,
    GerminationAssessment,
    InvalidEmergingTransition,
    ReleaseGateEvidence,
    ReleaseGateRejected,
)


def candidate(status: EmergingPositionStatus = EmergingPositionStatus.DRAFT):
    return EmergingCandidate.create(
        candidate_id="candidate-1",
        cluster_id="cluster-1",
        position_name="向量技能岗位",
        core_responsibilities=["交付"],
        required_skills=[{"raw_skill": "RAG"}],
        bonus_skills=[],
        industry_scenarios=["客服"],
        germination_score=None,
        score_dimensions={},
        evidence_jd_ids=["jd-1"],
        status=status,
    )


def gate(*, qualified: bool = True, run_succeeded: bool = True):
    return ReleaseGateEvidence(
        run_succeeded=run_succeeded,
        stability_score=0.9,
        minimum_stability_score=0.65,
        assessment=GerminationAssessment.from_values(
            {"germination_score": 0.8, "qualified_as_emerging": qualified},
            "run-1",
        ),
        emerging_threshold=0.6,
        evidence_jd_ids=("jd-1",),
        real_member_count=1,
        window_count=3,
        complete_score_dimensions=True,
        complete_definition=True,
        complete_claim_evidence=True,
        definition_unchanged_since_approval=True,
    )


def test_candidate_requires_review_before_publish_and_publish_before_promotion():
    with pytest.raises(InvalidEmergingTransition):
        candidate().publish(gate())
    approved = candidate(EmergingPositionStatus.PENDING_REVIEW).review(
        EmergingPositionStatus.APPROVED
    )
    published = approved.publish(gate())
    published.assert_promotable(gate())
    assert published.status is EmergingPositionStatus.PUBLISHED


def test_release_gate_rejects_failed_discovery_or_unqualified_assessment():
    verified = candidate(EmergingPositionStatus.PENDING_REVIEW).review(
        EmergingPositionStatus.APPROVED
    )
    with pytest.raises(ReleaseGateRejected):
        verified.publish(gate(run_succeeded=False))
    with pytest.raises(ReleaseGateRejected):
        verified.publish(gate(qualified=False))


def test_algorithm_owned_nested_values_are_recursively_immutable():
    item = candidate()
    with pytest.raises(TypeError):
        item.required_skills[0]["raw_skill"] = "fake"
