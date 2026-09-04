"""Offline clustering metrics for optional labelled competition datasets."""

from __future__ import annotations

import math
from collections import Counter

from app.application.comparison import CompareAlgorithms
from app.application.contracts import RunDiscoveryCommand
from app.domain.values import FrozenDict, JsonObject, freeze


def _comb2(value: int) -> float:
    return value * (value - 1) / 2


def _supervised_metrics(truth: list[str], predicted: list[str]) -> tuple[float, float, float]:
    total = len(truth)
    truth_counts = Counter(truth)
    predicted_counts = Counter(predicted)
    joint = Counter(zip(truth, predicted, strict=True))
    mutual_information = sum(
        (count / total)
        * math.log(
            (count * total) / (truth_counts[truth_label] * predicted_counts[predicted_label])
        )
        for (truth_label, predicted_label), count in joint.items()
    )
    truth_entropy = -sum(
        (count / total) * math.log(count / total) for count in truth_counts.values()
    )
    predicted_entropy = -sum(
        (count / total) * math.log(count / total) for count in predicted_counts.values()
    )
    denominator = math.sqrt(truth_entropy * predicted_entropy)
    nmi = mutual_information / denominator if denominator else float(truth == predicted)

    joint_pairs = sum(_comb2(value) for value in joint.values())
    truth_pairs = sum(_comb2(value) for value in truth_counts.values())
    predicted_pairs = sum(_comb2(value) for value in predicted_counts.values())
    total_pairs = _comb2(total)
    expected = truth_pairs * predicted_pairs / total_pairs if total_pairs else 0.0
    ari_denominator = 0.5 * (truth_pairs + predicted_pairs) - expected
    ari = (
        (joint_pairs - expected) / ari_denominator if ari_denominator else float(truth == predicted)
    )
    purity = (
        sum(
            max(
                (count for (label, cluster), count in joint.items() if cluster == value),
                default=0,
            )
            for value in predicted_counts
        )
        / total
    )
    return round(nmi, 6), round(ari, 6), round(purity, 6)


class EvaluateAlgorithmsOffline:
    def __init__(self, comparison: CompareAlgorithms) -> None:
        self.comparison = comparison

    def execute(
        self,
        command: RunDiscoveryCommand,
        algorithms: tuple[str, ...],
        algorithm_configs: JsonObject,
        labels: JsonObject,
        positive_candidate_jd_ids: tuple[str, ...],
        top_k: int,
        labeling_basis: str,
    ) -> JsonObject:
        compared = self.comparison.execute(command, algorithms, algorithm_configs)
        results = []
        for algorithm in compared.algorithms:
            predicted_by_jd = {
                jd_id: f"cluster:{index}"
                for index, cluster in enumerate(algorithm.clusters)
                for jd_id in cluster.member_jd_ids
            }
            predicted_by_jd.update(
                {str(item["jd_id"]): f"noise:{item['jd_id']}" for item in algorithm.noise_points}
            )
            labelled_ids = sorted(set(labels) & set(predicted_by_jd))
            if len(labelled_ids) >= 2:
                nmi, ari, purity = _supervised_metrics(
                    [str(labels[item]) for item in labelled_ids],
                    [predicted_by_jd[item] for item in labelled_ids],
                )
                supervised_status = "available"
            else:
                nmi = ari = purity = None
                supervised_status = "unavailable"
            ranked = [
                jd_id
                for cluster in sorted(
                    algorithm.clusters,
                    key=lambda item: (-len(item.member_jd_ids), item.cluster_key),
                )
                for jd_id in sorted(cluster.member_jd_ids)
            ]
            if positive_candidate_jd_ids:
                retrieved = ranked[:top_k]
                correct_candidates = sorted(
                    set(retrieved) & set(positive_candidate_jd_ids)
                )
                error_candidates = sorted(
                    set(retrieved) - set(positive_candidate_jd_ids)
                )
                missed_candidates = sorted(
                    set(positive_candidate_jd_ids) - set(retrieved)
                )
                precision_at_k = round(
                    len(correct_candidates)
                    / max(min(top_k, len(ranked)), 1),
                    6,
                )
            else:
                precision_at_k = None
                retrieved = []
                correct_candidates = []
                error_candidates = []
                missed_candidates = []
            results.append(
                {
                    "algorithm": algorithm.algorithm,
                    "silhouette_coefficient": algorithm.silhouette_coefficient,
                    "nmi": nmi,
                    "ari": ari,
                    "cluster_purity": purity,
                    "precision_at_k": precision_at_k,
                    "top_k": top_k,
                    "labelled_sample_count": len(labelled_ids),
                    "supervised_metrics_status": supervised_status,
                    "stability": algorithm.stability_analysis,
                    "retrieved_candidate_jd_ids": retrieved,
                    "correct_candidate_jd_ids": correct_candidates,
                    "error_candidate_jd_ids": error_candidates,
                    "missed_candidate_jd_ids": missed_candidates,
                    "cluster_memberships": [
                        {
                            "cluster_key": cluster.cluster_key,
                            "member_jd_ids": cluster.member_jd_ids,
                        }
                        for cluster in algorithm.clusters
                    ],
                }
            )
        value = freeze(
            {
                "contract_version": command.contract_version,
                "request_id": command.request_id,
                "evaluation_mode": ("labelled" if labels else "unsupervised_only"),
                "evaluation_sample": {
                    "sample_count": len(command.snapshots),
                    "labelled_jd_ids": tuple(sorted(labels)),
                    "positive_candidate_jd_ids": tuple(sorted(positive_candidate_jd_ids)),
                    "labeling_basis": labeling_basis,
                    "precision_at_k_process": (
                        "rank clusters by member count then cluster key; flatten members; "
                        "Precision@K = labelled positives in top K / returned top K"
                    ),
                    "stability_process": (
                        "repeat deterministic clustering under threshold/weight perturbations; "
                        "combine cluster-count stability (0.4) and pairwise member consistency (0.6)"
                    ),
                },
                "algorithms": results,
            }
        )
        if not isinstance(value, FrozenDict):
            raise TypeError("offline evaluation result must be a JSON object")
        return value
