"""Fixed labeled competition evaluation for MATCH-05/MATCH-06 acceptance."""

from __future__ import annotations

from app.application.evaluation import MatchEvaluationService
from app.domain.evaluation import MatchEvaluation
from uuid import uuid4
from app.domain.gap_analysis import build_gap_analysis
from app.evaluation.metrics import mean_reciprocal_rank
from app.evaluation.models import (
    CompetitionEvaluationReport,
    CompetitionMetrics,
    CompetitionSampleResult,
    ExpectedSkillRelation,
    LearningPrerequisiteExpectation,
    OfflineDataset,
)
from app.infrastructure.relation_sources import InMemorySkillRelationSource


def _predicted_relation(
    evaluation: MatchEvaluation, requirement_id: str
) -> tuple[str, bool]:
    for item in evaluation.skill_results:
        if item.requirement_id != requirement_id:
            continue
        relation_type = item.match_type
        if relation_type not in {"exact", "equivalent", "related", "transferable"}:
            relation_type = "unknown"
        has_evidence = (
            bool(item.candidate_evidence)
            if relation_type == "exact"
            else bool(item.relation_evidence and item.candidate_evidence)
        )
        return relation_type, has_evidence
    return "unknown", False


def _relation_accuracy(
    evaluation: MatchEvaluation,
    expected: tuple[ExpectedSkillRelation, ...],
) -> float:
    if not expected:
        return 1.0
    correct = 0
    for item in expected:
        predicted, has_evidence = _predicted_relation(evaluation, item.requirement_id)
        ok = predicted == item.relation_type and (
            not item.evidence_required or item.relation_type == "unknown" or has_evidence
        )
        correct += int(ok)
    return correct / len(expected)


def _semantic_no_score_accuracy(
    evaluation: MatchEvaluation,
    expected: tuple[str, ...],
) -> float:
    if not expected:
        return 1.0
    contributions = {
        item.result_id: item
        for item in (evaluation.final_match_result.score_contributions or ())
        if evaluation.final_match_result is not None
    }
    correct = 0
    for requirement_id in expected:
        skill = next(
            (
                item
                for item in evaluation.skill_results
                if item.requirement_id == requirement_id
            ),
            None,
        )
        contribution = contributions.get(requirement_id)
        ok = (
            skill is not None
            and skill.match_type == "semantic_candidate"
            and skill.match_status == "unknown"
            and (contribution is None or contribution.score_value is None)
        )
        correct += int(ok)
    return correct / len(expected)


def _learning_path_accuracy(
    evaluation: MatchEvaluation,
    expected: tuple[LearningPrerequisiteExpectation, ...],
    time_budget_hours: float | None,
) -> float:
    if not expected:
        return 1.0
    gap = build_gap_analysis(
        evaluation,
        time_budget_hours=time_budget_hours,
    )
    by_skill = {
        step.target_skill_id: step for step in gap.learning_path if step.target_skill_id
    }
    correct = 0
    for item in expected:
        target = by_skill.get(item.target_skill_id)
        prerequisite = by_skill.get(item.prerequisite_skill_id)
        ok = (
            target is not None
            and prerequisite is not None
            and prerequisite.step_order < target.step_order
            and item.prerequisite_skill_id in target.prerequisite_skill_ids
        )
        correct += int(ok)
    return correct / len(expected)


class CompetitionOfflineEvaluator:
    def run(self, dataset: OfflineDataset) -> CompetitionEvaluationReport:
        samples = []
        for sample in dataset.samples:
            service = MatchEvaluationService(
                relation_source=InMemorySkillRelationSource(sample.skill_relations),
            )
            evaluation = service.evaluate(
                {
                    "cv_profile": sample.cv_profile.model_dump(mode="json"),
                    "position_profile": sample.position_profile.model_dump(mode="json"),
                }
            )
            final = evaluation.final_match_result
            hard_gate_accuracy = (
                1.0
                if final is not None
                and final.hard_gate_status == sample.expected_hard_gate_status
                else 0.0
            )
            mrr = mean_reciprocal_rank(sample.annotations, enabled=True)
            relation = _relation_accuracy(evaluation, sample.expected_skill_relations)
            semantic = _semantic_no_score_accuracy(
                evaluation, sample.expected_semantic_no_score_requirement_ids
            )
            path = _learning_path_accuracy(
                evaluation,
                sample.expected_learning_prerequisites,
                sample.time_budget_hours,
            )
            samples.append(
                CompetitionSampleResult(
                    sample_id=sample.sample_id,
                    mean_reciprocal_rank=mrr,
                    hard_gate_accuracy=hard_gate_accuracy,
                    relation_explanation_accuracy=relation,
                    semantic_no_score_accuracy=semantic,
                    learning_path_order_accuracy=path,
                )
            )
        metrics = CompetitionMetrics(
            mean_reciprocal_rank=_average(
                item.mean_reciprocal_rank for item in samples
            ),
            hard_gate_accuracy=sum(
                item.hard_gate_accuracy for item in samples
            )
            / len(samples),
            relation_explanation_accuracy=sum(
                item.relation_explanation_accuracy for item in samples
            )
            / len(samples),
            semantic_no_score_accuracy=sum(
                item.semantic_no_score_accuracy for item in samples
            )
            / len(samples),
            learning_path_order_accuracy=sum(
                item.learning_path_order_accuracy for item in samples
            )
            / len(samples),
            sample_count=len(samples),
        )
        payload = {
            "report_version": "competition-evaluation-report.v1",
            "dataset_version": dataset.dataset_version,
            "annotation_version": dataset.annotation_version,
            "metrics": metrics.model_dump(mode="json"),
            "samples": [item.model_dump(mode="json") for item in samples],
            "fixture_notice": dataset.fixture_notice,
        }
        return CompetitionEvaluationReport(
            **payload,
            result_id="report_" + uuid4().hex,
        )


def _average(values) -> float | None:
    known = [value for value in values if value is not None]
    return round(sum(known) / len(known), 6) if known else None


__all__ = ["CompetitionOfflineEvaluator"]
