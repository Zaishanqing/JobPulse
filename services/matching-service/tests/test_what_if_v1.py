from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.evaluation import MatchEvaluationService
from app.application.learning_paths import LearningPathService
from app.application.route_planning import LearningRoutePlanner
from app.application.what_if import WhatIfService
from app.domain.gap_analysis import GapAnalysisConfig, build_gap_analysis
from app.domain.gaps import PrioritizedGap
from app.domain.profiles import CVMatchProfile, Evidence, PositionMatchProfile


def test_add_skill_recomputes_formal_score_without_mutating_profile(
    cv_payload: dict, position_payload: dict
) -> None:
    original = deepcopy(cv_payload)
    service = WhatIfService(MatchEvaluationService())

    first = service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "canonical_name": "SQL",
                    "target_level": "working",
                    "estimated_hours": 8,
                    "milestone_status": "verified",
                }
            ],
        }
    )
    second = service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "canonical_name": "SQL",
                    "target_level": "working",
                    "estimated_hours": 8,
                    "milestone_status": "verified",
                }
            ],
        }
    )

    assert first.generation_status == "completed"
    assert first.scenario_id == second.scenario_id
    assert first.score_delta == second.score_delta
    assert first.scenario_score is not None
    assert first.baseline_score is not None
    assert first.scenario_score > first.baseline_score
    assert cv_payload == original
    assert first.scenario_evaluation is not None
    assert first.scenario_evaluation.cv_profile_version.endswith(first.scenario_id)


def test_pure_ownership_action_is_scored_in_v2(
    cv_payload: dict, position_payload: dict
) -> None:
    position_payload["required_skills"][0]["evidence_refs"][0]["quote"] = (
        "Owned Python core module"
    )
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "own-python-component",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_python",
                    "ownership": "owned",
                    "estimated_hours": 2,
                }
            ],
        }
    )

    assert result.generation_status == "completed"
    assert result.score_effect_status == "modeled"
    assert result.score_delta is not None and result.score_delta > 0
    capability_delta = next(
        item for item in result.dimension_deltas if item.dimension == "capability_level"
    )
    assert capability_delta.delta is not None and capability_delta.delta > 0


def test_ownership_gap_is_classified_and_modeled_in_v2(
    cv_payload: dict, position_payload: dict
) -> None:
    position_payload["required_skills"][0]["evidence_refs"][0]["quote"] = (
        "Owned Python core module"
    )
    evaluation = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )

    analysis = build_gap_analysis(evaluation, GapAnalysisConfig())
    ownership_gaps = [
        item for item in analysis.prioritized_gaps if item.gap_type == "ownership_gap"
    ]

    assert ownership_gaps
    assert ownership_gaps[0].current_ownership == "used"
    assert ownership_gaps[0].target_ownership == "owned"
    assert ownership_gaps[0].score_effect_status == "modeled"


def test_requirement_graph_evaluates_nested_groups_without_hard_gate(
    cv_payload: dict, position_payload: dict
) -> None:
    position_payload["hard_conditions"] = []
    position_payload["required_skills"][0]["requirement_id"] = "req-python"
    position_payload["required_skills"][1]["requirement_id"] = "req-sql"
    position_payload["required_skills"][0]["required_level"] = None
    graph_evidence = position_payload["required_skills"][0]["evidence_refs"][0]
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-alternatives",
                "group_type": "or",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-python"},
                    {"node_type": "requirement_ref", "ref_id": "req-sql"},
                ],
                "evidence": graph_evidence,
                "confidence": 0.9,
            },
            {
                "requirement_group_id": "group-root",
                "group_type": "and",
                "priority": "required",
                "children": [
                    {"node_type": "group_ref", "ref_id": "group-alternatives"},
                    {"node_type": "requirement_ref", "ref_id": "req-sql"},
                ],
                "evidence": graph_evidence,
                "confidence": 0.9,
            },
            {
                "requirement_group_id": "group-one-of",
                "group_type": "one_of",
                "priority": "preferred",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-python"},
                    {"node_type": "requirement_ref", "ref_id": "req-sql"},
                ],
                "evidence": graph_evidence,
                "confidence": 0.9,
            },
            {
                "requirement_group_id": "group-min-count",
                "group_type": "min_count",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "req-python"},
                    {"node_type": "requirement_ref", "ref_id": "req-sql"},
                ],
                "min_count": 2,
                "evidence": graph_evidence,
                "confidence": 0.9,
            },
        ],
        "unresolved_items": [],
    }

    evaluation = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )

    groups = {item.group_id: item for item in evaluation.requirement_group_results}
    assert groups["group-alternatives"].status == "satisfied"
    assert groups["group-alternatives"].is_root is False
    assert groups["group-root"].status == "partial"
    assert groups["group-root"].is_root is True
    assert groups["group-one-of"].status == "satisfied"
    assert groups["group-min-count"].status == "partial"
    assert evaluation.final_match_result is not None
    assert evaluation.final_match_result.hard_gate_status != "failed"
    # These roots contain only required-skill leaves, so they replace those
    # leaves inside required_skills instead of adding a duplicate graph score.
    graph_dimension = next(
        item
        for item in evaluation.final_match_result.dimension_scores
        if item.dimension == "requirement_groups"
    )
    assert graph_dimension.applicable_count == 0
    contributions = evaluation.final_match_result.score_contributions
    assert not any(
        item.result_id in {"req-python", "req-sql"}
        and item.dimension == "required_skills"
        for item in contributions
    )
    assert {
        item.result_id
        for item in contributions
        if item.dimension == "required_skills"
    } >= {"group-root", "group-one-of", "group-min-count"}


def test_graph_covered_atomic_hard_condition_still_fails_hard_gate(
    cv_payload: dict, position_payload: dict
) -> None:
    cv_payload["work_experiences"] = [
        {
            "experience_id": "short-work",
            "kind": "work",
            "role": "developer",
            "responsibilities": ["backend development"],
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "evidence_refs": [],
        }
    ]
    graph_evidence = position_payload["evidence_refs"][0]
    position_payload["requirement_graph"] = {
        "graph_version": "requirement-graph.hard-gate.v1",
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "group-hard-experience",
                "group_type": "must",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": "condition_exp"}
                ],
                "evidence": graph_evidence,
                "confidence": 1.0,
            }
        ],
        "unresolved_items": [],
    }

    evaluation = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )

    experience_result = next(
        item
        for item in evaluation.hard_constraint_results
        if item.requirement_id == "condition_exp"
    )
    assert experience_result.status == "fail"
    assert evaluation.final_match_result is not None
    assert evaluation.final_match_result.hard_gate_status == "failed"


@pytest.mark.parametrize(
    ("actions", "expected_code"),
    (
        (
            [
                {
                    "action_id": "duplicate",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                },
                {
                    "action_id": "duplicate",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_sql",
                },
            ],
            "WHAT_IF_ACTION_ID_DUPLICATE",
        ),
        (
            [
                {
                    "action_id": "dependent",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "requires_action_ids": ["missing"],
                }
            ],
            "WHAT_IF_ACTION_DEPENDENCY_UNKNOWN",
        ),
        (
            [
                {
                    "action_id": "cycle-a",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "requires_action_ids": ["cycle-b"],
                },
                {
                    "action_id": "cycle-b",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_sql",
                    "requires_action_ids": ["cycle-a"],
                },
            ],
            "WHAT_IF_ACTION_DEPENDENCY_CYCLE",
        ),
        (
            [
                {
                    "action_id": "unknown-target",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "target_requirement_ids": ["requirement-does-not-exist"],
                }
            ],
            "WHAT_IF_ACTION_TARGET_UNKNOWN",
        ),
    ),
)
def test_what_if_rejects_invalid_action_graphs(
    actions: list[dict],
    expected_code: str,
    cv_payload: dict,
    position_payload: dict,
) -> None:
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": actions,
        }
    )

    assert result.generation_status == "rejected"
    assert result.error_code == expected_code


def test_what_if_orders_actions_by_dependencies(
    cv_payload: dict, position_payload: dict
) -> None:
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "project-sql",
                    "action_type": "add_project_experience",
                    "skill_id": "skill_sql",
                    "responsibilities": ["deliver SQL project"],
                    "requires_action_ids": ["learn-sql"],
                },
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                },
            ],
        }
    )

    assert result.generation_status == "completed"
    assert tuple(item.action_id for item in result.actions) == (
        "learn-sql",
        "project-sql",
    )


def test_what_if_binds_original_evaluation_and_rejects_drift(
    cv_payload: dict, position_payload: dict
) -> None:
    evaluator = MatchEvaluationService()
    baseline = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    assert baseline.final_match_result is not None
    persisted_baseline = baseline.model_copy(
        update={
            "evaluation_id": "evaluation-original",
            "final_match_result": baseline.final_match_result.model_copy(
                update={"source_evaluation_id": "evaluation-original"}
            ),
        }
    )
    request = {
        "baseline_evaluation": persisted_baseline.model_dump(mode="python"),
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "learn-sql",
                "action_type": "add_skill",
                "skill_id": "skill_sql",
            }
        ],
    }

    completed = WhatIfService(evaluator).evaluate(request)

    assert completed.generation_status == "completed"
    assert completed.baseline_evaluation_id == "evaluation-original"
    assert completed.baseline_evaluation == persisted_baseline
    assert completed.scoring_config_version == "scoring-config.v3"
    assert completed.position_graph_version == position_payload["graph_version"]

    position_payload["graph_version"] = "graph-drifted-without-profile-version"
    rejected = WhatIfService(evaluator).evaluate(request)
    assert rejected.generation_status == "rejected"
    assert rejected.error_code == "WHAT_IF_BASELINE_MISMATCH"


def test_scenario_identity_includes_graph_and_scoring_policy(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "learn-sql",
                "action_type": "add_skill",
                "skill_id": "skill_sql",
            }
        ],
    }
    standard = service.evaluate(request)
    position_payload["graph_version"] = "graph-policy-v2"
    graph_changed = service.evaluate(request)
    position_payload["graph_version"] = "graph-42"
    enterprise = service.evaluate(
        {
            **request,
            "target_type": "enterprise_job",
            "use_enterprise_weights": True,
        }
    )

    assert standard.generation_status == "completed"
    assert graph_changed.generation_status == "completed"
    assert enterprise.generation_status == "completed"
    assert len(
        {standard.scenario_id, graph_changed.scenario_id, enterprise.scenario_id}
    ) == 3


def test_what_if_empty_actions_return_the_baseline(
    cv_payload: dict, position_payload: dict
) -> None:
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload, "actions": []}
    )

    assert result.generation_status == "completed"
    assert result.actions == ()
    assert result.scenario_score == result.baseline_score
    assert result.score_delta == 0
    assert result.scenario_hard_gate_status == result.baseline_hard_gate_status
    assert all(item.delta in {0, None} for item in result.dimension_deltas)


def test_what_if_does_not_call_optional_semantic_services(
    cv_payload: dict, position_payload: dict
) -> None:
    class FailingSemanticCandidates:
        mode = "enabled"

        def apply(self, **_: object) -> object:
            raise AssertionError("What-if must stay on the frozen formal scorer")

    evaluator = MatchEvaluationService(
        semantic_candidates=FailingSemanticCandidates()  # type: ignore[arg-type]
    )
    result = WhatIfService(evaluator).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "evidence-python",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_python",
                    "target_level": "proficient",
                }
            ],
        }
    )

    assert result.generation_status == "completed"


def test_learning_path_builds_distinct_cumulative_routes(
    cv_payload: dict, position_payload: dict
) -> None:
    evaluation_service = MatchEvaluationService()
    evaluation = evaluation_service.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )

    result = LearningPathService(evaluation_service).generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "time_budget_hours": 16,
        }
    )

    assert result.generation_status == "completed"
    assert result.candidate_actions
    assert result.learning_routes or (
        result.minimal_action_set is not None
        and result.minimal_action_set.status
        in {"no_positive_actions", "unreachable", "budget_excluded"}
    )
    assert result.minimal_action_set is not None
    assert all(
        action.cost_model == "cost-band.v1"
        for action in result.candidate_actions
    )
    assert any(action.estimated_score_delta is not None for action in result.candidate_actions)
    action_sets = {route.action_ids for route in result.learning_routes}
    assert len(action_sets) == len(result.learning_routes)
    assert all(
        route.total_cost_hours <= 16
        for route in result.learning_routes
        if route.route_type == "budget_max_gain"
    )

    # B-PATH-FE-PROVENANCE: routes carry real per-action cost provenance and
    # learning steps expose the concrete planning cost model (no hardcoding).
    for route in result.learning_routes:
        assert route.action_costs
        for cost in route.action_costs:
            assert cost.action_id in route.action_ids
            assert cost.cost_model == "cost-band.v1"
            assert cost.cost_source_type == "heuristic"
            assert cost.cost_source_ref == cost.cost_model
            assert cost.estimate_status == "estimated"
        for step in result.learning_path:
            assert step.cost_model == "cost-band.v1"
        assert step.cost_source_type == "heuristic"
        assert step.estimate_status == "estimated"


def test_cf08_experience_hard_gate_is_short_horizon_blocked(
    cv_payload: dict, position_payload: dict
) -> None:
    cv_payload["work_experiences"] = [
        {
            "experience_id": "short-work",
            "kind": "work",
            "role": "developer",
            "responsibilities": ["backend development"],
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "evidence_refs": [],
        }
    ]
    position_payload["required_skills"] = []
    position_payload["preferred_skills"] = []
    position_payload["core_responsibilities"] = []
    position_payload["tools"] = {"values": [], "evidence_refs": []}
    position_payload["industries"] = {"values": [], "evidence_refs": []}
    position_payload["business_scenarios"] = {"values": [], "evidence_refs": []}
    evaluator = MatchEvaluationService()
    evaluation = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    assert evaluation.final_match_result is not None
    assert evaluation.final_match_result.hard_gate_status == "failed"

    analysis = LearningPathService(evaluator).generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": cv_payload,
            "position_profile": position_payload,
        }
    )

    hard_actions = [
        item
        for item in analysis.candidate_actions
        if item.action_type == "satisfy_hard_condition"
    ]
    assert hard_actions == []
    assert analysis.minimal_action_set is not None
    assert analysis.minimal_action_set.status == "hard_blocked"
    assert analysis.minimal_action_set.selected_action_ids == ()
    assert analysis.minimal_action_set.hard_gate_delta is None
    assert analysis.minimal_action_set.unreachable_reason_codes == (
        "IMMUTABLE_HARD_CONDITION:experience:condition_exp",
    )


def test_cf08_finds_exact_minimum_cardinality_and_reports_budget(
    cv_payload: dict, position_payload: dict
) -> None:
    cv_payload["skills"] = []
    cv_payload["match_features"] = []
    cv_payload["capability_profiles"] = []
    cv_payload["capability_evidence_links"] = []
    position_payload["hard_conditions"] = []
    position_payload["core_responsibilities"] = []
    position_payload["tools"] = {"values": [], "evidence_refs": []}
    position_payload["industries"] = {"values": [], "evidence_refs": []}
    position_payload["business_scenarios"] = {"values": [], "evidence_refs": []}
    position_payload["required_skills"][0]["requirement_id"] = "req-python"
    position_payload["required_skills"][1]["requirement_id"] = "req-sql"
    for requirement in position_payload["required_skills"]:
        requirement["required_level"] = "basic"
    evaluator = MatchEvaluationService()
    evaluation = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    request = {
        "evaluation": evaluation.model_dump(mode="python"),
        "cv_profile": cv_payload,
        "position_profile": position_payload,
    }

    reachable = LearningPathService(evaluator).generate(request)
    constrained = LearningPathService(evaluator).generate(
        {**request, "time_budget_hours": 3}
    )

    assert reachable.minimal_action_set is not None
    assert reachable.minimal_action_set.status == "unreachable"
    assert reachable.minimal_action_set.target_reachable is False
    assert constrained.minimal_action_set is not None
    assert constrained.minimal_action_set.status in {
        "unreachable",
        "budget_excluded",
    }
    assert "BUDGET_EXCLUDES_CANDIDATES" in (
        constrained.minimal_action_set.unreachable_reason_codes
    )
    assert constrained.minimal_action_set.budget_hours == 3
    assert constrained.minimal_action_set.budget_remaining_hours == 3


def test_scenario_identity_ignores_derived_action_estimates(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    action = {
        "action_id": "learn-sql",
        "action_type": "add_skill",
        "skill_id": "skill_sql",
        "estimated_hours": 4,
    }
    plain = service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [action],
        }
    )
    annotated = service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    **action,
                    "estimated_score_delta": 10,
                    "estimated_utility": 2.5,
                    "score_effect_reason": "derived-only",
                }
            ],
        }
    )

    assert plain.generation_status == "completed"
    assert annotated.generation_status == "completed"
    assert plain.scenario_id == annotated.scenario_id


def test_route_selection_preserves_gap_priority_and_accepts_stage_chains(
    cv_payload: dict, position_payload: dict
) -> None:
    evaluator = MatchEvaluationService()
    evaluation = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    analysis = build_gap_analysis(evaluation)
    planner = LearningRoutePlanner(WhatIfService(evaluator))
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)

    groups = planner._action_groups(analysis.prioritized_gaps, position)
    selected = planner._select_actions(groups, 12)
    assert selected
    assert len(selected) <= 12
    from collections import Counter

    buckets = Counter(
        {
            "add_skill": "direct_skill",
            "add_project_experience": "evidence_project",
            "strengthen_evidence": "evidence_project",
            "strengthen_ownership": "ownership",
            "controlled_skill_transfer": "controlled_transfer",
            "satisfy_hard_condition": "hard_gate",
        }[action.action_type]
        for action in selected
    )
    caps = {
        "direct_skill": 3,
        "evidence_project": 2,
        "ownership": 2,
        "controlled_transfer": 2,
        "hard_gate": 1,
    }
    assert all(buckets[bucket] <= caps[bucket] for bucket in buckets)

    by_id = {action.action_id: action for action in selected}
    dependent = next(
        action
        for action in selected
        if action.requires_action_ids
        and set(action.requires_action_ids).issubset(by_id)
    )
    chain = tuple(by_id[action_id] for action_id in dependent.requires_action_ids) + (
        dependent,
    )
    assert planner._prerequisites_closed(chain, analysis.prioritized_gaps, cv)


def test_representative_responsibility_is_the_action_target_not_an_arbitrary_skill(
    cv_payload: dict,
    position_payload: dict,
) -> None:
    responsibility_evidence = position_payload["evidence_refs"][0]
    position_payload["responsibility_requirements"] = [
        {
            "requirement_id": "responsibility:representative:1",
            "text": "负责后端服务开发",
            "skill_ids": ["skill_python"],
            "resolution_status": "resolved",
            "evidence_refs": [responsibility_evidence],
        }
    ]
    position = PositionMatchProfile.model_validate(position_payload)
    gap = PrioritizedGap(
        gap_type="responsibility_gap",
        requirement_id="responsibility:representative:1",
        priority="high",
        priority_score=80,
        reason_codes=("RESPONSIBILITY_GAP",),
        evidence=(Evidence.model_validate(responsibility_evidence),),
        position_evidence_present=True,
    )

    groups = LearningRoutePlanner(WhatIfService(MatchEvaluationService()))._action_groups(
        (gap,), position
    )

    assert len(groups) == 1
    assert len(groups[0]) == 1
    action = groups[0][0]
    assert action.action_type == "add_project_experience"
    assert action.skill_id is None
    assert action.responsibilities == ("负责后端服务开发",)
    assert action.target_requirement_ids == ("responsibility:representative:1",)
    assert action.milestone_status == "planned"
    projected = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position.model_dump(mode="python"),
            "actions": [action.model_dump(mode="python")],
        }
    )
    assert projected.generation_status == "completed"
    assert projected.projected_if_completed is True
    assert (projected.projected_score_delta or 0) > 0


def test_representative_responsibility_without_skill_link_is_not_planned(
    position_payload: dict,
) -> None:
    responsibility_evidence = position_payload["evidence_refs"][0]
    position_payload["responsibility_requirements"] = [
        {
            "requirement_id": "responsibility:generic:1",
            "text": "与团队紧密协作",
            "skill_ids": [],
            "resolution_status": "resolved",
            "evidence_refs": [responsibility_evidence],
        }
    ]
    position = PositionMatchProfile.model_validate(position_payload)
    gap = PrioritizedGap(
        gap_type="responsibility_gap",
        requirement_id="responsibility:generic:1",
        priority="medium",
        priority_score=50,
        reason_codes=("RESPONSIBILITY_GAP",),
        evidence=(Evidence.model_validate(responsibility_evidence),),
        position_evidence_present=True,
    )

    groups = LearningRoutePlanner(WhatIfService(MatchEvaluationService()))._action_groups(
        (gap,), position
    )

    assert groups == ()


def test_learning_path_rejects_profiles_from_another_evaluation(
    cv_payload: dict, position_payload: dict
) -> None:
    evaluation = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    cv_payload["profile_version"] = "another-profile-version"

    result = LearningPathService().generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": cv_payload,
            "position_profile": position_payload,
        }
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "EVALUATION_PROFILE_MISMATCH"


def test_superseded_action_is_not_applied(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    supersede_request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "learn-sql",
                "action_type": "add_skill",
                "skill_id": "skill_sql",
                "canonical_name": "SQL",
                "target_level": "working",
            },
            {
                "action_id": "project-sql",
                "action_type": "add_project_experience",
                "skill_id": "skill_sql",
                "responsibilities": ["deliver SQL project"],
                "supersedes_action_ids": ["learn-sql"],
            },
        ],
    }
    plain_request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "project-sql",
                "action_type": "add_project_experience",
                "skill_id": "skill_sql",
                "responsibilities": ["deliver SQL project"],
            }
        ],
    }

    superseded = service.evaluate(supersede_request)
    plain = service.evaluate(plain_request)

    assert superseded.generation_status == "completed"
    assert plain.generation_status == "completed"
    assert tuple(item.action_id for item in superseded.actions) == ("project-sql",)
    assert superseded.scenario_id == plain.scenario_id
    assert superseded.score_delta == plain.score_delta
    assert superseded.scenario_score == plain.scenario_score


def test_supersede_chain_keeps_only_final_active_action(
    cv_payload: dict, position_payload: dict
) -> None:
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "basic-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "canonical_name": "SQL",
                    "target_level": "basic",
                },
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "canonical_name": "SQL",
                    "target_level": "working",
                    "supersedes_action_ids": ["basic-sql"],
                },
                {
                    "action_id": "project-sql",
                    "action_type": "add_project_experience",
                    "skill_id": "skill_sql",
                    "responsibilities": ["deliver SQL project"],
                    "supersedes_action_ids": ["learn-sql"],
                },
            ],
        }
    )

    assert result.generation_status == "completed"
    assert tuple(item.action_id for item in result.actions) == ("project-sql",)


def test_supersede_cycle_is_rejected(
    cv_payload: dict, position_payload: dict
) -> None:
    result = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "cycle-a",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "supersedes_action_ids": ["cycle-b"],
                },
                {
                    "action_id": "cycle-b",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_sql",
                    "supersedes_action_ids": ["cycle-a"],
                },
            ],
        }
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_ACTION_SUPERSEDE_CYCLE"


def test_required_action_cannot_be_superseded(
    cv_payload: dict, position_payload: dict
) -> None:
    # Direct conflict: the same action requires and supersedes its prerequisite.
    direct = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                },
                {
                    "action_id": "project-sql",
                    "action_type": "add_project_experience",
                    "skill_id": "skill_sql",
                    "responsibilities": ["deliver SQL project"],
                    "requires_action_ids": ["learn-sql"],
                    "supersedes_action_ids": ["learn-sql"],
                },
            ],
        }
    )

    assert direct.generation_status == "rejected"
    assert direct.error_code == "WHAT_IF_ACTION_SUPERSEDE_CONFLICT"

    # Indirect conflict: an active action depends on a superseded action.
    indirect = WhatIfService(MatchEvaluationService()).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                },
                {
                    "action_id": "audit-sql",
                    "action_type": "strengthen_evidence",
                    "skill_id": "skill_sql",
                    "supersedes_action_ids": ["learn-sql"],
                },
                {
                    "action_id": "project-sql",
                    "action_type": "add_project_experience",
                    "skill_id": "skill_sql",
                    "responsibilities": ["deliver SQL project"],
                    "requires_action_ids": ["learn-sql"],
                },
            ],
        }
    )

    assert indirect.generation_status == "rejected"
    assert indirect.error_code == "WHAT_IF_ACTION_DEPENDENCY_SUPERSEDED"


def test_scenario_id_uses_resolved_actions(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    supersede_request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "learn-sql",
                "action_type": "add_skill",
                "skill_id": "skill_sql",
                "canonical_name": "SQL",
                "target_level": "working",
            },
            {
                "action_id": "project-sql",
                "action_type": "add_project_experience",
                "skill_id": "skill_sql",
                "responsibilities": ["deliver SQL project"],
                "supersedes_action_ids": ["learn-sql"],
            },
        ],
    }
    plain_request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "project-sql",
                "action_type": "add_project_experience",
                "skill_id": "skill_sql",
                "responsibilities": ["deliver SQL project"],
            }
        ],
    }

    superseded = service.evaluate(supersede_request)
    plain = service.evaluate(plain_request)
    repeat = service.evaluate(supersede_request)

    assert superseded.generation_status == "completed"
    assert plain.generation_status == "completed"
    assert repeat.generation_status == "completed"
    assert superseded.scenario_id == plain.scenario_id
    assert superseded.scenario_id == repeat.scenario_id


def test_multiple_supersedes_are_allowed_and_deterministic(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": "learn-sql",
                "action_type": "add_skill",
                "skill_id": "skill_sql",
            },
            {
                "action_id": "project-sql",
                "action_type": "add_project_experience",
                "skill_id": "skill_sql",
                "responsibilities": ["deliver SQL project"],
                "supersedes_action_ids": ["learn-sql"],
            },
            {
                "action_id": "assessment-sql",
                "action_type": "strengthen_evidence",
                "skill_id": "skill_sql",
                "supersedes_action_ids": ["learn-sql"],
            },
        ],
    }

    first = service.evaluate(request)
    second = service.evaluate(request)

    assert first.generation_status == "completed"
    assert tuple(item.action_id for item in first.actions) == (
        "assessment-sql",
        "project-sql",
    )
    assert first.scenario_id == second.scenario_id
    assert first.score_delta == second.score_delta


def test_outcome_boundary_marks_whatif_and_routes_as_modeled_counterfactual(
    cv_payload: dict, position_payload: dict
) -> None:
    """B-PATH-OUTCOME-BOUNDARY.

    What-if results and learning routes are modeled counterfactual re-scores,
    never observed real-world learning gains. The primary modeled_* fields must
    be present and stay consistent with the deprecated compatibility aliases.
    """
    evaluator = MatchEvaluationService()
    what_if = WhatIfService(evaluator).evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [
                {
                    "action_id": "learn-sql",
                    "action_type": "add_skill",
                    "skill_id": "skill_sql",
                    "canonical_name": "SQL",
                    "target_level": "working",
                    "estimated_hours": 8,
                }
            ],
        }
    )

    assert what_if.generation_status == "completed"
    assert what_if.outcome_semantics == "modeled_counterfactual"
    assert what_if.observed_outcome is False
    assert what_if.modeled_final_score == what_if.scenario_score
    assert what_if.modeled_score_delta == what_if.score_delta
    assert what_if.modeled_confidence_delta == what_if.confidence_delta

    evaluation = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    plan = LearningPathService(evaluator).generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "time_budget_hours": 16,
        }
    )

    assert plan.generation_status == "completed"
    assert plan.learning_routes or (
        plan.minimal_action_set is not None
        and plan.minimal_action_set.status
        in {"no_positive_actions", "unreachable", "budget_excluded"}
    )
    for route in plan.learning_routes:
        assert route.outcome_semantics == "modeled_counterfactual"
        assert route.observed_outcome is False
        assert route.modeled_final_score == route.final_score
        assert route.modeled_score_delta == route.projected_match_gain
        assert route.modeled_confidence_delta == route.confidence_gain

    assert plan.minimal_action_set is not None
    minimal = plan.minimal_action_set
    assert minimal.outcome_semantics == "modeled_counterfactual"
    assert minimal.observed_outcome is False
    assert minimal.modeled_final_score == minimal.scenario_score
    assert minimal.modeled_score_delta == minimal.score_delta
