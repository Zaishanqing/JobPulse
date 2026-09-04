"""Learning Catalog: deterministic assets, fallback and planning identity."""

from __future__ import annotations

from app.application.evaluation import MatchEvaluationService
from app.application.learning_catalog import lookup_learning_asset, responsibility_action_template
from app.application.learning_paths import LearningPathService
from app.application.route_planning import LearningRoutePlanner
from app.application.skill_paths import ControlledSkillPathPlanner
from app.application.what_if import WhatIfService
from app.domain.gap_analysis import GapAnalysisConfig, build_gap_analysis
from app.domain.gaps import PrioritizedGap
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.what_if import WhatIfAction


def _evaluation(cv_payload: dict, position_payload: dict):
    return MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def _catalog_position(ready_position_json: dict) -> dict:
    position = {**ready_position_json}
    position["core_responsibilities"] = []
    position["responsibility_requirements"] = []
    position["preferred_skills"] = []
    position["required_skills"] = [
        {
            "skill_id": "skill_pytorch",
            "canonical_name": "PyTorch",
            "required_level": "working",
            "importance": 1.0,
            "resolution_status": "resolved",
            "evidence_refs": [
                {
                    "source_id": "jd:skill:1",
                    "quote": "PyTorch",
                    "start": 0,
                    "end": 7,
                    "alignment": "exact",
                    "occurrence_index": 0,
                }
            ],
        },
        {
            "skill_id": "skill_unknown",
            "canonical_name": "UnknownTool",
            "required_level": "working",
            "importance": 0.8,
            "resolution_status": "resolved",
            "evidence_refs": [
                {
                    "source_id": "jd:skill:2",
                    "quote": "UnknownTool",
                    "start": 0,
                    "end": 11,
                    "alignment": "exact",
                    "occurrence_index": 0,
                }
            ],
        },
    ]
    return position


def test_catalog_lookup_by_skill_id_and_stage_is_deterministic():
    first = lookup_learning_asset(
        skill_id="skill_python",
        stage="project",
    )
    second = lookup_learning_asset(
        skill_id="Skill_Python ",
        stage="project",
    )
    assert first is not None
    assert first.title == "Python 工程实践项目"
    assert first is second
    assert lookup_learning_asset(
        skill_id="skill_python",
        stage="foundation",
    ).title == "Python 基础语法与核心编程"
    assert lookup_learning_asset(
        skill_id="skill_python",
        stage="ownership",
    ).title == "独立负责 Python 服务模块开发"
    assert lookup_learning_asset(skill_id="skill_python", stage="transfer") is None


def test_catalog_lookup_falls_back_to_canonical_name():
    asset = lookup_learning_asset(
        skill_id="standard-position:skill:770c5e11-8e31-49e8-a7a5-5ce17427c00d",
        canonical_name="PyTorch",
        stage="project",
    )
    assert asset is not None
    assert asset.title == "PyTorch 模型训练与调优实践"
    assert "训练与调优" in asset.deliverable
    assert asset.acceptance_criteria
    assert asset.resource_keywords


def test_catalog_covers_c_language_and_vlm_stages():
    assert lookup_learning_asset(
        skill_id="skill_c",
        stage="foundation",
    ).title == "C 语言基础语法与内存模型"
    assert lookup_learning_asset(
        skill_id="skill_c",
        stage="project",
    ).title == "C 语言系统实践"
    assert lookup_learning_asset(
        skill_id="skill_vlm",
        stage="foundation",
    ).title == "视觉语言模型基础与多模态理解"
    vlm_project = lookup_learning_asset(
        skill_id="skill_vlm",
        stage="project",
    )
    assert vlm_project is not None
    assert vlm_project.title == "视觉语言模型应用实践"
    assert "数据管线" in vlm_project.deliverable
    assert vlm_project.acceptance_criteria
    assert lookup_learning_asset(
        skill_id="visual-language-model",
        canonical_name="VLM",
        stage="ownership",
    ).title == "独立负责视觉语言模型工程"


def test_catalog_target_level_restriction_is_optional_and_deterministic():
    assert lookup_learning_asset(
        skill_id="skill_python",
        stage="foundation",
        target_level="proficient",
    ) is None
    assert lookup_learning_asset(
        skill_id="skill_python",
        stage="foundation",
        target_level="basic",
    ) is not None
    assert lookup_learning_asset(
        skill_id="skill_python",
        stage="foundation",
        target_level=None,
    ) is not None


def test_catalog_miss_keeps_planner_fallback():
    assert lookup_learning_asset(skill_id="skill_unknown", stage="project") is None
    assert lookup_learning_asset(
        skill_id="unknown-uuid",
        canonical_name="UnknownTool",
        stage="proficiency",
    ) is None


def test_responsibility_templates_moved_to_catalog_keep_behavior():
    template = responsibility_action_template(
        "负责 Agentic AI 的服务部署、性能测试和优化"
    )
    assert template.name == "Agentic AI 服务部署与性能工程"
    assert "压测脚本" in template.deliverable
    assert any("QPS" in item for item in template.acceptance_criteria)
    demand_template = responsibility_action_template(
        "负责挖掘发现公司潜在业务需求，评估可行性并输出验证记录"
    )
    assert demand_template.name == "业务需求挖掘与机会验证"
    assert "可行性结论" in demand_template.deliverable


def test_catalog_enriches_configured_steps_and_falls_back_for_unknown(
    ready_cv_json, ready_position_json
):
    position = _catalog_position(ready_position_json)
    evaluation = _evaluation(ready_cv_json, position)
    result = LearningPathService().generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": ready_cv_json,
            "position_profile": position,
            "time_budget_hours": 40,
        }
    )

    assert result.generation_status == "completed"
    pytorch_project = next(
        item
        for item in result.candidate_actions
        if item.skill_id == "skill_pytorch" and item.stage == "project"
    )
    assert pytorch_project.learning_title == "PyTorch 模型训练与调优实践"
    assert pytorch_project.canonical_name is None
    assert "模型训练与调优实践" in (pytorch_project.deliverable or "")
    assert pytorch_project.acceptance_criteria
    pytorch_foundation = next(
        item
        for item in result.candidate_actions
        if item.skill_id == "skill_pytorch" and item.stage == "foundation"
    )
    assert pytorch_foundation.learning_title == "PyTorch 基础张量运算与自动求导"
    unknown_foundation = next(
        item
        for item in result.candidate_actions
        if item.skill_id == "skill_unknown" and item.stage == "foundation"
    )
    assert unknown_foundation.learning_title is None
    assert unknown_foundation.canonical_name is None
    assert "基础学习" in (unknown_foundation.deliverable or "")

    pytorch_steps = [
        item
        for item in result.learning_path
        if item.target_skill_id == "skill_pytorch"
    ]
    assert all(item.source_action_id for item in result.learning_path)
    candidate_ids = {item.action_id for item in result.candidate_actions}
    assert all(
        item.source_action_id in candidate_ids for item in result.learning_path
    )
    assert any("张量运算" in item.objective for item in pytorch_steps)
    assert any("训练管线" in item.objective for item in pytorch_steps)
    assert all(item.completion_criteria for item in pytorch_steps)
    unknown_steps = [
        item
        for item in result.learning_path
        if item.target_skill_id == "skill_unknown"
    ]
    assert any("基础学习" in item.objective for item in unknown_steps)
    assert any("skill_unknown" in item.target_skill_id for item in unknown_steps)


def test_catalog_enrichment_preserves_planning_identity(
    ready_cv_json, ready_position_json
):
    position = _catalog_position(ready_position_json)
    evaluation = _evaluation(ready_cv_json, position)

    service = LearningPathService()
    result = service.generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": ready_cv_json,
            "position_profile": position,
            "time_budget_hours": 40,
        }
    )

    cv = CVMatchProfile.model_validate(ready_cv_json)
    position_model = PositionMatchProfile.model_validate(position)
    analysis = build_gap_analysis(evaluation, include_learning_steps=False)
    assert result.prioritized_gaps == analysis.prioritized_gaps
    skill_paths = ControlledSkillPathPlanner().plan(
        cv,
        analysis.prioritized_gaps,
        lambda skill_ids: None,
        graph_enabled=position_model.graph_mode != "disabled",
        expected_graph_version=position_model.graph_version,
    )
    planner = LearningRoutePlanner(WhatIfService(MatchEvaluationService()))
    actions, routes, minimal = planner.plan(
        cv,
        position_model,
        evaluation,
        analysis,
        time_budget_hours=40,
        target_type="standard_position",
        use_enterprise_weights=False,
        skill_path_decisions=skill_paths,
    )

    assert [item.action_id for item in result.candidate_actions] == [
        item.action_id for item in actions
    ]
    assert [
        (item.estimated_hours, item.estimated_score_delta)
        for item in result.candidate_actions
    ] == [
        (item.estimated_hours, item.estimated_score_delta)
        for item in actions
    ]
    assert [
        (item.route_type, item.action_ids, item.total_cost_hours, item.modeled_score_delta)
        for item in result.learning_routes
    ] == [
        (item.route_type, item.action_ids, item.total_cost_hours, item.modeled_score_delta)
        for item in routes
    ]
    assert result.minimal_action_set == minimal


def test_route_carries_projected_dimension_scores(ready_cv_json, ready_position_json):
    position = _catalog_position(ready_position_json)
    evaluation = _evaluation(ready_cv_json, position)
    baseline = {
        item.dimension: item.score
        for item in evaluation.final_match_result.dimension_scores
    }
    result = LearningPathService().generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": ready_cv_json,
            "position_profile": position,
            "time_budget_hours": 40,
        }
    )

    assert result.learning_routes
    for route in result.learning_routes:
        assert route.scenario_dimension_scores
        scenario = {
            item.dimension: item.score
            for item in route.scenario_dimension_scores
        }
        changed = [
            name
            for name, score in baseline.items()
            if scenario.get(name) not in (None, score)
        ]
        assert changed, f"route {route.route_type} must change at least one dimension score"
        assert route.modeled_score_delta is not None and route.modeled_score_delta > 0


def test_transfer_recommendations_keep_only_top_value_targets():
    gaps = (
        PrioritizedGap(
            gap_type="required_skill_missing",
            requirement_id="req-low",
            skill_id="skill_low",
            priority="low",
            priority_score=30,
            reason_codes=("MISSING",),
        ),
        PrioritizedGap(
            gap_type="required_skill_missing",
            requirement_id="req-high",
            skill_id="skill_high",
            priority="high",
            priority_score=90,
            reason_codes=("MISSING",),
        ),
        PrioritizedGap(
            gap_type="required_skill_missing",
            requirement_id="req-mid",
            skill_id="skill_mid",
            priority="medium",
            priority_score=60,
            reason_codes=("MISSING",),
        ),
    )

    def transfer(action_id: str, requirement_id: str, delta: float) -> WhatIfAction:
        return WhatIfAction(
            action_id=action_id,
            action_type="controlled_skill_transfer",
            skill_id=f"skill-{requirement_id}",
            source_skill_id="skill_source",
            target_requirement_ids=(requirement_id,),
            path_refs=("path-1",),
            graph_version="graph-42",
            estimated_hours=10.0,
            stage="transfer",
            cost_model="cost-band.v1",
            estimated_score_delta=delta,
        )

    actions = (
        transfer("transfer-low", "req-low", 2.0),
        transfer("transfer-mid", "req-mid", 20.0),
        transfer("transfer-high", "req-high", 1.0),
    )
    kept = LearningRoutePlanner._prefer_high_value_transfers(actions, gaps)
    assert {item.action_id for item in kept} == {
        "transfer-high",
        "transfer-mid",
    }
    assert len(kept) == 2

    with_other = (
        WhatIfAction(
            action_id="learn-basic",
            action_type="add_skill",
            skill_id="skill_high",
            estimated_hours=8.0,
            stage="foundation",
            cost_model="cost-band.v1",
        ),
        *actions,
    )
    kept_with_other = LearningRoutePlanner._prefer_high_value_transfers(
        with_other, gaps
    )
    assert {item.action_id for item in kept_with_other} == {
        "learn-basic",
        "transfer-high",
        "transfer-mid",
    }


def test_bonus_skill_gap_generates_learning_actions(ready_cv_json, ready_position_json):
    position = {**ready_position_json}
    position["core_responsibilities"] = []
    position["responsibility_requirements"] = []
    position["required_skills"] = [
        {
            "skill_id": "skill_pytorch",
            "canonical_name": "PyTorch",
            "required_level": "working",
            "importance": 1.0,
            "resolution_status": "resolved",
            "evidence_refs": [
                {
                    "source_id": "jd:skill:1",
                    "quote": "PyTorch",
                    "start": 0,
                    "end": 7,
                    "alignment": "exact",
                    "occurrence_index": 0,
                }
            ],
        }
    ]
    position["preferred_skills"] = [
        {
            "skill_id": "skill_go",
            "canonical_name": "Go",
            "required_level": "working",
            "importance": 0.5,
            "resolution_status": "resolved",
            "evidence_refs": [
                {
                    "source_id": "jd:skill:2",
                    "quote": "Go",
                    "start": 0,
                    "end": 2,
                    "alignment": "exact",
                    "occurrence_index": 0,
                }
            ],
        }
    ]
    evaluation = _evaluation(ready_cv_json, position)
    result = LearningPathService().generate(
        {
            "evaluation": evaluation.model_dump(mode="python"),
            "cv_profile": ready_cv_json,
            "position_profile": position,
            "time_budget_hours": 40,
        }
    )

    required_gap = next(
        item
        for item in result.prioritized_gaps
        if item.skill_id == "skill_pytorch"
    )
    bonus_gap = next(
        item
        for item in result.prioritized_gaps
        if item.skill_id == "skill_go"
    )
    assert bonus_gap.gap_type == "bonus_skill_missing"
    assert required_gap.priority_score > bonus_gap.priority_score

    bonus_actions = [
        item
        for item in result.candidate_actions
        if item.skill_id == "skill_go"
    ]
    assert bonus_actions
    assert len(bonus_actions) <= 2
    assert any(item.stage == "foundation" for item in bonus_actions)
    assert any(
        "learn-basic-bonus-skill_go" in route.action_ids
        for route in result.learning_routes
    )
    coverage = LearningPathService.catalog_coverage(
        result.candidate_actions, evaluation
    )
    assert coverage["total_actions"] > 0
    assert coverage["catalog_hits"] + coverage["fallback_count"] == coverage["total_actions"]
    assert 0 < coverage["hit_rate"] <= 1


def test_bonus_gaps_are_pre_selected_by_importance():
    gaps = tuple(
        PrioritizedGap(
            gap_type="bonus_skill_missing",
            requirement_id=f"bonus-{index}",
            skill_id=f"skill-{index}",
            priority="low",
            priority_score=score,
            reason_codes=("MISSING",),
        )
        for index, score in enumerate((10, 90, 40, 70, 20, 60))
    )
    planning = LearningRoutePlanner._select_planning_gaps(gaps)
    bonus_ids = [
        item.requirement_id
        for item in planning
        if item.gap_type == "bonus_skill_missing"
    ]
    assert bonus_ids == ["bonus-1", "bonus-3", "bonus-5"]

    with_required = (
        PrioritizedGap(
            gap_type="required_skill_missing",
            requirement_id="req-core",
            skill_id="skill_core",
            priority="critical",
            priority_score=95,
            reason_codes=("MISSING",),
        ),
        *gaps,
    )
    planning_with_required = LearningRoutePlanner._select_planning_gaps(
        with_required
    )
    assert any(
        item.requirement_id == "req-core" for item in planning_with_required
    )


def test_low_dimension_boost_raises_gap_priority(ready_cv_json, ready_position_json):
    evaluation = _evaluation(ready_cv_json, ready_position_json)
    final = evaluation.final_match_result
    low_dimensions = {
        item.dimension
        for item in final.dimension_scores
        if item.score is not None
        and item.effective_weight > 0
        and item.score < GapAnalysisConfig().low_dimension_threshold
    }
    assert low_dimensions

    boosted = build_gap_analysis(
        evaluation,
        GapAnalysisConfig(low_dimension_boost=0.2),
    )
    baseline = build_gap_analysis(
        evaluation,
        GapAnalysisConfig(low_dimension_boost=0.0),
    )
    boosted_by_id = {
        item.requirement_id: item for item in boosted.prioritized_gaps
    }
    baseline_by_id = {
        item.requirement_id: item for item in baseline.prioritized_gaps
    }
    affected = [
        requirement_id
        for requirement_id, item in boosted_by_id.items()
        if requirement_id in baseline_by_id
        and item.priority_score > baseline_by_id[requirement_id].priority_score
    ]
    assert affected
    for requirement_id in affected:
        assert (
            boosted_by_id[requirement_id].priority_score
            <= baseline_by_id[requirement_id].priority_score + 20
        )
