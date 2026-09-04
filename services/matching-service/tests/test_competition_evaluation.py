from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.application.deepseek_semantic_candidates import (
    DeepSeekSemanticCandidateConfig,
    DeepSeekSemanticCandidateService,
)
from app.application.evaluation import MatchEvaluationService
from app.domain.deepseek_candidates import (
    LLMSemanticCandidateBatch,
    RawDeepSeekSkillCandidate,
)
from app.domain.gap_analysis import build_gap_analysis
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.skill_relations import SkillRelation
from app.evaluation.competition import (
    CompetitionOfflineEvaluator,
    _semantic_no_score_accuracy,
)
from app.evaluation.models import (
    ExpectedSkillRelation,
    LearningPrerequisiteExpectation,
    OfflineDataset,
    OfflineSample,
    RequirementAnnotation,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _evidence(source: str, quote: str) -> dict:
    return {
        "source_id": source,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _relation(
    relation_id: str,
    source: str,
    target: str,
    relation_type: str,
) -> SkillRelation:
    return SkillRelation(
        relation_id=relation_id,
        source_skill_id=source,
        target_skill_id=target,
        relation_type=relation_type,  # type: ignore[arg-type]
        source_system="competition-fixture",
        graph_version="competition-graph.v1",
        confidence=0.9,
        evidence_refs=(_evidence(f"graph:{relation_id}", f"{source} {relation_type} {target}"),),
    )


def _refresh(profile: dict) -> dict:
    profile["profile_version"] = "profile-source.v1"
    return profile


def _base_cv() -> dict:
    return _refresh(
        deepcopy(json.loads((FIXTURES / "cv_ready.json").read_text(encoding="utf-8")))
    )


def _base_position() -> dict:
    return _refresh(
        deepcopy(
            json.loads((FIXTURES / "position_ready.json").read_text(encoding="utf-8"))
        )
    )


def _position_with_skills(position: dict, required: list[dict]) -> dict:
    updated = deepcopy(position)
    updated["required_skills"] = required
    updated["preferred_skills"] = []
    return _refresh(updated)


def _skill_requirement(skill_id: str, name: str, quote: str) -> dict:
    return {
        "skill_id": skill_id,
        "canonical_name": name,
        "required_level": "working",
        "importance": 1.0,
        "resolution_status": "resolved",
        "evidence_refs": [_evidence(f"jd:{skill_id}", quote)],
    }


def _sample(
    sample_id: str,
    cv: dict,
    position: dict,
    *,
    annotations: tuple[RequirementAnnotation, ...],
    relations: tuple[SkillRelation, ...] = (),
    expected_relations: tuple[ExpectedSkillRelation, ...] = (),
    expected_prerequisites: tuple[LearningPrerequisiteExpectation, ...] = (),
    time_budget_hours: float | None = None,
) -> OfflineSample:
    return OfflineSample(
        sample_id=sample_id,
        cv_profile=CVMatchProfile.model_validate(cv),
        position_profile=PositionMatchProfile.model_validate(position),
        annotations=annotations,
        expected_recommendation="potential_match",
        expected_hard_gate_status="passed",
        skill_relations=relations,
        expected_skill_relations=expected_relations,
        expected_learning_prerequisites=expected_prerequisites,
        expected_semantic_no_score_requirement_ids=(),
        time_budget_hours=time_budget_hours,
    )


def _annotation(requirement_id: str, label: str, *, rank: int | None = None):
    return RequirementAnnotation(
        requirement_id=requirement_id,
        dimension="required_skill",
        label=label,  # type: ignore[arg-type]
        relevant_rank=rank,
    )


def _fixed_dataset() -> OfflineDataset:
    cv = _base_cv()
    no_gap_position = _position_with_skills(
        _base_position(),
        [_skill_requirement("skill_python", "Python", "Python")],
    )
    multi_position = _position_with_skills(
        _base_position(),
        [
            _skill_requirement("skill_python", "Python", "Python"),
            _skill_requirement("skill_go", "Go", "Go"),
            _skill_requirement("skill_advanced", "Advanced", "Advanced"),
        ],
    )
    prerequisite_position = _position_with_skills(
        _base_position(),
        [
            _skill_requirement("skill_foundation", "Foundation", "Foundation"),
            _skill_requirement("skill_advanced", "Advanced", "Advanced"),
        ],
    )
    transferable_position = _position_with_skills(
        _base_position(),
        [_skill_requirement("skill_go", "Go", "Go")],
    )
    samples = (
        _sample(
            "competition_no_gap",
            cv,
            no_gap_position,
            annotations=(_annotation("required:skill_python", "matched", rank=1),),
            expected_relations=(
                ExpectedSkillRelation(
                    requirement_id="required:skill_python",
                    relation_type="exact",
                ),
            ),
        ),
        _sample(
            "competition_multiple_missing",
            cv,
            multi_position,
            annotations=(
                _annotation("required:skill_python", "matched"),
                _annotation("required:skill_go", "not_matched"),
                _annotation("required:skill_advanced", "not_matched"),
            ),
            expected_relations=(
                ExpectedSkillRelation(
                    requirement_id="required:skill_go",
                    relation_type="unknown",
                    evidence_required=False,
                ),
                ExpectedSkillRelation(
                    requirement_id="required:skill_advanced",
                    relation_type="unknown",
                    evidence_required=False,
                ),
            ),
            time_budget_hours=2.0,
        ),
        _sample(
            "competition_prerequisite_chain",
            cv,
            prerequisite_position,
            annotations=(
                _annotation("required:skill_foundation", "not_matched"),
                _annotation("required:skill_advanced", "not_matched"),
            ),
            relations=(
                _relation(
                    "foundation_from_python",
                    "skill_python",
                    "skill_foundation",
                    "prerequisite",
                ),
                _relation(
                    "advanced_from_foundation",
                    "skill_foundation",
                    "skill_advanced",
                    "prerequisite",
                ),
            ),
            expected_relations=(
                ExpectedSkillRelation(
                    requirement_id="required:skill_foundation",
                    relation_type="unknown",
                    evidence_required=False,
                ),
                ExpectedSkillRelation(
                    requirement_id="required:skill_advanced",
                    relation_type="unknown",
                    evidence_required=False,
                ),
            ),
            expected_prerequisites=(
                LearningPrerequisiteExpectation(
                    target_skill_id="skill_advanced",
                    prerequisite_skill_id="skill_foundation",
                ),
            ),
        ),
        _sample(
            "competition_transferable",
            cv,
            transferable_position,
            annotations=(_annotation("required:skill_go", "partial", rank=1),),
            relations=(
                _relation(
                    "go_from_python",
                    "skill_python",
                    "skill_go",
                    "transferable",
                ),
            ),
            expected_relations=(
                ExpectedSkillRelation(
                    requirement_id="required:skill_go",
                    relation_type="transferable",
                ),
            ),
        ),
        _sample(
            "competition_kg_missing",
            cv,
            transferable_position,
            annotations=(_annotation("required:skill_go", "not_matched"),),
            expected_relations=(
                ExpectedSkillRelation(
                    requirement_id="required:skill_go",
                    relation_type="unknown",
                    evidence_required=False,
                ),
            ),
        ),
    )
    return OfflineDataset(
        dataset_version="matching-competition-fixed.v1",
        annotation_version="matching-competition-annotations.v1",
        samples=samples,
        fixture_notice="anonymous_fixture_not_business_accuracy",
    )


def test_fixed_competition_dataset_metrics_are_fully_correct():
    report = CompetitionOfflineEvaluator().run(_fixed_dataset())

    assert report.metrics.sample_count == 5
    assert report.metrics.hard_gate_accuracy == 1.0
    assert report.metrics.relation_explanation_accuracy == 1.0
    assert report.metrics.learning_path_order_accuracy == 1.0
    assert report.metrics.mean_reciprocal_rank == 1.0
    assert report.metrics.semantic_no_score_accuracy == 1.0
    assert report.result_id


def test_time_budget_marks_steps_outside_budget():
    dataset = _fixed_dataset()
    sample = next(
        item
        for item in dataset.samples
        if item.sample_id == "competition_multiple_missing"
    )
    evaluation = MatchEvaluationService(
        relation_source=None,
    ).evaluate(
        {
            "cv_profile": sample.cv_profile.model_dump(mode="json"),
            "position_profile": sample.position_profile.model_dump(mode="json"),
        }
    )
    gap = build_gap_analysis(evaluation, time_budget_hours=2.0)

    assert gap.time_budget_hours == 2.0
    assert gap.over_budget is True
    assert all(step.cost_source_type == "heuristic" for step in gap.learning_path)
    assert all(step.cost_source_ref == "gap-learning-hours.v1" for step in gap.learning_path)
    assert all(step.estimate_status == "estimated" for step in gap.learning_path)


def test_unverified_semantic_candidate_has_no_score_contribution():
    source = type(
        "Source",
        (),
        {
            "generate_candidates": lambda self, **kwargs: LLMSemanticCandidateBatch(
                model="fixture",
                algorithm_version="fixture",
                candidates=(
                    RawDeepSeekSkillCandidate(
                        requirement_id="required:skill_go",
                        required_skill_id="skill_go",
                        required_skill_name="Go",
                        candidate_skill_id="skill_python",
                        candidate_skill_name="Python",
                        proposed_relation_type="related",
                        rationale="candidate",
                        position_quote="Go",
                        candidate_quote="Python",
                    ),
                ),
            )
        },
    )()
    cv = _base_cv()
    position = _position_with_skills(
        _base_position(),
        [_skill_requirement("skill_go", "Go", "Go")],
    )
    evaluation = MatchEvaluationService(
        semantic_candidates=DeepSeekSemanticCandidateService(
            source,
            DeepSeekSemanticCandidateConfig(mode="enabled"),  # type: ignore[arg-type]
        ),
    ).evaluate(
        {
            "cv_profile": cv,
            "position_profile": position,
        }
    )

    assert _semantic_no_score_accuracy(evaluation, ("required:skill_go",)) == 1.0
    contribution = next(
        item
        for item in evaluation.final_match_result.score_contributions
        if item.result_id == "required:skill_go"
    )
    assert contribution.score_value is None
