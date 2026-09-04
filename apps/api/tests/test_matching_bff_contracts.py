from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.matching_bff_mapping import (
    evidence_deletion_data,
    enrich_report,
    report_result_status,
    what_if_data,
)
from app.api.evaluation_data import evaluation_report_data
from app.contexts.matching_learning.matching_service import (
    RemoteEvaluation,
    RemoteLearningPath,
)
from app.contexts.matching_learning._ports.matching import MatchingPositionCandidate
from app.domain.accounts import AccountActor
from app.schemas.matching_bff import (
    BFFTaskStatus,
    EvidenceDeletionResponse,
    ResponsibilityCandidateResponse,
    MatchReportResponse,
    MatchTaskResponse,
    WhatIfResponse,
)
from app.main import app
from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _evaluation() -> RemoteEvaluation:
    return RemoteEvaluation(
        evaluation_id="evaluation-1",
        task_id="task-1",
        stale=False,
        evaluation={
            "evaluation_status": "completed",
            "skill_results": [
                {
                    "requirement_id": "requirement-1",
                    "position_evidence": [
                        {
                            "source_id": "kg:1",
                            "quote": "Python required",
                            "start": 0,
                            "end": 14,
                            "alignment": "exact",
                            "occurrence_index": 0,
                        }
                    ],
                    "candidate_evidence": [
                        {
                            "source_id": "snapshot:1",
                            "quote": "Python",
                            "start": 0,
                            "end": 6,
                            "alignment": "exact",
                            "occurrence_index": 0,
                        }
                    ],
                }
            ],
            "final_match_result": {
                "position_graph_version": "graph-42",
                "recommendation_level": "strong_match",
                "match_confidence": 0.9,
            },
        },
        gap_analysis={
            "generation_status": "completed",
            "config_version": "gap-analysis-config.v1",
            "gap_policy_version": "gap-priority.v1",
            "gap_policy_hash": "a" * 64,
            "prioritized_gaps": [
                {
                    "requirement_id": "requirement-1",
                    "gap_type": "ownership_gap",
                    "priority": "high",
                    "priority_score": 70,
                    "reason_codes": ["SKILL_OWNERSHIP_GAP"],
                    "current_ownership": "used",
                    "target_ownership": "owned",
                    "score_effect_status": "modeled",
                    "evidence": [
                        {
                            "source_id": "snapshot:1",
                            "quote": "Python",
                            "start": 0,
                            "end": 6,
                            "alignment": "exact",
                            "occurrence_index": 0,
                        }
                    ],
                }
            ],
            "candidate_actions": [
                {
                    "action_id": "ownership-requirement-1",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill-python",
                    "ownership": "owned",
                    "target_requirement_ids": ["requirement-1"],
                    "estimated_hours": 4,
                    "cost_band": {
                        "min_hours": 2,
                        "expected_hours": 4,
                        "max_hours": 8,
                        "confidence": 0.25,
                        "basis": "ownership-evidence-packaging",
                    },
                    "stage": "ownership",
                    "estimated_score_delta": 2.5,
                    "estimated_utility": 0.625,
                }
            ],
            "learning_routes": [
                {
                    "route_type": "fastest_employment",
                    "action_ids": ["ownership-requirement-1"],
                    "total_cost_hours": 4,
                    "baseline_score": 60,
                    "final_score": 62.5,
                    "projected_match_gain": 2.5,
                    "target_reachable": True,
                    "algorithm_version": "learning-route-enumeration.v1",
                    "action_costs": [
                        {
                            "action_id": "ownership-requirement-1",
                            "direct_hours": 4,
                            "dependency_hours": 0,
                            "total_hours": 4,
                            "difficulty": "low",
                            "selected": True,
                            "cost_model": "heuristic_level_distance.v1",
                            "cost_source_type": "heuristic",
                            "cost_source_ref": "heuristic_level_distance.v1",
                            "estimate_status": "estimated",
                        }
                    ],
                }
            ],
            "minimal_action_set": {
                "status": "reached",
                "source_evaluation_id": "evaluation-1",
                "scenario_id": "scenario-1",
                "selected_action_ids": ["ownership-requirement-1"],
                "deferred_action_ids": [],
                "action_costs": [
                    {
                        "action_id": "ownership-requirement-1",
                        "direct_hours": 4,
                        "dependency_hours": 0,
                        "total_hours": 4,
                        "difficulty": "low",
                        "selected": True,
                        "cost_model": "heuristic_level_distance.v1",
                        "estimate_status": "heuristic",
                    }
                ],
                "minimum_action_count": 1,
                "total_cost_hours": 4,
                "budget_hours": 8,
                "budget_used_hours": 4,
                "budget_remaining_hours": 4,
                "baseline_score": 60,
                "scenario_score": 62.5,
                "score_delta": 2.5,
                "dimension_deltas": [],
                "baseline_hard_gate_status": "passed",
                "scenario_hard_gate_status": "passed",
                "hard_gate_delta": "passed->passed",
                "target_reachable": True,
                "covered_requirement_ids": ["requirement-1"],
                "evidence_refs": [
                    {
                        "source_id": "snapshot:1",
                        "quote": "Python",
                        "start": 0,
                        "end": 6,
                        "alignment": "exact",
                        "occurrence_index": 0,
                    }
                ],
                "unreachable_reason_codes": [],
                "cv_profile_version": "cv-v1",
                "position_profile_version": "position-v1",
                "graph_version_id": "graph-42",
                "policy_version": "matching|scoring|config",
                "search_status": "exact_bounded",
                "algorithm_version": "minimal-action-set.v1",
            },
        },
        versions={},
        created_at=None,
        updated_at=None,
        resume_id="resume-1",
        validated_cv_snapshot_id="snapshot-1",
        position_id="position-1",
    )


def _headers(username: str = "matching_bff_user") -> dict[str, str]:
    create_internal_user(username, "personal_user")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "personal_user",
            "username": username,
            "password": "password123",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_report_mapping_enriches_candidate_and_position_evidence():
    data = enrich_report(_evaluation())

    skill = data["evaluation"]["skill_results"][0]
    candidate = skill["candidate_evidence"][0]
    position = skill["position_evidence"][0]
    assert candidate["source_object_type"] == "validated_cv_snapshot"
    assert position["source_object_type"] == "position_profile"
    gap_evidence = data["gap_analysis"]["prioritized_gaps"][0]["evidence"][0]
    assert gap_evidence["source_object_type"] == "validated_cv_snapshot"
    gap = data["gap_analysis"]["prioritized_gaps"][0]
    assert gap["current_ownership"] == "used"
    assert gap["target_ownership"] == "owned"
    assert data["gap_analysis"]["candidate_actions"][0]["estimated_score_delta"] == 2.5
    assert data["gap_analysis"]["candidate_actions"][0]["cost_band"] == {
        "min_hours": 2.0,
        "expected_hours": 4.0,
        "max_hours": 8.0,
        "confidence": 0.25,
        "basis": "ownership-evidence-packaging",
    }
    assert data["gap_analysis"]["learning_routes"][0]["projected_match_gain"] == 2.5
    route_cost = data["gap_analysis"]["learning_routes"][0]["action_costs"][0]
    assert route_cost["cost_model"] == "heuristic_level_distance.v1"
    assert route_cost["cost_source_type"] == "heuristic"
    assert route_cost["cost_source_ref"] == "heuristic_level_distance.v1"
    assert route_cost["estimate_status"] == "estimated"
    assert data["gap_analysis"]["gap_policy_version"] == "gap-priority.v1"
    assert data["gap_analysis"]["gap_policy_hash"] == "a" * 64
    minimal = data["gap_analysis"]["minimal_action_set"]
    assert minimal["selected_action_ids"] == ["ownership-requirement-1"]
    assert minimal["budget_remaining_hours"] == 4
    assert minimal["evidence_refs"][0]["source_object_type"] == "matching_evidence"


def test_gap_evidence_with_ambiguous_source_remains_unresolved():
    evaluation = _evaluation()
    shared = evaluation.evaluation["skill_results"][0]["candidate_evidence"][0]
    evaluation.evaluation["skill_results"][0]["position_evidence"].append(dict(shared))

    data = enrich_report(evaluation)

    gap_evidence = data["gap_analysis"]["prioritized_gaps"][0]["evidence"][0]
    assert gap_evidence["source_object_type"] == "matching_evidence"
    assert gap_evidence["source_object_id"] == "evaluation-1"


def _responsibility_evaluation() -> RemoteEvaluation:
    evaluation = _evaluation()
    evidence = {
        "source_id": "snapshot:responsibility",
        "quote": "负责后端服务可靠性",
        "start": 0,
        "end": 12,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    evaluation.evaluation["responsibility_results"] = [
        {
            "requirement_id": "responsibility:1",
            "position_requirement": "负责后端服务可靠性",
            "candidate_experience_id": "exp:1",
            "candidate_experience": "负责后端服务可靠性建设",
            "match_status": "matched",
            "status_detail": "partial",
            "position_evidence": [evidence],
            "candidate_evidence": [evidence],
            "reason_code": "RESPONSIBILITY_MATCHED",
            "confidence": 0.9,
            "match_type": "semantic",
            "ce_score": 1.234567,
            "retrieval_score": 0.61,
            "threshold_margin": 0.136190,
            "top_candidates": [
                {
                    "experience_id": "exp:1",
                    "text": "负责后端服务可靠性建设",
                    "retrieval_score": 0.63,
                    "ce_score": 1.234567,
                    "threshold_margin": 0.136190,
                    "evidence_refs": [evidence],
                }
            ],
        }
    ]
    return evaluation


def test_responsibility_contract_preserves_decision_policy_fields():
    data = enrich_report(_responsibility_evaluation())
    item = data["evaluation"]["responsibility_results"][0]
    assert item["status_detail"] == "partial"
    assert item["ce_score"] == 1.234567
    assert item["retrieval_score"] == 0.61
    assert item["threshold_margin"] == 0.136190
    candidate = item["top_candidates"][0]
    assert candidate["experience_id"] == "exp:1"
    assert candidate["text"] == "负责后端服务可靠性建设"
    assert candidate["evidence_refs"][0]["source_object_type"] == "validated_cv_snapshot"
    validated = MatchReportResponse.model_validate(
        evaluation_report_data(_responsibility_evaluation())
    )
    assert validated.matching_method == "semantic_verified"


def test_responsibility_contract_accepts_rule_mode_without_semantic_fields():
    evaluation = _evaluation()
    evaluation.evaluation["responsibility_results"] = [
        {
            "requirement_id": "responsibility:1",
            "position_requirement": "负责后端服务可靠性",
            "candidate_experience_id": None,
            "candidate_experience": None,
            "match_status": "not_observed",
            "status_detail": "not_observed",
            "position_evidence": [],
            "candidate_evidence": [],
            "reason_code": "RESPONSIBILITY_NOT_OBSERVED",
            "confidence": 1.0,
            "match_type": "deterministic",
            "ce_score": None,
            "retrieval_score": None,
            "threshold_margin": None,
            "top_candidates": [],
        }
    ]
    data = enrich_report(evaluation)
    item = data["evaluation"]["responsibility_results"][0]
    assert item["status_detail"] == "not_observed"
    assert item["ce_score"] is None
    assert item["top_candidates"] == []
    MatchReportResponse.model_validate(evaluation_report_data(evaluation))


def test_evidence_contract_exposes_structured_lineage_and_reference():
    data = enrich_report(_evaluation())

    skill = data["evaluation"]["skill_results"][0]
    candidate = skill["candidate_evidence"][0]
    position = skill["position_evidence"][0]
    assert candidate["source_object_type"] == "validated_cv_snapshot"
    assert candidate["source_object_id"] == "snapshot-1"
    assert candidate["source_document_id"] == "snapshot-1"
    assert candidate["source_fragment_id"] == "snapshot:1"
    assert candidate["quote"] == "Python"
    assert candidate["start"] == 0
    assert candidate["end"] == 6
    assert candidate["version"]["validated_cv_snapshot_id"] == "snapshot-1"
    assert candidate["version"]["resume_id"] == "resume-1"
    assert candidate["version"]["evaluation_id"] == "evaluation-1"
    assert candidate["result_reference"] == (
        "validated_cv_snapshot:snapshot-1#evidence:snapshot:1:0-6"
    )

    assert position["source_object_type"] == "position_profile"
    assert position["source_object_id"] == "position-1"
    assert position["source_document_id"] == "position-1"
    assert position["source_fragment_id"] == "kg:1"
    assert position["quote"] == "Python required"
    assert position["version"]["position_id"] == "position-1"
    assert position["version"]["graph_version"] == "graph-42"
    assert position["result_reference"] == (
        "position_profile:position-1#evidence:kg:1:0-14"
    )

    gap_evidence = data["gap_analysis"]["prioritized_gaps"][0]["evidence"][0]
    assert gap_evidence["source_object_type"] == "validated_cv_snapshot"
    assert gap_evidence["source_object_id"] == "snapshot-1"
    assert gap_evidence["source_document_id"] == "snapshot-1"
    assert gap_evidence["result_reference"] == (
        "validated_cv_snapshot:snapshot-1#evidence:snapshot:1:0-6"
    )


def test_report_result_status_contract():
    data = enrich_report(_evaluation())
    assert data["evaluation"]["evaluation_status"] == "completed"
    assert data["gap_analysis"]["result_status"] == "completed"
    assert report_result_status(
        data["evaluation"],
        data["gap_analysis"],
    ) == "completed"

    empty_evaluation = {
        "evaluation_id": "evaluation-2",
        "skill_results": [],
        "hard_constraint_results": [],
        "final_match_result": None,
    }
    empty_gap = {
        "generation_status": "completed",
        "prioritized_gaps": [],
        "learning_path": [],
    }
    assert report_result_status(empty_evaluation, empty_gap) == "empty"
    insufficient = dict(
        empty_evaluation,
        final_match_result={
            "recommendation_level": "insufficient_information",
        },
    )
    assert report_result_status(insufficient, empty_gap) == "insufficient_data"
    failed = dict(
        empty_evaluation,
        error_code="MATCHING_EVALUATION_FAILED",
    )
    assert report_result_status(failed, empty_gap) == "failed"


def test_evidence_deletion_mapping_preserves_metrics_and_lineage():
    data = evidence_deletion_data(
        {
            "generation_status": "completed",
            "deletion_run_id": "deletion-run-1",
            "deletion_kind": "critical",
            "deleted_evidence_source_ids": ["snapshot:1"],
            "critical_evidence_source_ids": ["snapshot:1"],
            "explanation_factors": [
                {
                    "factor_id": "required_skill:requirement-1",
                    "factor_type": "required_skill",
                    "requirement_id": "requirement-1",
                    "reason_code": "EXACT_MATCH",
                    "criticality": "critical",
                    "evidence_source_ids": ["snapshot:1"],
                    "used_by_scorer": True,
                    "evidence_supported": True,
                }
            ],
            "baseline_score": 80,
            "ablated_score": 60,
            "retained_only_score": 80,
            "score_delta": -20,
            "dimension_deltas": [
                {
                    "dimension": "required_skills",
                    "baseline_score": 90,
                    "scenario_score": 50,
                    "delta": -40,
                }
            ],
            "baseline_hard_gate_status": "passed",
            "ablated_hard_gate_status": "passed",
            "added_gap_ids": ["skill_gap:requirement-1"],
            "added_action_ids": ["learn-requirement-1"],
            "comprehensiveness": 0.25,
            "sufficiency": 1.0,
            "unsupported_reason_rate": 0.0,
            "faithfulness_status": "faithful",
            "baseline_evaluation_id": "evaluation-1",
            "cv_profile_version": "cv-v1",
            "position_profile_version": "position-v1",
            "scoring_algorithm_version": "score-v1",
            "scoring_config_version": "config-v1",
            "classification_policy_version": "explanation-factor-policy.v1",
            "stability_threshold_points": 1.0,
            "hypothetical": True,
            "algorithm_version": "evidence-deletion-recompute.v1",
        },
        _evaluation(),
    )

    result = EvidenceDeletionResponse.model_validate(data)
    assert result.deletion_run_id == "deletion-run-1"
    assert result.deleted_evidence_source_ids == ["snapshot:1"]
    assert result.dimension_deltas[0].delta == -40
    assert result.added_gap_ids == ["skill_gap:requirement-1"]
    assert result.added_action_ids == ["learn-requirement-1"]
    assert result.faithfulness_status == "faithful"


def test_what_if_mapping_translates_native_evidence_for_bff_response():
    evaluation = _evaluation()
    data = what_if_data(
        {
            "generation_status": "completed",
            "scenario_id": "scenario-1",
            "baseline_evaluation": evaluation.evaluation,
            "scenario_evaluation": evaluation.evaluation,
            "actions": [],
            "baseline_score": 80,
            "scenario_score": 80,
            "score_delta": 0,
            "baseline_confidence": 0.8,
            "scenario_confidence": 0.8,
            "confidence_delta": 0,
            "baseline_hard_gate_status": "passed",
            "scenario_hard_gate_status": "passed",
            "dimension_deltas": [],
            "denominator_changed": False,
            "score_effect_status": "modeled",
            "baseline_evaluation_id": evaluation.evaluation_id,
            "target_type": "standard_position",
            "use_enterprise_weights": False,
            "algorithm_version": "counterfactual-profile.v2",
            "projected_if_completed": True,
            "projected_actions": [],
            "projected_score": 86,
            "projected_score_delta": 6,
            "projected_confidence": 0.85,
            "projected_recommendation": "potential_match",
            "projected_hard_gate_status": "passed",
        },
        evaluation,
    )

    result = WhatIfResponse.model_validate(data)
    assert result.score_delta == 0
    assert result.scenario_evaluation is not None
    candidate = result.scenario_evaluation.skill_results[0].candidate_evidence[0]
    assert candidate.source_object_type == "validated_cv_snapshot"

    # B-PATH-OUTCOME-BOUNDARY: what-if output is a modeled counterfactual
    # re-score; the explicit modeled_* fields must be populated alongside the
    # deprecated aliases and must not claim observed real-world learning.
    assert result.outcome_semantics == "modeled_counterfactual"
    assert result.observed_outcome is False
    assert result.modeled_score_delta == result.score_delta == 0
    assert result.modeled_confidence_delta == result.confidence_delta == 0
    assert result.modeled_final_score == result.scenario_score == 80
    assert result.projected_if_completed is True
    assert result.projected_score == 86
    assert result.projected_score_delta == 6
    assert result.projected_confidence == 0.85


def test_match_task_status_accepts_cancelled():
    task = MatchTaskResponse(
        task_id="task-cancelled",
        status="cancelled",
        canonical_status="cancelled",
    )
    assert task.status == "cancelled"
    assert BFFTaskStatus == MatchTaskResponse.model_fields["status"].annotation


def test_bff_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        MatchReportResponse(
            evaluation_id="evaluation-1",
            unexpected_extra="must be rejected",
        )
    with pytest.raises(ValidationError):
        MatchTaskResponse(
            task_id="task-1",
            status="succeeded",
            result_payload={"evaluation_id": "evaluation-1", "extra": "rejected"},
        )
    with pytest.raises(ValidationError):
        ResponsibilityCandidateResponse(
            experience_id="exp:1",
            text="text",
            extra="must be rejected",
        )


class FakeMatching:
    def get(self, actor, evaluation_id, *, correlation_id=""):
        return _evaluation()

    def list(self, actor):
        return []

    def position_name(self, position_id):
        return "Ready"


class FakeMatchingPositionCatalog:
    def __init__(self):
        self.items = [
            MatchingPositionCandidate(
                "position-ready", "Ready", "Engineering", "published", "active",
                "POS_READY", "position-taxonomy.v3.0.0",
            ),
            MatchingPositionCandidate(
                "position-no-profile", "No profile", "Engineering", "existing", "active",
                None, "position-taxonomy.v3.0.0",
            ),
            MatchingPositionCandidate(
                "position-deprecated", "Deprecated", "Engineering", "published", "deprecated",
                "POS_DEPRECATED", "position-taxonomy.v3.0.0",
            ),
            MatchingPositionCandidate(
                "position-graph-pending", "Graph pending", "Engineering", "published", "active",
                "POS_GRAPH_PENDING", "position-taxonomy.v3.0.0",
            ),
        ]

    def get(self, position_id):
        return next((item for item in self.items if item.position_id == position_id), None)

    def list(self):
        return list(self.items)


class FakeMatchingPositionContracts:
    def position_profile(self, position_id):
        if position_id == "position-ready":
            return {
                "graph_version": "graph-42",
                "requirement_graph": {
                    "graph_version": "standard-position-specialty-routes.v2"
                },
            }
        return None


class FakeMatchingIdentities:
    def authorize_request(self, *args, **kwargs):
        return None


class FakeMatchingResumes:
    def get(self, resume_id):
        return None


class FakeMatchingRanking:
    status = "ready"

    def ranking(self, actor, *, resume_id):
        del actor
        return {
            "resume_id": resume_id,
            "validated_cv_snapshot_id": "snapshot-1",
            "algorithm_version": "coarse-skill-coverage.v1",
            "status": self.status,
            "total": 1,
            "completed": 0,
            "items": [{
                "rank": 1,
                "position_id": "position-ready",
                "position_name": "Ready",
                "score": 75.0,
                "score_source": "coarse",
                "calculation_status": "preliminary",
                "evaluation_id": None,
                "task_id": None,
            }],
        }

    def run_ranking(self, actor, *, resume_id, correlation_id, concurrency):
        del actor, resume_id, correlation_id, concurrency

    def prepare_ranking(self, actor, *, resume_id):
        del actor, resume_id
        self.status = "ready"

    def cancel_ranking(self, actor, *, resume_id):
        del actor, resume_id
        self.status = "cancelled"


class FakeLearningPaths:
    def get(self, actor, path_id):
        evaluation = _evaluation()
        return RemoteLearningPath(
            path_id=f"matching-service:{evaluation.evaluation_id}",
            evaluation_id=evaluation.evaluation_id,
            target_position_id="position-1",
            gap_analysis=evaluation.gap_analysis,
            status="completed",
            created_at=None,
            updated_at=None,
        )


def test_match_report_endpoint_returns_enriched_evidence():
    original = app.state.container
    app.state.container = replace(original, matching=FakeMatching())
    try:
        response = client.get(
            "/api/v1/matches/reports/evaluation-1",
            headers=_headers(),
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    skill = response.json()["data"]["evaluation"]["skill_results"][0]
    candidate = skill["candidate_evidence"][0]
    position = skill["position_evidence"][0]
    assert candidate["source_object_type"] == "validated_cv_snapshot"
    assert candidate["source_object_id"] == "snapshot-1"
    assert candidate["result_reference"].startswith(
        "validated_cv_snapshot:snapshot-1#"
    )
    assert position["source_object_type"] == "position_profile"
    assert position["version"]["graph_version"] == "graph-42"
    assert response.json()["data"]["result_status"] == "completed"


def test_match_positions_contract_distinguishes_readiness_and_matches_preflight_gate():
    original = app.state.container
    use_cases = replace(
        original.matching,
        position_catalog=FakeMatchingPositionCatalog(),
        contracts=FakeMatchingPositionContracts(),
        identities=FakeMatchingIdentities(),
        resumes=FakeMatchingResumes(),
    )
    app.state.container = replace(original, matching=use_cases)
    try:
        response = client.get("/api/v1/matches/positions", headers=_headers())
    finally:
        app.state.container = original

    assert response.status_code == 200
    items = {item["position_id"]: item for item in response.json()["data"]}
    assert items["position-ready"] == {
        "position_id": "position-ready",
        "position_name": "Ready",
        "taxonomy_family_name": "Engineering",
        "status": "published",
        "lifecycle_status": "active",
        "matchable": True,
        "reason": "MATCHABLE",
        "blockers": [],
        "position_graph_version": "graph-42",
        "position_profile_version": None,
    }
    assert items["position-no-profile"]["blockers"] == ["POSITION_PROFILE_UNAVAILABLE"]
    assert items["position-deprecated"]["blockers"] == ["POSITION_DEPRECATED"]
    assert items["position-graph-pending"]["blockers"] == [
        "POSITION_GRAPH_VERSION_UNAVAILABLE"
    ]

    actor = AccountActor("user-1", "personal_user")
    for position_id in items:
        gate = use_cases.preflight(actor, resume_id="resume-1", position_id=position_id)
        position_blockers = [
            blocker for blocker in gate["blockers"] if not blocker.startswith("CV_")
        ]
        assert position_blockers == items[position_id]["blockers"]


def test_match_ranking_endpoint_returns_compact_progressive_rows():
    original = app.state.container
    app.state.container = replace(original, matching=FakeMatchingRanking())
    try:
        response = client.post(
            "/api/v1/matches/rankings",
            headers=_headers(),
            json={"resume_id": "resume-1"},
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"] == [{
        "rank": 1,
        "position_id": "position-ready",
        "position_name": "Ready",
        "score": 75.0,
        "score_source": "coarse",
        "calculation_status": "preliminary",
        "evaluation_id": None,
        "task_id": None,
        "error_code": None,
    }]


def test_match_ranking_endpoint_retries_failed_rows_on_explicit_start():
    class CompletedWithFailure(FakeMatchingRanking):
        def ranking(self, actor, *, resume_id):
            result = super().ranking(actor, resume_id=resume_id)
            return {
                **result,
                "status": "completed",
                "items": [{
                    **result["items"][0],
                    "calculation_status": "failed",
                    "error_code": "EVALUATION_TASK_EXECUTION_FAILED",
                }],
            }

        def prepare_ranking(self, actor, *, resume_id):
            self.prepared = True

    original = app.state.container
    fake = CompletedWithFailure()
    fake.prepared = False
    app.state.container = replace(original, matching=fake)
    try:
        response = client.post(
            "/api/v1/matches/rankings",
            headers=_headers(),
            json={"resume_id": "resume-1"},
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "running"
    assert fake.prepared


def test_match_ranking_endpoint_can_cancel_a_batch_run():
    original = app.state.container
    app.state.container = replace(original, matching=FakeMatchingRanking())
    try:
        response = client.post(
            "/api/v1/matches/rankings/cancel",
            headers=_headers(),
            json={"resume_id": "resume-1"},
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"


def test_learning_path_endpoint_returns_enriched_gap_evidence():
    original = app.state.container
    app.state.container = replace(original, learning_paths=FakeLearningPaths())
    try:
        response = client.get(
            "/api/v1/learning-paths/matching-service:evaluation-1",
            headers=_headers(),
        )
    finally:
        app.state.container = original

    assert response.status_code == 200
    data = response.json()["data"]
    gap_evidence = data["gap_analysis"]["prioritized_gaps"][0]["evidence"][0]
    assert gap_evidence["source_object_type"] == "matching_evidence"
    assert gap_evidence["source_object_id"] == "evaluation-1"
    assert gap_evidence["source_fragment_id"] == "snapshot:1"
    assert gap_evidence["result_reference"] == (
        "matching_evidence:evaluation-1#evidence:snapshot:1:0-6"
    )
