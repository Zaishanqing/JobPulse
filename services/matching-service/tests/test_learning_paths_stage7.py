from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.evaluation import MatchEvaluationService
from app.application.learning_paths import LearningPathService
from app.application.route_planning import LearningRoutePlanner
from app.domain.evaluation import RequirementGroupResult
from app.domain.gap_analysis import (
    GapAnalysisConfig,
    build_gap_analysis,
    gap_policy_hash,
)
from app.domain.profiles import PositionMatchProfile
from app.domain.skill_relations import SkillRelation
from app.infrastructure.relation_sources import InMemorySkillRelationSource
from app.main import app

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-token"})


def _evaluation(cv_payload: dict, position_payload: dict):
    return MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def _required(evaluation):
    return next(
        item for item in evaluation.skill_results if item.importance_level == "required"
    )


def _skill_result(base, requirement_id: str, skill_id: str, **updates):
    values = {
        "requirement_id": requirement_id,
        "skill_id": skill_id,
        "skill_name": skill_id,
        "importance_level": "required",
        "requirement_weight": 1.0,
        **updates,
    }
    return base.model_copy(update=values)


def _relation(
    relation_id: str,
    source: str,
    target: str,
    *,
    graph_version: str = "graph-fixture-v1",
    relation_type: str = "transferable",
) -> SkillRelation:
    quote = f"{source} transfers to {target}"
    return SkillRelation.model_validate(
        {
            "relation_id": relation_id,
            "source_skill_id": source,
            "target_skill_id": target,
            "relation_type": relation_type,
            "source_system": "test-graph",
            "graph_version": graph_version,
            "confidence": 0.9,
                "evidence_refs": [
                    {
                        "source_id": f"graph:{relation_id}",
                        "quote": quote,
                        "start": 0,
                        "end": len(quote),
                        "alignment": "exact",
                    }
                ],
        }
    )


def _target_position(ready_position_json: dict, skill_id: str) -> dict:
    position = {**ready_position_json}
    requirement = {
        **ready_position_json["required_skills"][0],
        "skill_id": skill_id,
        "canonical_name": skill_id,
        "required_level": "working",
    }
    position["required_skills"] = [requirement]
    return position


def test_required_skill_missing_is_prioritized_before_context_gaps(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    missing = _skill_result(
        base,
        "required:skill_missing_fixture",
        "skill_missing_fixture",
        match_status="missing",
        match_type="none",
        reason_code="REQUIRED_SKILL_NOT_OBSERVED",
        candidate_declared_level=None,
        candidate_demonstrated_level=None,
        candidate_evidence=(),
        confidence=1.0,
    )
    evaluation = evaluation.model_copy(update={"skill_results": (missing,)})

    result = build_gap_analysis(evaluation)

    assert result.prioritized_gaps[0].requirement_id == missing.requirement_id
    assert result.prioritized_gaps[0].gap_type == "required_skill_missing"
    assert result.prioritized_gaps[0].priority in {"critical", "high"}
    step = next(
        item for item in result.learning_path if item.target_skill_id == missing.skill_id
    )
    assert step.estimated_hours == 8.0
    assert "reason:REQUIRED_SKILL_NOT_OBSERVED" in step.basis


def test_level_gap_and_evidence_gap_are_separate(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    weak = _skill_result(
        base,
        "required:skill_level_fixture",
        "skill_level_fixture",
        match_status="weak",
        match_type="exact",
        required_level="advanced",
        candidate_demonstrated_level="working",
        reason_code="EXACT_SKILL_LEVEL_BELOW",
    )
    evidence_only = _skill_result(
        base,
        "required:skill_evidence_fixture",
        "skill_evidence_fixture",
        match_status="declared_only",
        match_type="exact",
        candidate_demonstrated_level="unknown",
        candidate_evidence=(),
        reason_code="SKILL_DECLARED_WITHOUT_EVIDENCE",
    )
    evaluation = evaluation.model_copy(
        update={"skill_results": (weak, evidence_only)}
    )

    result = build_gap_analysis(evaluation)
    by_id = {item.requirement_id: item for item in result.prioritized_gaps}

    assert by_id[weak.requirement_id].gap_type == "skill_level_gap"
    assert by_id[weak.requirement_id].current_level == "working"
    assert by_id[weak.requirement_id].target_level == "advanced"
    assert by_id[evidence_only.requirement_id].gap_type == "usage_evidence_gap"
    assert "CANDIDATE_EVIDENCE_MISSING" in by_id[evidence_only.requirement_id].reason_codes
    assert "POSITION_EVIDENCE_MISSING" not in by_id[evidence_only.requirement_id].reason_codes
    evidence_step = next(
        item
        for item in result.learning_path
        if item.target_skill_id == evidence_only.skill_id
    )
    assert evidence_step.estimated_hours == 2.0
    assert "reason:SKILL_DECLARED_WITHOUT_EVIDENCE" in evidence_step.basis


def test_gap_reason_codes_distinguish_missing_position_evidence(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    weak = _skill_result(
        base,
        "required:skill_position_evidence_fixture",
        "skill_position_evidence_fixture",
        match_status="weak",
        match_type="exact",
        required_level="advanced",
        candidate_demonstrated_level="working",
        position_evidence=(),
        reason_code="EXACT_SKILL_LEVEL_BELOW",
    )

    result = build_gap_analysis(evaluation.model_copy(update={"skill_results": (weak,)}))
    gap = result.prioritized_gaps[0]

    assert "POSITION_EVIDENCE_MISSING" in gap.reason_codes
    assert "CANDIDATE_EVIDENCE_MISSING" not in gap.reason_codes


def test_responsibility_gap_reason_uses_final_partial_status(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    responsibility = evaluation.responsibility_results[0].model_copy(
        update={
            "match_status": "matched",
            "status_detail": "partial",
            "reason_code": "RESPONSIBILITY_MATCHED",
        }
    )

    result = build_gap_analysis(
        evaluation.model_copy(update={"responsibility_results": (responsibility,)})
    )
    gap = next(
        item
        for item in result.prioritized_gaps
        if item.requirement_id == responsibility.requirement_id
    )

    assert "RESPONSIBILITY_PARTIALLY_MATCHED" in gap.reason_codes
    assert "RESPONSIBILITY_GAP" in gap.reason_codes
    assert "RESPONSIBILITY_MATCHED" not in gap.reason_codes


def test_responsibility_action_names_capability_and_defines_measurable_delivery(
    ready_cv_json, ready_position_json
):
    requirement_id = "responsibility:agentic-serving"
    position_payload = {
        **ready_position_json,
        "core_responsibilities": [
            "负责 Agentic AI 的服务部署、性能测试和优化"
        ],
        "responsibility_requirements": [
            {
                "requirement_id": requirement_id,
                "text": "负责 Agentic AI 的服务部署、性能测试和优化",
                "skill_ids": ["skill_python"],
                "resolution_status": "resolved",
                "evidence_refs": [
                    {
                        "source_id": "jd:responsibility:agentic",
                        "quote": "负责 Agentic AI 的服务部署、性能测试和优化",
                        "start": 0,
                        "end": 30,
                        "alignment": "exact",
                    }
                ],
            }
        ],
    }
    evaluation = _evaluation(ready_cv_json, position_payload)
    responsibility_result = evaluation.responsibility_results[0].model_copy(
        update={
            "match_status": "not_observed",
            "status_detail": "not_observed",
            "candidate_experience_id": None,
            "candidate_experience": None,
            "candidate_evidence": (),
            "reason_code": "RESPONSIBILITY_NOT_OBSERVED",
        }
    )
    evaluation = evaluation.model_copy(
        update={"responsibility_results": (responsibility_result,)}
    )
    analysis = build_gap_analysis(evaluation, include_learning_steps=False)
    position = PositionMatchProfile.model_validate(position_payload)

    groups = LearningRoutePlanner._action_groups(
        analysis.prioritized_gaps,
        position,
        evaluation=evaluation,
    )
    action = next(
        item
        for group in groups
        for item in group
        if requirement_id in item.target_requirement_ids
    )

    assert action.canonical_name == "Agentic AI 服务部署与性能工程"
    assert "QPS" in " ".join(action.acceptance_criteria)
    assert "压测脚本" in (action.deliverable or "")
    assert action.cost_band is not None
    assert action.cost_band.max_hours < action.estimated_hours * 2


def test_satisfied_standard_position_one_of_clause_suppresses_unused_alternative_action(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    missing = _skill_result(
        base,
        "required:unused-language-alternative",
        "skill_cpp",
        match_status="missing",
        match_type="none",
        reason_code="REQUIRED_SKILL_NOT_OBSERVED",
        candidate_evidence=(),
    )
    alternative = RequirementGroupResult(
        group_id="standard-clause:language",
        group_type="one_of",
        priority="required",
        status="satisfied",
        required_count=1,
        satisfied_count=1,
        evaluable_count=2,
        child_result_ids=(base.requirement_id, missing.requirement_id),
        covered_result_ids=(base.requirement_id, missing.requirement_id),
        covered_dimensions=("required_skills",),
        is_root=False,
        score=1.0,
        reason_code="REQUIREMENT_GROUP_SATISFIED",
        confidence=1.0,
        position_evidence=base.position_evidence,
    )
    evaluation = evaluation.model_copy(
        update={
            "skill_results": (base, missing),
            "requirement_group_results": (alternative,),
        }
    )
    analysis = build_gap_analysis(evaluation, include_learning_steps=False)
    position = PositionMatchProfile.model_validate(ready_position_json)

    groups = LearningRoutePlanner._action_groups(
        analysis.prioritized_gaps,
        position,
        evaluation=evaluation,
    )

    assert not any(
        action.skill_id == "skill_cpp" for group in groups for action in group
    )


def test_prerequisite_gap_is_ordered_before_dependent_skill(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    prerequisite = _skill_result(
        base,
        "required:skill_foundation_fixture",
        "skill_foundation_fixture",
        match_status="missing",
        match_type="none",
        reason_code="REQUIRED_SKILL_NOT_OBSERVED",
        candidate_evidence=(),
    )
    dependent = _skill_result(
        base,
        "required:skill_advanced_fixture",
        "skill_advanced_fixture",
        match_status="missing",
        match_type="prerequisite",
        relation_type="prerequisite",
        related_candidate_skill_id="skill_foundation_fixture",
        prerequisite_skill_ids=("skill_foundation_fixture",),
        reason_code="PREREQUISITE_ONLY",
        relation_evidence=base.position_evidence,
    )
    evaluation = evaluation.model_copy(
        update={"skill_results": (dependent, prerequisite)}
    )

    result = build_gap_analysis(evaluation)
    skill_steps = {
        item.target_skill_id: item for item in result.learning_path if item.target_skill_id
    }

    assert skill_steps["skill_advanced_fixture"].prerequisite_skill_ids == (
        "skill_foundation_fixture",
    )
    assert (
        skill_steps["skill_foundation_fixture"].step_order
        < skill_steps["skill_advanced_fixture"].step_order
    )


def test_transferable_skill_reduces_starting_point_without_removing_gap(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    missing = _skill_result(
        base,
        "required:skill_target_plain",
        "skill_target_plain",
        match_status="missing",
        match_type="none",
        reason_code="REQUIRED_SKILL_NOT_OBSERVED",
        candidate_evidence=(),
    )
    transferable = _skill_result(
        base,
        "required:skill_target_transferable",
        "skill_target_transferable",
        match_status="partial",
        match_type="transferable",
        relation_type="transferable",
        related_candidate_skill_id="skill_python",
        transferability_score=0.7,
        reason_code="TRANSFERABLE_SKILL_PARTIAL_MATCH",
        relation_evidence=base.position_evidence,
    )
    plain_result = build_gap_analysis(
        evaluation.model_copy(update={"skill_results": (missing,)})
    )
    transfer_result = build_gap_analysis(
        evaluation.model_copy(update={"skill_results": (transferable,)})
    )
    plain_gap = next(item for item in plain_result.prioritized_gaps if item.skill_id)
    transfer_gap = next(item for item in transfer_result.prioritized_gaps if item.skill_id)

    assert transfer_gap.gap_type == "required_skill_missing"
    assert transfer_gap.transferable_skill_ids == ("skill_python",)
    assert transfer_gap.priority_score < plain_gap.priority_score
    step = next(
        item
        for item in transfer_result.learning_path
        if item.target_skill_id == transferable.skill_id
    )
    assert step.estimated_hours < 8.0
    assert "reason:TRANSFERABLE_SKILL_PARTIAL_MATCH" in step.basis


def test_hard_constraint_failure_is_a_separate_gap(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    failed = evaluation.hard_constraint_results[0].model_copy(
        update={
            "status": "fail",
            "reason_code": "CONSTRAINT_NOT_SATISFIED",
            "confidence": 1.0,
        }
    )
    final = evaluation.final_match_result.model_copy(
        update={"hard_gate_status": "failed"}
    )
    evaluation = evaluation.model_copy(
        update={
            "hard_constraint_results": (
                failed,
                *evaluation.hard_constraint_results[1:],
            ),
            "final_match_result": final,
        }
    )

    result = build_gap_analysis(evaluation)
    gap = next(
        item
        for item in result.prioritized_gaps
        if item.requirement_id == failed.requirement_id
    )

    assert gap.gap_type == "hard_constraint_gap"
    assert "HARD_REQUIREMENT_GAP" in gap.reason_codes
    assert result.estimated_readiness <= 0.25
    step = next(
        item
        for item in result.learning_path
        if failed.requirement_id in item.source_requirement_ids
    )
    assert "reason:CONSTRAINT_NOT_SATISFIED" in step.basis
    assert "evidence:" not in step.basis or any(
        value.startswith("evidence:") for value in step.basis
    )


def test_unknown_and_unresolved_create_information_gaps_not_false_missing(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    unknown = _skill_result(
        base,
        "required:skill_unknown_fixture",
        "skill_unknown_fixture",
        match_status="unknown",
        reason_code="CANDIDATE_SKILL_UNKNOWN",
        candidate_evidence=(),
    )
    unresolved = _skill_result(
        base,
        "required:skill_unresolved_fixture",
        "skill_unresolved_fixture",
        match_status="unresolved",
        reason_code="CANDIDATE_SKILL_UNRESOLVED",
        candidate_evidence=(),
    )
    evaluation = evaluation.model_copy(
        update={"skill_results": (unknown, unresolved)}
    )

    result = build_gap_analysis(evaluation)
    by_id = {item.requirement_id: item for item in result.prioritized_gaps}

    assert by_id[unknown.requirement_id].gap_type == "evidence_gap"
    assert by_id[unresolved.requirement_id].gap_type == "unresolved_gap"
    assert all(
        item.gap_type != "required_skill_missing"
        for item in result.prioritized_gaps
        if item.requirement_id in {unknown.requirement_id, unresolved.requirement_id}
    )
    unresolved_step = next(
        item
        for item in result.learning_path
        if item.target_skill_id == unresolved.skill_id
    )
    assert "reason:CANDIDATE_SKILL_UNRESOLVED" in unresolved_step.basis


def test_stale_evaluation_is_rejected(ready_cv_json, ready_position_json):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    stale = evaluation.model_copy(
        update={
            "final_match_result": evaluation.final_match_result.model_copy(
                update={"source_evaluation_id": "eval_other-source"}
            )
        }
    )

    result = LearningPathService().generate(
        {"evaluation": stale.model_dump(mode="json")}
    )

    assert result.generation_status == "rejected"


@pytest.mark.parametrize("bad_value", ["false", 0, 1, None])
def test_learning_path_rejects_non_boolean_enterprise_weight(bad_value):
    result = LearningPathService().generate(
        {"use_enterprise_weights": bad_value}
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "LEARNING_PATH_OPTION_INVALID"


def test_semantic_learning_path_compares_profile_versions_not_profile_ids(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    responsibility = evaluation.responsibility_results[0].model_copy(
        update={
            "match_type": "semantic",
            "embedding_model": "text-embedding-test",
            "embedding_version": "embedding.v1",
        }
    )
    semantic_metadata = {
        "vector_text_derivation_version": "vector-text.v1",
        "embedding_model": "text-embedding-test",
        "embedding_version": "embedding.v1",
        "semantic_algorithm_version": "semantic-matching.v1",
    }
    evaluation = evaluation.model_copy(
        update={
            "responsibility_results": (responsibility,),
            "vector_profile_version": evaluation.cv_profile_version,
            "threshold_config_version": "semantic-threshold.v1",
            **semantic_metadata,
            "final_match_result": evaluation.final_match_result.model_copy(
                update={
                    **semantic_metadata,
                    "semantic_threshold_config_version": "semantic-threshold.v1",
                }
            ),
        }
    )

    result = LearningPathService().generate(
        {"evaluation": evaluation.model_dump(mode="json")}
    )

    assert evaluation.cv_profile_id != evaluation.cv_profile_version
    assert result.generation_status == "completed"
    assert result.error_code is None


def test_learning_path_api_accepts_profiles_and_preserves_versions(
    ready_cv_json, ready_position_json
):
    response = client.post(
        "/api/v1/learning-paths",
        json={"cv_profile": ready_cv_json, "position_profile": ready_position_json},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation_status"] == "completed"
    assert data["algorithm_version"] == "deterministic-gap-path.v3"
    assert data["config_version"] == "gap-analysis-config.v3"
    assert data["source_scoring_algorithm_version"] == "explainable-scoring.v4"
    assert data["profile_references"]["cv_profile_version"] == ready_cv_json[
        "profile_version"
    ]
    assert data["profile_references"]["position_profile_version"] == (
        ready_position_json["profile_version"]
    )
    assert data["gap_policy_version"] == "gap-priority.v3"
    assert data["gap_policy_hash"]


def test_gap_policy_hash_is_deterministic_and_sensitive_to_policy_changes():
    base = GapAnalysisConfig()
    assert gap_policy_hash(base) == gap_policy_hash(GapAnalysisConfig())
    assert len(gap_policy_hash(base)) == 64

    changed_weight = GapAnalysisConfig(required_factor_weight=0.30, severity_weight=0.20)
    assert gap_policy_hash(changed_weight) != gap_policy_hash(base)

    changed_threshold = GapAnalysisConfig(critical_threshold=80.0)
    assert gap_policy_hash(changed_threshold) != gap_policy_hash(base)

    changed_low_dimension = GapAnalysisConfig(low_dimension_boost=0.2)
    assert gap_policy_hash(changed_low_dimension) != gap_policy_hash(base)

    changed_version = GapAnalysisConfig(gap_policy_version="gap-priority.v4")
    assert gap_policy_hash(changed_version) != gap_policy_hash(base)


def test_two_hop_skill_path_is_evidence_bound_and_remains_partial(
    ready_cv_json, ready_position_json
):
    position = _target_position(ready_position_json, "skill_target")
    relations = (
        _relation("relation-1", "skill_python", "skill_middle"),
        _relation("relation-2", "skill_middle", "skill_target"),
    )
    evaluation_service = MatchEvaluationService(
        relation_source=InMemorySkillRelationSource(relations)
    )

    result = LearningPathService(evaluation_service).generate(
        {"cv_profile": ready_cv_json, "position_profile": position}
    )

    decision = next(
        item for item in result.skill_path_decisions if item.target_skill_id == "skill_target"
    )
    path = decision.paths[0]
    assert decision.status == "reachable"
    assert path.node_skill_ids == ("skill_python", "skill_middle", "skill_target")
    assert path.hop_count == 2
    assert path.outcome_status == "partial"
    assert path.effective_confidence < path.minimum_confidence
    assert all(edge.graph_version == "graph-fixture-v1" for edge in path.edges)
    assert all(edge.evidence_refs for edge in path.edges)
    transfer = next(
        (
            item
            for item in result.candidate_actions
            if item.action_type == "controlled_skill_transfer"
        ),
        None,
    )
    if transfer is not None:
        assert transfer.path_refs == (path.path_id,)
    else:
        assert result.minimal_action_set is not None
        assert result.minimal_action_set.status in {
            "reached",
            "no_positive_actions",
            "unreachable",
        }


def test_related_relation_does_not_create_a_scored_transfer_path(
    ready_cv_json, ready_position_json
):
    position = _target_position(ready_position_json, "skill_target")
    relations = (
        _relation(
            "relation-related",
            "skill_python",
            "skill_target",
            relation_type="related",
        ),
    )
    result = LearningPathService(
        MatchEvaluationService(relation_source=InMemorySkillRelationSource(relations))
    ).generate({"cv_profile": ready_cv_json, "position_profile": position})

    decision = next(
        item for item in result.skill_path_decisions if item.target_skill_id == "skill_target"
    )
    assert decision.status == "unreachable"
    assert not any(
        item.action_type == "controlled_skill_transfer"
        for item in result.candidate_actions
    )


def test_parent_child_relation_is_not_traversed_in_reverse(
    ready_cv_json, ready_position_json
):
    position = _target_position(ready_position_json, "skill_target")
    relations = (
        _relation(
            "relation-parent",
            "skill_target",
            "skill_python",
            relation_type="parent_child",
        ),
    )
    result = LearningPathService(
        MatchEvaluationService(relation_source=InMemorySkillRelationSource(relations))
    ).generate({"cv_profile": ready_cv_json, "position_profile": position})

    decision = next(
        item for item in result.skill_path_decisions if item.target_skill_id == "skill_target"
    )
    assert decision.status == "unreachable"


def test_mixed_graph_versions_are_explicitly_unreachable(
    ready_cv_json, ready_position_json
):
    position = _target_position(ready_position_json, "skill_target")
    relations = (
        _relation("relation-1", "skill_python", "skill_middle"),
        _relation(
            "relation-2",
            "skill_middle",
            "skill_target",
            graph_version="graph-fixture-v2",
        ),
    )
    service = LearningPathService(
        MatchEvaluationService(relation_source=InMemorySkillRelationSource(relations))
    )

    result = service.generate(
        {"cv_profile": ready_cv_json, "position_profile": position}
    )

    decision = next(
        item for item in result.skill_path_decisions if item.target_skill_id == "skill_target"
    )
    assert decision.status == "unreachable"
    assert "GRAPH_VERSION_MISMATCH" in decision.reason_codes


def test_external_prerequisite_is_reported_missing(
    ready_cv_json, ready_position_json
):
    position = _target_position(ready_position_json, "skill_target")
    relation = SkillRelation.model_validate(
        {
            **_relation(
                "relation-prerequisite", "skill_external", "skill_target"
            ).model_dump(mode="python"),
            "relation_type": "prerequisite",
        }
    )
    service = LearningPathService(
        MatchEvaluationService(
            relation_source=InMemorySkillRelationSource((relation,))
        )
    )

    result = service.generate(
        {"cv_profile": ready_cv_json, "position_profile": position}
    )

    step = next(
        (
            item
            for item in result.learning_path
            if item.target_skill_id == "skill_target"
        ),
        None,
    )
    assert result.minimal_action_set is not None
    assert result.minimal_action_set.status in {
        "no_positive_actions",
        "unreachable",
    }
    if step is not None:
        assert step.prerequisite_states[0].skill_id == "skill_external"
        assert step.prerequisite_states[0].status == "missing"
        assert step.planning_status == "blocked"
        assert "PREREQUISITE_MISSING" in step.blocked_reason_codes


def test_prerequisite_cycle_is_blocked_instead_of_silently_ordered(
    ready_cv_json, ready_position_json
):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    base = _required(evaluation)
    first = _skill_result(
        base,
        "required:skill_cycle_a",
        "skill_cycle_a",
        match_status="missing",
        match_type="prerequisite",
        relation_type="prerequisite",
        prerequisite_skill_ids=("skill_cycle_b",),
        reason_code="PREREQUISITE_ONLY",
    )
    second = _skill_result(
        base,
        "required:skill_cycle_b",
        "skill_cycle_b",
        match_status="missing",
        match_type="prerequisite",
        relation_type="prerequisite",
        prerequisite_skill_ids=("skill_cycle_a",),
        reason_code="PREREQUISITE_ONLY",
    )

    result = build_gap_analysis(
        evaluation.model_copy(update={"skill_results": (first, second)})
    )

    cycle_steps = tuple(
        item
        for item in result.learning_path
        if item.target_skill_id in {"skill_cycle_a", "skill_cycle_b"}
    )
    assert len(cycle_steps) == 2
    assert all(item.planning_status == "blocked" for item in cycle_steps)
    assert all(
        "PREREQUISITE_CYCLE" in item.blocked_reason_codes
        for item in cycle_steps
    )
