"""Targeted tests for the EXP-MATCH diagnostic algorithm fixes.

Covers: adapter uncertainty propagation, education timeline, experience
bounds, JD OR/one-of requirement groups, skill presence/proficiency/ownership
split, responsibility multi-entry candidate text, prospective What-if
evidence, cost bands, CF-10 credit policy and the partial_effective rule.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.application.evaluation import MatchEvaluationService
from app.application.responsibility_ce import responsibility_candidate_text
from app.application.responsibility_policy import ResponsibilityDecisionPolicy
from app.application.what_if import WhatIfService
from app.domain.evaluation import ResponsibilityCandidate, ResponsibilityResult
from app.domain.matching import MatchingAlgorithmConfig, evaluate_skills
from app.domain.profiles import (
    CVMatchProfile,
    Evidence,
    ExperienceFeature,
    PositionMatchProfile,
)
from app.domain.relation_matching import apply_skill_relations
from app.domain.requirement_graph import build_requirement_graph_from_jd
from app.domain.skill_relations import SkillRelation
from app.domain.what_if import (
    CostBand,
    WhatIfAction,
    WhatIfResult,
    apply_actions,
    classify_what_if_outcome,
)

_EVALUATION_ROOT = (
    Path(__file__).resolve().parents[4]
    / "experiments"
    / "services"
    / "matching-service"
    / "evaluation"
)
if str(_EVALUATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVALUATION_ROOT))


def _evidence(source_id: str, quote: str) -> dict:
    return {
        "source_id": source_id,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _refresh(payload: dict) -> dict:
    payload["profile_version"] = "profile-source.v1"
    return payload


def _position_with_skill_quote(
    ready_position_json: dict,
    quote: str,
    skills: list[tuple[str, str, str]],
) -> dict:
    payload = deepcopy(ready_position_json)
    payload["hard_conditions"] = []
    payload["core_responsibilities"] = []
    payload["requirement_graph"] = None
    payload["required_skills"] = [
        {
            "requirement_id": requirement_id,
            "skill_id": skill_id,
            "canonical_name": name,
            "required_level": None,
            "importance": 1.0,
            "resolution_status": "resolved",
            "evidence_refs": [_evidence(f"jd:{skill_id}", quote)],
        }
        for requirement_id, skill_id, name in skills
    ]
    payload["preferred_skills"] = []
    payload["evidence_refs"] = []
    return _refresh(payload)


def test_adapter_uncertainty_is_not_dropped() -> None:
    from final_matching_validation_v1.build_final_profiles import _cv_payload

    raw = {
        "document_id": "test-cv-uncertain",
        "review_status": "corrected_needs_human_review",
        "unresolved_fields": ["project_experience", "email"],
        "skills": [
            {
                "raw_value": "Python",
                "skill_id": "LANG_PYTHON",
                "canonical_name": "Python",
                "resolution_status": "resolved",
                "resolution_source": "canonical_name",
                "normalization_confidence": 0.8,
                "evidence_refs": [_evidence("ocr:cv", "Python")],
            }
        ],
        "work_experience": [],
        "project_experience": [],
        "education": [
            {
                "raw_text": "本科 2022.09 - 2026.06",
                "evidence_refs": [_evidence("ocr:cv", "本科 2022.09 - 2026.06")],
            }
        ],
    }
    pools = {
        "skill": [
            {
                "evidence_id": "test-cv-uncertain:skill:1",
                "quote": "Python",
            }
        ],
        "work_experience": [],
        "project_experience": [],
        "education": [
            {
                "evidence_id": "test-cv-uncertain:education:1",
                "quote": "本科 2022.09 - 2026.06",
            }
        ],
    }
    payload = _cv_payload(raw, pools)
    assert payload["review_status"] == "needs_human_review"
    assert any(item["reason"] == "SOURCE_UNRESOLVED_FIELD" for item in payload["unresolved_items"])
    assert payload["education"][0]["degree_status"] == "obtained"


def test_education_future_master_does_not_satisfy_master_gate(
    cv_payload: dict, position_payload: dict
) -> None:
    from datetime import date

    cv_payload["education"] = [
        {
            "education_id": "edu:future-master",
            "degree_level": "master",
            "field_of_study": None,
            "start_date": date(2026, 9, 1),
            "end_date": date(2029, 6, 1),
            "degree_status": "expected",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("edu:future-master", "2026.09-2029.06 硕士")],
        }
    ]
    position_payload["hard_conditions"] = [
        {
            "condition_id": "cond:edu",
            "condition_type": "education",
            "operator": "at_least",
            "value": "master",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("jd:edu", "硕士及以上")],
        }
    ]
    result = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    education = next(
        item
        for item in result.hard_constraint_results
        if item.constraint_type == "education"
    )
    assert education.status == "fail"
    assert education.reason_code == "DEGREE_NOT_YET_OBTAINED"


def test_experience_upper_bound_proves_failure(
    cv_payload: dict, position_payload: dict
) -> None:
    from datetime import date

    cv_payload["as_of_date"] = "2026-07-27"
    cv_payload["work_experiences"] = [
        {
            "experience_id": "exp:open",
            "kind": "work",
            "role": "intern",
            "responsibilities": ["internship"],
            "business_scenarios": [],
            "tool_skill_ids": [],
            "start_date": date(2026, 6, 1),
            "end_date": None,
            "is_current": False,
            "evidence_refs": [_evidence("exp:open", "2026.06 实习")],
        }
    ]
    position_payload["hard_conditions"] = [
        {
            "condition_id": "cond:exp",
            "condition_type": "experience",
            "operator": "at_least",
            "value": "3 years",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("jd:exp", "3 年以上经验")],
        }
    ]
    result = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    experience = next(
        item
        for item in result.hard_constraint_results
        if item.constraint_type == "experience"
    )
    assert experience.status == "fail"
    assert experience.reason_code == "EXPERIENCE_MAXIMUM_BELOW_REQUIRED"
    assert "-" in (experience.candidate_value or "")


def test_experience_current_date_is_honored(
    cv_payload: dict, position_payload: dict
) -> None:
    from datetime import date

    cv_payload["as_of_date"] = "2026-07-27"
    cv_payload["work_experiences"] = [
        {
            "experience_id": "exp:current",
            "kind": "work",
            "role": "developer",
            "responsibilities": ["backend"],
            "business_scenarios": [],
            "tool_skill_ids": [],
            "start_date": date(2023, 1, 1),
            "end_date": None,
            "is_current": True,
            "evidence_refs": [_evidence("exp:current", "2023.01 至今")],
        }
    ]
    position_payload["hard_conditions"] = [
        {
            "condition_id": "cond:exp",
            "condition_type": "experience",
            "operator": "at_least",
            "value": "3 years",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("jd:exp", "3 年以上经验")],
        }
    ]
    result = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    experience = next(
        item
        for item in result.hard_constraint_results
        if item.constraint_type == "experience"
    )
    assert experience.status == "pass"


def test_jd_or_group_is_derived_and_not_flattened(ready_position_json: dict) -> None:
    position_payload = _position_with_skill_quote(
        ready_position_json,
        "精通 Go 或 Python",
        [
            ("req:go", "LANG_GO", "Go"),
            ("req:python", "LANG_PYTHON", "Python"),
        ],
    )
    position = PositionMatchProfile.model_validate(position_payload)
    graph = build_requirement_graph_from_jd(position)
    assert graph is not None
    assert len(graph.groups) == 1
    group = graph.groups[0]
    assert group.group_type == "or"
    assert {child.ref_id for child in group.children} == {"req:go", "req:python"}


def test_slash_requirement_with_conjunctive_python_stays_separate(
    ready_position_json: dict,
) -> None:
    position_payload = _position_with_skill_quote(
        ready_position_json,
        "熟练掌握 Python,熟悉 C++ 或 Java 至少一种语言",
        [
            ("req:python", "LANG_PYTHON", "Python"),
            ("req:cpp", "LANG_CPP", "C++"),
            ("req:java", "LANG_JAVA", "Java"),
        ],
    )
    position = PositionMatchProfile.model_validate(position_payload)
    graph = build_requirement_graph_from_jd(position)
    assert graph is not None
    group = graph.groups[0]
    assert {child.ref_id for child in group.children} == {"req:cpp", "req:java"}


def test_skill_presence_proficiency_ownership_are_split(
    ready_cv_json: dict, ready_position_json: dict
) -> None:
    changed = deepcopy(ready_position_json)
    changed["required_skills"][0]["required_level"] = "expert"
    _refresh(changed)
    cv = CVMatchProfile.model_validate(ready_cv_json)
    position = PositionMatchProfile.model_validate(changed)
    result = evaluate_skills(cv, position, MatchingAlgorithmConfig())[0]
    assert result.match_status == "matched"
    assert result.skill_present is True
    assert result.proficiency_satisfied is False
    assert result.evidence_sufficient is True


def test_responsibility_candidate_text_joins_all_entries() -> None:
    experience = ExperienceFeature(
        experience_id="exp:1",
        kind="work",
        responsibilities=("负责 A", "负责 B"),
        evidence_refs=(),
    )
    text = responsibility_candidate_text(experience)
    assert "负责 A" in text
    assert "负责 B" in text


def test_what_if_planned_action_never_creates_evidence(
    cv_payload: dict, position_payload: dict
) -> None:
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    planned = WhatIfAction(
        action_id="learn-sql",
        action_type="add_skill",
        skill_id="skill_sql",
        canonical_name="SQL",
        target_level="working",
        milestone_status="planned",
        deliverable="SQL 学习记录",
        acceptance_criteria=("练习可复现",),
    )
    scenario = apply_actions(cv, position, (planned,), scenario_id="s1")
    assert not any(item.skill_id == "skill_sql" for item in scenario.capability_profiles)
    assert all(
        ref.source_id != "what-if:learn-sql"
        for item in scenario.skills
        for ref in item.evidence_refs
    )

    verified = planned.model_copy(update={"milestone_status": "verified"})
    scenario_verified = apply_actions(cv, position, (verified,), scenario_id="s2")
    assert any(
        item.skill_id == "skill_sql"
        for item in scenario_verified.capability_profiles
    )


def test_what_if_project_does_not_copy_jd_responsibilities(
    cv_payload: dict, position_payload: dict
) -> None:
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    action = WhatIfAction(
        action_id="project-sql",
        action_type="add_project_experience",
        skill_id="skill_sql",
        canonical_name="SQL",
        target_level="working",
        target_requirement_ids=("required:skill_sql",),
        responsibilities=("围绕 SQL 完成可验收的实践任务",),
        milestone_status="planned",
        deliverable="SQL 实践项目交付物",
        acceptance_criteria=("交付物可运行",),
    )
    scenario = apply_actions(cv, position, (action,), scenario_id="s1")
    assert not scenario.projects
    assert not any(
        item.skill_id == "skill_sql"
        for item in scenario.capability_profiles
    )


def test_cost_band_is_wide_and_confident() -> None:
    band = CostBand(
        min_hours=2.0,
        expected_hours=8.0,
        max_hours=24.0,
        confidence=0.25,
        basis="test",
    )
    assert band.min_hours < band.expected_hours < band.max_hours
    assert band.confidence < 0.5
    with pytest.raises(ValueError):
        CostBand(
            min_hours=10.0,
            expected_hours=5.0,
            max_hours=8.0,
            confidence=0.5,
            basis="test",
        )


def test_parent_child_relation_gets_no_score_credit(
    ready_cv_json: dict, ready_position_json: dict
) -> None:
    cv = CVMatchProfile.model_validate(ready_cv_json)
    position_payload = _position_with_skill_quote(
        ready_position_json,
        "parent",
        [("req:parent", "skill_programming_parent", "Programming")],
    )
    position = PositionMatchProfile.model_validate(position_payload)
    relation = SkillRelation(
        relation_id="rel:parent",
        source_skill_id="skill_python",
        target_skill_id="skill_programming_parent",
        relation_type="parent_child",
        source_system="test",
        graph_version="graph-42",
        confidence=0.9,
        evidence_refs=(
            Evidence(
                source_id="graph:rel:parent",
                quote="parent relation evidence",
                alignment="unresolved",
            ),
        ),
    )
    base = evaluate_skills(cv, position, MatchingAlgorithmConfig())[0]
    applied = apply_skill_relations(
        (base,),
        cv,
        (relation,),
        MatchingAlgorithmConfig().capability_levels,
        MatchingAlgorithmConfig().relations,
    )[0]
    assert applied.match_status == "missing"
    assert applied.reason_code == "PARENT_CHILD_LEARNING_ONLY"
    assert applied.transferability_score == 0.0


def test_partial_effective_requires_meaningful_change() -> None:
    result = WhatIfResult(
        generation_status="completed",
        scenario_id="scenario-test",
        baseline_score=50.0,
        scenario_score=50.3,
        score_delta=0.3,
        baseline_recommendation="weak_match",
        scenario_recommendation="weak_match",
        baseline_hard_gate_status="passed",
        scenario_hard_gate_status="passed",
    )
    status, reasons = classify_what_if_outcome(result, "unreachable")
    assert status == "no_effect"
    assert "NO_MEANINGFUL_EFFECT" in reasons

    improved = result.model_copy(
        update={
            "scenario_score": 56.0,
            "score_delta": 6.0,
        }
    )
    status, reasons = classify_what_if_outcome(improved, "unreachable")
    assert status == "partial_effective"
    assert "MEANINGFUL_SCORE_DELTA" in reasons


def test_what_if_projected_outcome_is_separate_from_current(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())
    result = service.evaluate(
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
                    "milestone_status": "planned",
                    "deliverable": "SQL 学习记录",
                    "acceptance_criteria": ("练习可复现",),
                }
            ],
        }
    )
    assert result.generation_status == "completed"
    assert result.projected_if_completed is True
    assert result.scenario_score == result.baseline_score
    assert result.projected_score is not None
    assert (result.projected_score or 0.0) > (result.baseline_score or 0.0)
    assert result.projected_recommendation is not None
    assert result.scenario_evaluation is not None
    assert all(
        ref.source_id != "what-if:learn-sql"
        for item in result.scenario_evaluation.skill_results
        for ref in item.candidate_evidence
    )


def test_information_sufficiency_material_does_not_block_recommendation(
    cv_payload: dict, position_payload: dict
) -> None:
    position_payload["hard_conditions"] = []
    position_payload["required_skills"] = [
        deepcopy(position_payload["required_skills"][0])
    ]
    position_payload["preferred_skills"] = []
    cv_payload["unresolved_items"] = [
        {
            "item_id": "cv:unresolved:project",
            "item_type": "field",
            "raw_value": "project_experience",
            "reason": "SOURCE_UNRESOLVED_FIELD",
            "evidence_refs": [],
        }
    ]
    result = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    final = result.final_match_result
    assert result.information_sufficiency_level == "material", (
        result.information_sufficiency_reasons
    )
    assert any(
        item.reason_code == "INFORMATION_MATERIAL_UNCERTAINTY"
        for item in final.uncertain_items
    )


def test_information_sufficiency_blocking_withholds_recommendation(
    cv_payload: dict, position_payload: dict
) -> None:
    position_payload["hard_conditions"] = [
        {
            "condition_id": "cond:edu",
            "condition_type": "education",
            "operator": "at_least",
            "value": "bachelor",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("jd:edu", "本科及以上")],
        }
    ]
    position_payload["required_skills"] = [
        deepcopy(position_payload["required_skills"][0])
    ]
    position_payload["preferred_skills"] = []
    cv_payload["education"] = [
        {
            "education_id": "edu:unverifiable",
            "degree_level": None,
            "field_of_study": None,
            "start_date": None,
            "end_date": None,
            "degree_status": "unknown",
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("edu:unverifiable", "北京大学 计算机科学与技术")],
        }
    ]
    cv_payload["unresolved_items"] = [
        {
            "item_id": "cv:unresolved:education",
            "item_type": "field",
            "raw_value": "education",
            "reason": "SOURCE_UNRESOLVED_FIELD",
            "evidence_refs": [],
        }
    ]
    result = MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    final = result.final_match_result
    assert result.information_sufficiency_level == "blocking"
    assert final.recommendation_level == "insufficient_information"


def test_responsibility_policy_four_states_without_touching_ce(
    cv_payload: dict, position_payload: dict
) -> None:
    cv_payload["work_experiences"] = [
        {
            "experience_id": "exp:1",
            "kind": "work",
            "role": "developer",
            "responsibilities": ["真实工作职责文本"],
            "business_scenarios": [],
            "tool_skill_ids": [],
            "start_date": None,
            "end_date": None,
            "is_current": False,
            "evidence_refs": [],
        }
    ]
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    policy = ResponsibilityDecisionPolicy()

    def result(
        match_status: str,
        retrieval: float,
        *,
        evidence: bool,
        margin: float,
    ) -> ResponsibilityResult:
        return ResponsibilityResult(
            requirement_id="responsibility:1",
            position_requirement="职责",
            candidate_experience_id=(
                "exp:1" if match_status == "matched" else None
            ),
            candidate_experience="text" if match_status == "matched" else None,
            match_status=match_status,  # type: ignore[arg-type]
            position_evidence=(),
            candidate_evidence=(
                (Evidence(source_id="s", quote="q", alignment="unresolved"),)
                if evidence
                else ()
            ),
            reason_code="RESPONSIBILITY_MATCHED"
            if match_status == "matched"
            else "RESPONSIBILITY_NOT_OBSERVED",
            confidence=0.9,
            ce_score=2.0,
            retrieval_score=retrieval,
            threshold_margin=margin,
            top_candidates=(
                ResponsibilityCandidate(
                    experience_id="exp:1",
                    text="text",
                    retrieval_score=retrieval,
                    ce_score=2.0,
                    threshold_margin=margin,
                    evidence_refs=(
                        (Evidence(source_id="s", quote="q", alignment="unresolved"),)
                        if evidence
                        else ()
                    ),
                ),
            ),
        )

    cases = [
        ("matched", 0.6, True, 0.2, "matched"),
        ("matched", 0.4, True, 0.2, "partial"),
        ("matched", 0.4, False, 0.2, "insufficient_evidence"),
        ("not_observed", 0.5, True, -0.5, "not_observed"),
        ("not_observed", 0.2, True, -0.5, "insufficient_evidence"),
    ]
    for match_status, retrieval, evidence, margin, expected in cases:
        applied = policy.apply(
            (
                result(
                    match_status,
                    retrieval,
                    evidence=evidence,
                    margin=margin,
                ),
            ),
            cv,
            position,
        )[0]
        assert applied.status_detail == expected, (
            match_status,
            retrieval,
            expected,
        )
