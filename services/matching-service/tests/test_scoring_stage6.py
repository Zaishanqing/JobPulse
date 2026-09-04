from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application.evaluation import MatchEvaluationService
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import (
    ScoringConfig,
    ScoringWeights,
    _skill_score,
    _responsibility_score,
    build_contribution_ledger,
    score_match_evaluation,
)
from app.main import app

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-token"})


def _refresh(payload: dict) -> dict:
    payload["profile_version"] = "profile-source.v1"
    return payload


def _models(cv_payload: dict, position_payload: dict):
    return (
        CVMatchProfile.model_validate(cv_payload),
        PositionMatchProfile.model_validate(position_payload),
    )


def _evaluation(cv_payload: dict, position_payload: dict):
    return MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def _full_context_payloads(cv_payload: dict, position_payload: dict, overrides: dict):
    cv = deepcopy(cv_payload)
    position = deepcopy(position_payload)
    cv["projects"] = deepcopy(overrides["cv"]["projects"])
    cv["match_features"].extend(deepcopy(overrides["cv"]["match_features"]))
    for key, value in overrides["position"].items():
        position[key] = deepcopy(value)
    return _refresh(cv), _refresh(position)


def _high_evaluation(cv_payload: dict, position_payload: dict, overrides: dict):
    cv, position = _full_context_payloads(cv_payload, position_payload, overrides)
    evaluation = _evaluation(cv, position)
    required = next(
        item for item in evaluation.skill_results if item.importance_level == "required"
    )
    skills = tuple(
        item.model_copy(
            update={
                "match_status": "matched",
                "match_type": "exact",
                "reason_code": "EXACT_SKILL_LEVEL_MET",
                "candidate_evidence": required.candidate_evidence,
                "confidence": required.confidence,
            }
        )
        if item.importance_level == "bonus"
        else item
        for item in evaluation.skill_results
    )
    return evaluation.model_copy(update={"skill_results": skills}), cv, position


def test_full_dimension_high_match_has_explainable_contributions(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    assert result.overall_score == 100.0
    assert result.recommendation_level == "strong_match"
    assert result.hard_gate_status == "passed"
    assert {item.dimension for item in result.dimension_scores} == {
        "required_skills",
        "responsibilities",
        "projects",
        "capability_level",
        "hard_conditions",
        "business_scenarios",
            "bonus_transferable",
            "requirement_groups",
    }
    assert abs(sum(item.weighted_points for item in result.score_contributions) - 100) < 1e-4
    assert all(item.reason_code for item in result.score_contributions)
    assert all(
        item.position_evidence or item.candidate_evidence or item.relation_evidence
        for item in result.score_contributions
    )
    assert result.strengths
    assert not result.gaps


def test_explicit_hard_failure_gates_recommendation_but_keeps_analysis(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    failed = evaluation.hard_constraint_results[0].model_copy(
        update={
            "status": "fail",
            "reason_code": "CONSTRAINT_NOT_SATISFIED",
            "confidence": 1.0,
        }
    )
    evaluation = evaluation.model_copy(
        update={
            "hard_constraint_results": (
                failed,
                *evaluation.hard_constraint_results[1:],
            )
        }
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    assert result.hard_gate_status == "failed"
    assert result.recommendation_level == "not_recommended"
    assert result.overall_score is not None and result.overall_score > 80
    assert len(result.dimension_scores) == 8
    assert any(item.result_id == failed.requirement_id for item in result.gaps)


def test_missing_required_skill_blocks_strong_recommendation(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    missing_required = next(
        item for item in evaluation.skill_results if item.importance_level == "required"
    ).model_copy(
        update={
            "match_status": "missing",
            "match_type": "none",
            "reason_code": "REQUIRED_SKILL_NOT_OBSERVED",
            "confidence": 1.0,
            "candidate_evidence": (),
        }
    )
    evaluation = evaluation.model_copy(
        update={
            "skill_results": (
                missing_required,
                *tuple(
                    item
                    for item in evaluation.skill_results
                    if item.requirement_id != missing_required.requirement_id
                ),
            )
        }
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    assert result.recommendation_level != "strong_match"


def test_material_uncertainty_requires_verified_responsibility_for_strong(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    responsibilities = tuple(
        item.model_copy(
            update={
                "match_status": "not_observed",
                "status_detail": "not_observed",
            }
        )
        for item in evaluation.responsibility_results
    )
    material = evaluation.model_copy(
        update={
            "information_sufficiency_level": "material",
            "responsibility_results": responsibilities,
        }
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(material, cv, position)
    result_without_floor = score_match_evaluation(
        material,
        cv,
        position,
        ScoringConfig(strong_evidence_floor=False),
    )

    assert result.recommendation_level == "potential_match"
    assert result_without_floor.recommendation_level == "strong_match"


def test_unknown_and_unresolved_lower_confidence_without_zero_scoring(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    baseline_cv, baseline_position = _models(cv_payload, position_payload)
    baseline = score_match_evaluation(evaluation, baseline_cv, baseline_position)
    hard = tuple(
        item.model_copy(update={"status": "unknown", "confidence": 0.0})
        for item in evaluation.hard_constraint_results
    )
    skills = tuple(
        item.model_copy(update={"match_status": "unresolved", "confidence": 0.0})
        for item in evaluation.skill_results
    )
    uncertain_evaluation = evaluation.model_copy(
        update={"hard_constraint_results": hard, "skill_results": skills}
    )

    result = score_match_evaluation(
        uncertain_evaluation, baseline_cv, baseline_position
    )

    assert result.overall_score is not None and result.overall_score > 0
    assert result.match_confidence < baseline.match_confidence
    assert result.recommendation_level == "insufficient_information"
    assert result.hard_gate_status == "uncertain"
    assert result.uncertain_items
    assert all(
        item.score_value is None and item.weighted_points == 0
        for item in result.score_contributions
        if item.status in {"unknown", "unresolved"}
    )


def test_unavailable_position_context_is_excluded_from_score_denominator(
    ready_cv_json, ready_position_json
):
    position_payload = deepcopy(ready_position_json)
    for field in ("tools", "industries", "business_scenarios"):
        position_payload[field] = {
            "values": [],
            "evidence_refs": [],
            "availability": "unavailable",
        }
    evaluation = _evaluation(ready_cv_json, position_payload)
    cv, position = _models(ready_cv_json, position_payload)

    result = score_match_evaluation(evaluation, cv, position)
    business_context = next(
        item for item in result.dimension_scores if item.dimension == "business_scenarios"
    )

    assert position.industries.availability == "unavailable"
    assert position.business_scenarios.availability == "unavailable"
    assert evaluation.scenario_results == ()
    assert business_context.score is None
    assert business_context.effective_weight == 0.0


def test_related_skill_does_not_directly_add_skill_score(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    exact = next(
        item for item in evaluation.skill_results if item.importance_level == "required"
    )
    related = exact.model_copy(
        update={
            "requirement_id": "required:related_fixture",
            "match_status": "partial",
            "match_type": "related",
            "reason_code": "RELATED_SKILL_PARTIAL_MATCH",
            "relation_evidence": exact.position_evidence,
        }
    )
    deterministic = evaluation.responsibility_results[0]
    semantic = deterministic.model_copy(
        update={
            "requirement_id": "responsibility:semantic_fixture",
                "match_status": "partial",
                "match_type": "semantic",
                "reason_code": "SEMANTIC_RESPONSIBILITY_PARTIAL_MATCH",
                "status_detail": None,
            }
        )
    evaluation = evaluation.model_copy(
        update={
            "skill_results": (*evaluation.skill_results, related),
            "responsibility_results": (
                *evaluation.responsibility_results,
                semantic,
            ),
        }
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)
    contributions = {item.result_id: item for item in result.score_contributions}

    assert contributions[exact.requirement_id].score_value == 1.0
    assert contributions[related.requirement_id].score_value == 0.0
    assert contributions[deterministic.requirement_id].score_value == 1.0
    assert contributions[semantic.requirement_id].score_value == 0.4


def test_required_and_bonus_weights_are_separate(ready_cv_json, ready_position_json):
    result = _evaluation(ready_cv_json, ready_position_json).final_match_result

    assert result is not None
    dimensions = {item.dimension: item for item in result.dimension_scores}
    assert dimensions["required_skills"].configured_weight == 0.35
    assert dimensions["bonus_transferable"].configured_weight == 0.05
    assert any(
        item.dimension == "required_skills" for item in result.score_contributions
    )
    assert any(
        item.dimension == "bonus_transferable" for item in result.score_contributions
    )


def test_required_relation_is_not_counted_again_as_bonus(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    required = next(
        item for item in evaluation.skill_results if item.importance_level == "required"
    ).model_copy(
        update={
            "match_status": "partial",
            "match_type": "related",
            "reason_code": "RELATED_SKILL_PARTIAL_MATCH",
        }
    )
    evaluation = evaluation.model_copy(update={"skill_results": (required,)})
    cv, position = _models(ready_cv_json, ready_position_json)

    result = score_match_evaluation(evaluation, cv, position)

    related = [
        item
        for item in result.score_contributions
        if item.result_id == required.requirement_id
    ]
    assert {item.dimension for item in related} == {
        "required_skills",
        "capability_level",
    }


def test_all_required_skills_unknown_forces_insufficient_information(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    skills = tuple(
        item.model_copy(update={"match_status": "unknown", "confidence": 0.0})
        if item.importance_level == "required"
        else item
        for item in evaluation.skill_results
    )
    evaluation = evaluation.model_copy(update={"skill_results": skills})
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    assert result.recommendation_level == "insufficient_information"


def test_partial_hard_constraint_makes_gate_uncertain(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    hard = tuple(
        item.model_copy(update={"status": "partial", "confidence": 0.8})
        for item in evaluation.hard_constraint_results
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(
        evaluation.model_copy(update={"hard_constraint_results": hard}), cv, position
    )

    assert result.hard_gate_status == "uncertain"
    assert result.recommendation_level != "strong_match"


def test_partial_project_uses_measured_confidence(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    project = evaluation.project_results[0].model_copy(
        update={
            "match_status": "partial",
            "match_type": "none",
            "confidence": 0.25,
            "covered_skill_ids": (),
        }
    )
    cv, position = _models(ready_cv_json, ready_position_json)

    result = score_match_evaluation(
        evaluation.model_copy(update={"project_results": (project,)}), cv, position
    )
    contribution = next(
        item
        for item in result.score_contributions
        if item.dimension == "projects" and item.result_id == project.requirement_id
    )

    assert contribution.score_value == 0.25


def test_weight_configuration_changes_version_and_score(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    responsibility = evaluation.responsibility_results[0].model_copy(
        update={
            "match_status": "not_observed",
            "reason_code": "RESPONSIBILITY_NOT_OBSERVED",
            "confidence": 1.0,
        }
    )
    evaluation = evaluation.model_copy(
        update={"responsibility_results": (responsibility,)}
    )
    cv, position = _models(ready_cv_json, ready_position_json)
    baseline = score_match_evaluation(evaluation, cv, position)
    changed_config = ScoringConfig(
        scoring_config_version="scoring-config.required-15-responsibility-40.v1",
        weights=ScoringWeights(
            required_skills=0.15,
            responsibilities=0.40,
            projects=0.15,
            capability_level=0.10,
            hard_conditions=0.10,
            business_scenarios=0.05,
            bonus_transferable=0.05,
        ),
    )

    changed = score_match_evaluation(evaluation, cv, position, changed_config)

    assert changed.scoring_config_version != baseline.scoring_config_version
    assert changed.overall_score != baseline.overall_score


def test_api_keeps_previous_fields_and_adds_final_result(
    ready_cv_json, ready_position_json
):
    response = client.post(
        "/api/v1/evaluations",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "skill_results" in data
    assert "responsibility_results" in data
    assert data["final_match_result"]["algorithm_version"] == "explainable-scoring.v4"
    assert data["final_match_result"]["position_graph_version"] == "graph-fixture-v1"


def _missing_dimension_result(
    ready_cv_json, ready_position_json, context_overrides_json, *, field: str
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    evaluation = evaluation.model_copy(update={field: ()})
    cv, position = _models(cv_payload, position_payload)
    return score_match_evaluation(evaluation, cv, position)


def _dimension(result, dimension: str):
    return next(item for item in result.dimension_scores if item.dimension == dimension)


def test_missing_responsibilities_marks_missing_evaluation(
    ready_cv_json, ready_position_json, context_overrides_json
):
    result = _missing_dimension_result(
        ready_cv_json,
        ready_position_json,
        context_overrides_json,
        field="responsibility_results",
    )

    assert "responsibilities" in result.missing_evaluation_dimensions
    assert "responsibilities" in result.expected_dimensions
    dimension = _dimension(result, "responsibilities")
    assert dimension.dimension_status == "missing_evaluation"
    assert dimension.score is None
    assert dimension.confidence == 0.0
    assert result.recommendation_level == "insufficient_information"


def test_two_level_normalization_preserves_missing_dimension_weight_mass(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    evaluation = evaluation.model_copy(update={"responsibility_results": ()})
    cv, position = _models(cv_payload, position_payload)
    config = ScoringConfig(two_level_requirement_normalization=True)
    result = score_match_evaluation(evaluation, cv, position, config)

    assert "responsibilities" in result.missing_evaluation_dimensions
    assert result.recommendation_level == "insufficient_information"
    responsibilities = _dimension(result, "responsibilities")
    contribution_mass = sum(
        item.effective_weight for item in result.score_contributions
    )
    assert (
        abs(contribution_mass + responsibilities.effective_weight - 1.0)
        < 1e-4
    )
    ledger = build_contribution_ledger(evaluation, cv, position, config)
    assert abs(ledger.overall_score - result.overall_score) < 1e-4


def test_missing_projects_marks_missing_evaluation(
    ready_cv_json, ready_position_json, context_overrides_json
):
    result = _missing_dimension_result(
        ready_cv_json,
        ready_position_json,
        context_overrides_json,
        field="project_results",
    )

    assert "projects" in result.missing_evaluation_dimensions
    dimension = _dimension(result, "projects")
    assert dimension.dimension_status == "missing_evaluation"
    assert dimension.confidence == 0.0
    assert result.recommendation_level == "insufficient_information"


def test_missing_business_scenarios_marks_missing_evaluation(
    ready_cv_json, ready_position_json, context_overrides_json
):
    result = _missing_dimension_result(
        ready_cv_json,
        ready_position_json,
        context_overrides_json,
        field="scenario_results",
    )

    assert "business_scenarios" in result.missing_evaluation_dimensions
    dimension = _dimension(result, "business_scenarios")
    assert dimension.dimension_status == "missing_evaluation"
    assert dimension.confidence == 0.0
    assert result.recommendation_level == "insufficient_information"


def test_all_uncertain_differs_from_whole_dimension_missing(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    uncertain = tuple(
        item.model_copy(update={"match_status": "unknown", "confidence": 0.0})
        for item in evaluation.responsibility_results
    )
    uncertain = tuple(
        item.model_copy(update={"status_detail": None})
        for item in uncertain
    )
    evaluation = evaluation.model_copy(update={"responsibility_results": uncertain})
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    assert "responsibilities" not in result.missing_evaluation_dimensions
    dimension = _dimension(result, "responsibilities")
    assert dimension.dimension_status == "uncertain"
    assert dimension.applicable_count == len(uncertain)


def test_graph_missing_leaf_is_rejected_at_contract_boundary(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _full_context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    position_payload["required_skills"][0]["requirement_id"] = "req-python"
    graph_evidence = position_payload["required_skills"][0]["evidence_refs"][0]
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.leaf-missing.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-root",
                "group_type": "and",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-python"},
                    {"node_type": "requirement_ref", "ref_id": "req-not-in-profile"},
                ],
                "evidence": graph_evidence,
                "confidence": 0.9,
            }
        ],
        "unresolved_items": [],
    }

    with pytest.raises(ValidationError, match="unknown requirements"):
        _models(cv_payload, position_payload)


def test_missing_dimension_ledger_matches_formal_score(
    ready_cv_json, ready_position_json, context_overrides_json
):
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    evaluation = evaluation.model_copy(update={"responsibility_results": ()})
    cv, position = _models(cv_payload, position_payload)

    final = score_match_evaluation(evaluation, cv, position)
    ledger = build_contribution_ledger(evaluation, cv, position)

    assert ledger.overall_score == final.overall_score
    assert abs(ledger.weighted_points_sum() - (final.overall_score or 0.0)) < 0.02
    assert final.recommendation_level == "insufficient_information"


def test_unknown_match_type_fails_closed_instead_of_matched_full_score():
    item = SimpleNamespace(match_status="matched", match_type="new_semantic_type")

    assert _skill_score(item, ScoringConfig()) is None


def _responsibility_item(
    match_status: str,
    status_detail: str | None,
    *,
    match_type: str = "deterministic",
):
    return SimpleNamespace(
        match_status=match_status,
        match_type=match_type,
        status_detail=status_detail,
        reason_code="RESPONSIBILITY_MATCHED",
        confidence=0.9,
    )


@pytest.mark.parametrize(
    ("match_status", "status_detail", "expected"),
    [
        ("matched", "matched", 1.0),
        ("matched", "partial", 0.5),
        ("matched", "insufficient_evidence", None),
        ("not_observed", "not_observed", 0.0),
        ("matched", None, 1.0),
    ],
)
def test_responsibility_score_uses_decision_policy_final_state(
    match_status, status_detail, expected
):
    item = _responsibility_item(match_status, status_detail)
    config = ScoringConfig()
    assert _responsibility_score(item, config) == expected


def test_responsibility_partial_uses_existing_deterministic_partial_constant():
    config = ScoringConfig()
    assert config.deterministic_context_partial_score == 0.5
    item = _responsibility_item("matched", "partial", match_type="semantic")
    assert _responsibility_score(item, config) == config.deterministic_context_partial_score


def test_responsibility_contribution_status_and_score_follow_final_policy_state(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    responsibility = evaluation.responsibility_results[0].model_copy(
        update={
            "match_status": "matched",
            "status_detail": "partial",
        }
    )
    cv, position = _models(ready_cv_json, ready_position_json)
    result = score_match_evaluation(
        evaluation.model_copy(
            update={"responsibility_results": (responsibility,)}
        ),
        cv,
        position,
    )
    contribution = next(
        item
        for item in result.score_contributions
        if item.dimension == "responsibilities"
    )
    assert contribution.status == "partial"
    assert contribution.score_value == 0.5

def test_normal_position_without_project_requirement_uses_applied_experience_semantics(
    ready_cv_json, ready_position_json, context_overrides_json
):
    """A normal position without an explicit project requirement must not be
    explained as a fixed '项目经验要求' by the scorer.

    The fixture position declares required skills + core responsibilities but has
    no project-experience-required contract. The ``projects`` dimension is kept
    as the Applied Experience (综合实践证据) evidence channel, and the explanation
    frames it as "did the candidate actually use the required abilities" instead
    of "this JD asks for project experience".
    """
    evaluation, cv_payload, position_payload = _high_evaluation(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)

    result = score_match_evaluation(evaluation, cv, position)

    projects = _dimension(result, "projects")
    assert "projects" in result.expected_dimensions
    assert projects.configured_weight == 0.15  # weight unchanged by this task

    # Applied Experience framing is present and no fixed-requirement claim leaks.
    assert "Applied Experience" in result.explanation
    assert "综合实践证据" in result.explanation
    assert "项目经验要求" not in result.explanation
    assert "fixed 'project-experience requirement'" in result.explanation
