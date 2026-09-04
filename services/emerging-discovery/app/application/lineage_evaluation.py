"""Lineage-aware evaluator that keeps the legacy one-ID metrics intact.

The evaluator is the only layer allowed to see Gold/formal metadata.  The
resolver never receives Gold; this module compares its structural relations to
an explicitly supplied ground-truth relation set and reports both legacy
single-ID continuity and lineage-aware continuity.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from app.domain.candidate_lineage import (
    LINEAGE_EVALUATOR_VERSION,
    CandidateLineageRelation,
    LineageDecision,
)


@dataclass(frozen=True)
class LineageGroundTruthUnit:
    unit_id: str
    relation_type: str
    source_candidate_ids: tuple[str, ...]
    target_cluster_ids: tuple[str, ...]
    stable: bool = False


@dataclass(frozen=True)
class LegacyContinuityObservation:
    unit_id: str
    window_id: str
    candidate_id: str | None


def _matches(
    relation: CandidateLineageRelation,
    ground_truth: LineageGroundTruthUnit,
) -> bool:
    return (
        not relation.review_required
        and relation.relation_type == ground_truth.relation_type
        and set(relation.source_candidate_ids) == set(ground_truth.source_candidate_ids)
        and set(relation.target_cluster_ids) == set(ground_truth.target_cluster_ids)
    )


def _unit_recovered(
    relations: Sequence[CandidateLineageRelation],
    ground_truth: LineageGroundTruthUnit,
) -> bool:
    return any(_matches(relation, ground_truth) for relation in relations)


def _legacy_continuous(
    unit_id: str,
    observations: Sequence[LegacyContinuityObservation],
) -> bool:
    values = [
        observation.candidate_id
        for observation in observations
        if observation.unit_id == unit_id and observation.candidate_id is not None
    ]
    return bool(values) and len(set(values)) == 1


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 6)


def evaluate_lineage(
    *,
    generated_relations: Sequence[CandidateLineageRelation],
    generated_decisions: Sequence[LineageDecision],
    ground_truth: Sequence[LineageGroundTruthUnit],
    legacy_observations: Sequence[LegacyContinuityObservation],
) -> Mapping[str, str | int | float | bool | list | tuple | None]:
    """Evaluate lineage hypotheses against an evaluator-only Gold set."""
    accepted = [
        relation for relation in generated_relations if not relation.review_required
    ]
    review_decisions = [
        decision
        for decision in generated_decisions
        if decision.decision_type == "REVIEW" or decision.review_required
    ]
    new_decisions = [
        decision
        for decision in generated_decisions
        if decision.decision_type == "NEW"
    ]

    relation_counts: dict[str, int] = defaultdict(int)
    split_generated: list[CandidateLineageRelation] = []
    merge_generated: list[CandidateLineageRelation] = []
    continue_generated: list[CandidateLineageRelation] = []
    for relation in accepted:
        relation_counts[relation.relation_type] += 1
        if relation.relation_type == "SPLIT":
            split_generated.append(relation)
        elif relation.relation_type == "MERGE":
            merge_generated.append(relation)
        elif relation.relation_type == "CONTINUE":
            continue_generated.append(relation)

    split_truth = [item for item in ground_truth if item.relation_type == "SPLIT"]
    merge_truth = [item for item in ground_truth if item.relation_type == "MERGE"]
    continue_truth = [item for item in ground_truth if item.relation_type == "CONTINUE"]
    stable_truth = [item for item in ground_truth if item.stable]

    def typed_metrics(
        generated: Sequence[CandidateLineageRelation],
        truth: Sequence[LineageGroundTruthUnit],
    ) -> Mapping[str, str | int | float | bool | list | tuple | None]:
        tp = sum(
            1
            for relation in generated
            if any(_matches(relation, item) for item in truth)
        )
        precision = _rate(tp, len(generated))
        recall = _rate(tp, len(truth))
        return {
            "true_positive": tp,
            "generated": len(generated),
            "ground_truth": len(truth),
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }

    total_tp = sum(
        1
        for relation in accepted
        if any(_matches(relation, item) for item in ground_truth)
    )
    lineage_precision = _rate(total_tp, len(accepted))
    lineage_recall = _rate(total_tp, len(ground_truth))

    recovered_units = [
        item
        for item in ground_truth
        if _unit_recovered(generated_relations, item)
    ]
    recovered_ids = {item.unit_id for item in recovered_units}
    recovered_stable = [item for item in recovered_units if item.stable]
    legacy_continuous_ids = {
        item.unit_id
        for item in ground_truth
        if _legacy_continuous(item.unit_id, legacy_observations)
    }
    fragmented_ids = {item.unit_id for item in ground_truth} - legacy_continuous_ids
    lineage_fragmentation_recovered = sorted(fragmented_ids & recovered_ids)
    legacy_continuous_stable = {
        item.unit_id
        for item in stable_truth
        if item.unit_id in legacy_continuous_ids
    }
    false_lineage = [
        relation
        for relation in accepted
        if not any(_matches(relation, item) for item in ground_truth)
    ]
    false_relation_counts: dict[str, int] = defaultdict(int)
    for relation in false_lineage:
        false_relation_counts[relation.relation_type] += 1

    return {
        "evaluator_version": LINEAGE_EVALUATOR_VERSION,
        "generated_relations": len(generated_relations),
        "accepted_relations": len(accepted),
        "review_relations": len(review_decisions),
        "new_decisions": len(new_decisions),
        "generated_by_type": dict(relation_counts),
        "lineage_precision": lineage_precision,
        "lineage_recall": lineage_recall,
        "lineage_f1": _f1(lineage_precision, lineage_recall),
        "split": typed_metrics(split_generated, split_truth),
        "merge": typed_metrics(merge_generated, merge_truth),
        "continue": typed_metrics(continue_generated, continue_truth),
        "lineage_aware_continuity": _rate(len(recovered_units), len(ground_truth)),
        "lineage_aware_stable_recall": _rate(len(recovered_stable), len(stable_truth)),
        "lineage_aware_fragmentation": {
            "recovered_count": len(lineage_fragmentation_recovered),
            "fragmented_legacy_count": len(fragmented_ids),
            "recovered_unit_ids": lineage_fragmentation_recovered,
        },
        "legacy_one_id_continuity": _rate(
            len(legacy_continuous_ids),
            len(ground_truth),
        ),
        "legacy_stable_recall": _rate(
            len(legacy_continuous_stable),
            len(stable_truth),
        ),
        "false_lineage": {
            "count": len(false_lineage),
            "by_type": dict(false_relation_counts),
            "relation_ids": [item.relation_id for item in false_lineage],
        },
        "recovered_unit_ids": sorted(recovered_ids),
        "formulas": {
            "lineage_precision": "matching accepted lineage relations / generated accepted relations",
            "lineage_recall": "matching accepted lineage relations / evaluator Gold structural relations",
            "lineage_aware_continuity": "structural units recovered by lineage / evaluator structural units",
            "lineage_aware_stable_recall": "stable units recovered by lineage / evaluator stable units",
            "legacy_one_id_continuity": "units continuous under ordinary single Candidate IDs / units",
            "lineage_aware_fragmentation": "units fragmented under legacy IDs but recovered by lineage",
            "false_lineage": "accepted lineage relations matching no evaluator Gold relation",
        },
    }


def summarize_legacy_vs_lineage(
    evaluation: Mapping[str, str | int | float | bool | list | tuple | None],
) -> Mapping[str, str | int | float | bool | list | tuple | None]:
    """Side-by-side A/B summary requested by the batch protocol."""
    return {
        "legacy": {
            "one_id_continuity": evaluation["legacy_one_id_continuity"],
            "stable_recall": evaluation["legacy_stable_recall"],
            "fragmented_units": evaluation["lineage_aware_fragmentation"][
                "fragmented_legacy_count"
            ],
        },
        "lineage_aware": {
            "continuity": evaluation["lineage_aware_continuity"],
            "stable_recall": evaluation["lineage_aware_stable_recall"],
            "fragmentation_recovered": evaluation["lineage_aware_fragmentation"][
                "recovered_count"
            ],
        },
        "precision": evaluation["lineage_precision"],
        "recall": evaluation["lineage_recall"],
    }
