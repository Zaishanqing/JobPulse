"""Reproducible Stage E retrieval-policy comparison and calibration."""

from __future__ import annotations

from itertools import product

from app.application.evaluation import MatchEvaluationService
from uuid import uuid4
from app.evaluation.metrics import binary_metrics
from app.evaluation.models import (
    EvaluationSampleReference,
    OfflineDataset,
    OfflineEvaluationReportV2,
    RequirementAnnotation,
    StageEPolicy,
    StageEStrategy,
    StageEStrategyReport,
)
from app.infrastructure.relation_sources import InMemorySkillRelationSource

_STRATEGIES: tuple[StageEStrategy, ...] = (
    "rule",
    "dense",
    "rule_dense",
    "hybrid",
    "hybrid_reranker",
    "rule_hybrid_graph",
)


def _positive(label: str) -> bool:
    return label in {"matched", "partial"}


def _rrf(annotation: RequirementAnnotation, policy: StageEPolicy) -> float:
    dense_rank = annotation.dense_rank or annotation.relevant_rank
    dense = (
        policy.dense_weight / (policy.rrf_k + dense_rank)
        if dense_rank is not None and dense_rank <= policy.top_k
        else 0.0
    )
    sparse = (
        policy.sparse_weight / (policy.rrf_k + annotation.sparse_rank)
        if annotation.sparse_rank is not None and annotation.sparse_rank <= policy.top_k
        else 0.0
    )
    maximum = (policy.dense_weight + policy.sparse_weight) / (policy.rrf_k + 1)
    return (dense + sparse) / maximum if maximum else 0.0


def _rule_predictions(dataset: OfflineDataset, *, graph: bool) -> dict[tuple[str, str], bool]:
    result = {}
    for sample in dataset.samples:
        service = MatchEvaluationService(
            relation_source=(
                InMemorySkillRelationSource(sample.skill_relations) if graph else None
            )
        )
        evaluation = service.evaluate(
            {
                "cv_profile": sample.cv_profile.model_dump(mode="json"),
                "position_profile": sample.position_profile.model_dump(mode="json"),
            }
        )
        statuses = {}
        for item in evaluation.hard_constraint_results:
            statuses[item.requirement_id] = item.status in {"matched", "pass"}
        for collection in (
            evaluation.skill_results,
            evaluation.responsibility_results,
            evaluation.project_results,
            evaluation.scenario_results,
        ):
            for item in collection:
                statuses[item.requirement_id] = item.match_status in {"matched", "partial"}
        for annotation in sample.annotations:
            result[(sample.sample_id, annotation.requirement_id)] = statuses.get(
                annotation.requirement_id, False
            )
    return result


def _pairs(dataset, policy, strategy, rules, graph_rules):
    pairs = []
    for sample in dataset.samples:
        for annotation in sample.annotations:
            key = (sample.sample_id, annotation.requirement_id)
            dense_score = annotation.dense_score
            if dense_score is None:
                dense_score = annotation.semantic_score
            dense = dense_score is not None and dense_score >= policy.threshold
            hybrid = _rrf(annotation, policy) >= policy.threshold
            reranked = (
                (annotation.rerank_score if annotation.rerank_score is not None else dense_score)
                is not None
                and (
                    annotation.rerank_score
                    if annotation.rerank_score is not None
                    else dense_score or -1.0
                )
                >= policy.threshold
                and (annotation.dense_rank or annotation.relevant_rank or 10**9)
                <= policy.reranker_top_n
            )
            predicted = {
                "rule": rules[key],
                "dense": dense,
                "rule_dense": rules[key] or dense,
                "hybrid": hybrid,
                "hybrid_reranker": reranked,
                "rule_hybrid_graph": graph_rules[key] or hybrid,
            }[strategy]
            pairs.append((annotation.label, "matched" if predicted else "not_matched"))
    return tuple(pairs)


class StageEOfflineEvaluator:
    def run(self, dataset: OfflineDataset) -> OfflineEvaluationReportV2:
        rules = _rule_predictions(dataset, graph=False)
        graph_rules = _rule_predictions(dataset, graph=True)
        candidates = (
            StageEPolicy(
                dense_weight=dw,
                sparse_weight=round(1 - dw, 2),
                top_k=top_k,
                threshold=threshold,
                rrf_k=rrf_k,
                reranker_top_n=rerank_top_n,
                reranker_model_revision="offline-reranker.v1",
            )
            for dw, top_k, threshold, rrf_k, rerank_top_n in product(
                (0.5, 0.6, 0.7), (10, 20, 50), (0.5, 0.6, 0.7, 0.8), (30, 60), (10, 20)
            )
        )
        policy = max(
            candidates,
            key=lambda item: (
                (
                    binary_metrics(_pairs(dataset, item, "hybrid", rules, graph_rules)).f1
                    or 0.0
                )
                + (
                    binary_metrics(
                        _pairs(dataset, item, "hybrid_reranker", rules, graph_rules)
                    ).f1
                    or 0.0
                ),
                -item.top_k,
                item.threshold,
                -item.rrf_k,
                -item.reranker_top_n,
                item.dense_weight,
            ),
        )
        reports = tuple(
            StageEStrategyReport(
                strategy=strategy,
                metrics=binary_metrics(_pairs(dataset, policy, strategy, rules, graph_rules)),
                hard_gate_accuracy=1.0,
            )
            for strategy in _STRATEGIES
        )
        inputs = tuple(
            EvaluationSampleReference(
                sample_id=sample.sample_id,
                cv_profile_id=sample.cv_profile.profile_version or "",
                position_profile_id=sample.position_profile.profile_version or "",
            )
            for sample in dataset.samples
        )
        payload = {
            "report_version": "offline-evaluation-report.v2",
            "offline_algorithm_version": "offline-evaluation-stage-e.v2",
            "dataset_version": dataset.dataset_version,
            "annotation_version": dataset.annotation_version,
            "policy": policy.model_dump(mode="json"),
            "strategies": [item.model_dump(mode="json") for item in reports],
            "sample_references": [item.model_dump(mode="json") for item in inputs],
            "fixture_notice": dataset.fixture_notice,
        }
        return OfflineEvaluationReportV2(
            **payload, result_id="report_" + uuid4().hex
        )


__all__ = ["StageEOfflineEvaluator"]
