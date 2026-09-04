"""Small registry for reproducible clustering comparisons."""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from app.application.contracts import (
    AlgorithmEvaluationResult,
    ComparisonClusterResult,
)
from app.domain.discovery import JDSnapshot
from app.domain.values import FrozenDict, JsonObject, freeze
from app.infrastructure.providers import (
    AgglomerativeClusteringAlgorithm,
    TfidfSvdSkillEmbeddingProvider,
)
from app.infrastructure.semantic_embeddings import (
    LocalChineseSemanticEmbeddingProvider,
)
from app.infrastructure.multi_view import discover_multi_view


@dataclass(frozen=True)
class AlgorithmProfile:
    name: str
    feature_name: str
    clustering_name: str
    defaults: JsonObject


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return max(
        -1.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True)) / denominator,
        ),
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _density_groups(
    embeddings: tuple[tuple[float, ...], ...],
    *,
    eps: float,
    min_samples: int,
) -> tuple[list[list[int]], list[int]]:
    neighbors = [
        [
            other
            for other in range(len(embeddings))
            if 1.0 - _cosine(vector, embeddings[other]) <= eps
        ]
        for vector in embeddings
    ]
    labels: list[int | None] = [None] * len(embeddings)
    visited: set[int] = set()
    cluster_id = 0
    for point in range(len(embeddings)):
        if point in visited:
            continue
        visited.add(point)
        if len(neighbors[point]) < min_samples:
            continue
        labels[point] = cluster_id
        queue = list(neighbors[point])
        cursor = 0
        while cursor < len(queue):
            candidate = queue[cursor]
            cursor += 1
            if candidate not in visited:
                visited.add(candidate)
                if len(neighbors[candidate]) >= min_samples:
                    for neighbor in neighbors[candidate]:
                        if neighbor not in queue:
                            queue.append(neighbor)
            if labels[candidate] is None:
                labels[candidate] = cluster_id
        cluster_id += 1
    groups = [
        [index for index, label in enumerate(labels) if label == value]
        for value in range(cluster_id)
    ]
    noise = [index for index, label in enumerate(labels) if label is None]
    return groups, noise


def _metrics(
    embeddings: tuple[tuple[float, ...], ...],
    groups: list[list[int]],
) -> tuple[float | None, float | None, float | None]:
    intra_pairs = [
        _cosine(embeddings[left], embeddings[right])
        for group in groups
        for offset, left in enumerate(group)
        for right in group[offset + 1 :]
    ]
    centroids = (
        [
            tuple(
                sum(embeddings[index][column] for index in group) / len(group)
                for column in range(len(embeddings[0]))
            )
            for group in groups
            if group
        ]
        if embeddings
        else []
    )
    inter_pairs = [
        1.0 - _cosine(left, right)
        for offset, left in enumerate(centroids)
        for right in centroids[offset + 1 :]
    ]
    silhouettes: list[float] = []
    if len(groups) >= 2:
        for group_index, group in enumerate(groups):
            for point in group:
                same = [item for item in group if item != point]
                if not same:
                    silhouettes.append(0.0)
                    continue
                a = sum(1.0 - _cosine(embeddings[point], embeddings[item]) for item in same) / len(
                    same
                )
                b = min(
                    sum(1.0 - _cosine(embeddings[point], embeddings[item]) for item in other)
                    / len(other)
                    for index, other in enumerate(groups)
                    if index != group_index and other
                )
                denominator = max(a, b)
                silhouettes.append((b - a) / denominator if denominator else 0.0)
    return _rounded(_mean(silhouettes)), _rounded(_mean(intra_pairs)), _rounded(_mean(inter_pairs))


def _json_object(value: dict) -> JsonObject:
    frozen = freeze(value)
    if not isinstance(frozen, FrozenDict):
        raise TypeError("evaluation value must be a JSON object")
    return frozen


def _enterprise(snapshot: JDSnapshot) -> str | None:
    for key in ("enterprise_id", "company_id", "company_name"):
        value = snapshot.structured_data.extensions.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _hhi(weights: list[float], enterprises: list[str]) -> float:
    total = sum(weights)
    if total <= 0:
        return 0.0
    totals = {
        enterprise: sum(
            weight
            for weight, candidate in zip(weights, enterprises, strict=True)
            if candidate == enterprise
        )
        for enterprise in set(enterprises)
    }
    return round(sum((value / total) ** 2 for value in totals.values()), 6)


def _membership_consistency(
    base_groups: list[list[int]],
    base_snapshots: tuple[JDSnapshot, ...],
    candidate_groups: list[list[int]],
    candidate_snapshots: tuple[JDSnapshot, ...],
) -> float:
    common = sorted(
        {item.jd_id for item in base_snapshots} & {item.jd_id for item in candidate_snapshots}
    )
    if len(common) < 2:
        return 1.0

    def pairs(groups, snapshots):
        return {
            frozenset((snapshots[left].jd_id, snapshots[right].jd_id))
            for group in groups
            for offset, left in enumerate(group)
            for right in group[offset + 1 :]
        }

    base_pairs = pairs(base_groups, base_snapshots)
    candidate_pairs = pairs(candidate_groups, candidate_snapshots)
    comparisons = [
        frozenset((left, right))
        for offset, left in enumerate(common)
        for right in common[offset + 1 :]
    ]
    return round(
        sum((pair in base_pairs) == (pair in candidate_pairs) for pair in comparisons)
        / len(comparisons),
        6,
    )


class AlgorithmRegistry:
    def __init__(
        self,
        semantic_provider: LocalChineseSemanticEmbeddingProvider | None = None,
    ) -> None:
        self.semantic_provider = semantic_provider or LocalChineseSemanticEmbeddingProvider()
        common = {
            "enterprise_max_sample_ratio": 0.5,
            "enterprise_similarity_threshold": 0.9,
            "stability_perturbation": 0.05,
        }
        self._profiles = {
            "baseline": AlgorithmProfile(
                "baseline",
                "tfidf-svd-v1:text=1.0,skill=0.0",
                "agglomerative-average-link",
                FrozenDict(
                    {
                        "similarity_threshold": 0.55,
                        "text_weight": 1.0,
                        **common,
                    }
                ),
            ),
            "multi_view": AlgorithmProfile(
                "multi_view",
                "semantic+skill-cooccurrence+responsibility-tfidf-v1",
                "evidence-gated-multi-view",
                FrozenDict(
                    {
                        "similarity_threshold": 0.72,
                        "text_weight": 0.5,
                        "semantic_candidate_threshold": 0.72,
                        "skill_cooccurrence_threshold": 0.5,
                        "responsibility_similarity_threshold": 0.05,
                        "supporting_view_threshold": 0.2,
                        "semantic_failure_mode": "mark_unavailable",
                        **common,
                    }
                ),
            ),
            "fused_agglomerative": AlgorithmProfile(
                "fused_agglomerative",
                "tfidf-svd-skill-v1:text=0.5,skill=0.5",
                "agglomerative-average-link",
                FrozenDict(
                    {
                        "similarity_threshold": 0.55,
                        "text_weight": 0.5,
                        **common,
                    }
                ),
            ),
            "density_noise": AlgorithmProfile(
                "density_noise",
                "tfidf-svd-skill-v1:text=0.7,skill=0.3",
                "cosine-density-dbscan",
                FrozenDict(
                    {
                        "eps": 0.3,
                        "min_samples": 2,
                        "text_weight": 0.7,
                        **common,
                    }
                ),
            ),
            "semantic_agglomerative": AlgorithmProfile(
                "semantic_agglomerative",
                "local-chinese-semantic-v1",
                "agglomerative-average-link",
                FrozenDict(
                    {
                        "similarity_threshold": 0.55,
                        "text_weight": 0.7,
                        **common,
                    }
                ),
            ),
            "semantic_fused_agglomerative": AlgorithmProfile(
                "semantic_fused_agglomerative",
                "local-semantic+tfidf-svd-skill-v1",
                "agglomerative-average-link",
                FrozenDict(
                    {
                        "similarity_threshold": 0.55,
                        "text_weight": 0.7,
                        "semantic_weight": 0.5,
                        **common,
                    }
                ),
            ),
        }

    def names(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def profiles(self) -> tuple[AlgorithmProfile, ...]:
        return tuple(self._profiles.values())

    @staticmethod
    def _validate_parameters(profile: AlgorithmProfile, resolved: JsonObject) -> None:
        text_weight = float(resolved["text_weight"])
        cap = float(resolved["enterprise_max_sample_ratio"])
        similarity = float(resolved["enterprise_similarity_threshold"])
        perturbation = float(resolved["stability_perturbation"])
        if not 0.0 <= text_weight <= 1.0:
            raise ValueError("text_weight must be between zero and one")
        if "semantic_weight" in resolved:
            semantic_weight = float(resolved["semantic_weight"])
            if not 0.0 <= semantic_weight <= 1.0:
                raise ValueError("semantic_weight must be between zero and one")
        if not 0.0 < cap <= 1.0:
            raise ValueError("enterprise_max_sample_ratio must be above zero and at most one")
        if not 0.0 <= similarity <= 1.0:
            raise ValueError("enterprise_similarity_threshold must be between zero and one")
        if not 0.0 < perturbation <= 0.25:
            raise ValueError("stability_perturbation must be above zero and at most 0.25")
        if profile.clustering_name == "cosine-density-dbscan":
            eps = float(resolved["eps"])
            min_samples = float(resolved["min_samples"])
            if not 0.0 <= eps <= 1.0:
                raise ValueError("eps must be between zero and one")
            if not min_samples.is_integer() or min_samples < 2:
                raise ValueError("min_samples must be an integer of at least two")
        else:
            threshold = float(resolved["similarity_threshold"])
            if not 0.0 <= threshold <= 1.0:
                raise ValueError("similarity_threshold must be between zero and one")
        if profile.name == "multi_view":
            for name in (
                "semantic_candidate_threshold",
                "skill_cooccurrence_threshold",
                "responsibility_similarity_threshold",
                "supporting_view_threshold",
            ):
                if not 0.0 <= float(resolved[name]) <= 1.0:
                    raise ValueError(f"{name} must be between zero and one")

    def _cluster(
        self,
        profile: AlgorithmProfile,
        snapshots: tuple[JDSnapshot, ...],
        parameters: JsonObject,
    ) -> tuple[tuple[tuple[float, ...], ...], list[list[int]], list[int]]:
        baseline = TfidfSvdSkillEmbeddingProvider(
            text_weight=float(parameters["text_weight"])
        ).embed(snapshots)
        embeddings = baseline
        if profile.name == "multi_view":
            discovered = discover_multi_view(
                snapshots,
                self.semantic_provider,
                parameters,
            )
            index_by_jd = {item.jd_id: index for index, item in enumerate(snapshots)}
            groups = [
                [index_by_jd[member.jd_id] for member in cluster.members]
                for cluster in discovered.clusters
            ]
            return discovered.embeddings, groups, []
        if profile.name == "semantic_agglomerative":
            embeddings = self.semantic_provider.embed(snapshots)
        elif profile.name == "semantic_fused_agglomerative":
            semantic = np.asarray(self.semantic_provider.embed(snapshots), dtype=float)
            lexical = np.asarray(baseline, dtype=float)
            semantic_weight = float(parameters["semantic_weight"])
            fused = np.concatenate(
                (
                    lexical * (1.0 - semantic_weight),
                    semantic * semantic_weight,
                ),
                axis=1,
            )
            norms = np.linalg.norm(fused, axis=1, keepdims=True)
            fused = fused / np.where(norms == 0, 1, norms)
            embeddings = tuple(tuple(float(value) for value in row) for row in fused.round(12))
        if profile.clustering_name == "cosine-density-dbscan":
            groups, noise = _density_groups(
                embeddings,
                eps=float(parameters["eps"]),
                min_samples=int(parameters["min_samples"]),
            )
            return embeddings, groups, noise
        clustered = AgglomerativeClusteringAlgorithm(
            float(parameters["similarity_threshold"])
        ).cluster(snapshots, embeddings)
        index_by_jd = {item.jd_id: index for index, item in enumerate(snapshots)}
        groups = [
            [index_by_jd[member.jd_id] for member in cluster.members] for cluster in clustered
        ]
        return embeddings, groups, []

    def _enterprise_analysis(
        self,
        profile: AlgorithmProfile,
        snapshots: tuple[JDSnapshot, ...],
        embeddings: tuple[tuple[float, ...], ...],
        groups: list[list[int]],
        parameters: JsonObject,
    ) -> JsonObject:
        enterprises = [_enterprise(item) for item in snapshots]
        if not enterprises or any(item is None for item in enterprises):
            return _json_object(
                {
                    "status": "unavailable",
                    "reason": "enterprise field is unavailable for one or more JD snapshots",
                }
            )
        known = [str(item) for item in enterprises]
        weights = [1.0] * len(snapshots)
        threshold = float(parameters["enterprise_similarity_threshold"])
        similar_group_count = 0
        for enterprise in sorted(set(known)):
            pending = [index for index, value in enumerate(known) if value == enterprise]
            while pending:
                representative = pending.pop(0)
                related = [
                    candidate
                    for candidate in pending
                    if _cosine(embeddings[representative], embeddings[candidate]) >= threshold
                ]
                for candidate in related:
                    pending.remove(candidate)
                group = [representative, *related]
                if len(group) > 1:
                    similar_group_count += 1
                for index in group:
                    weights[index] = 1.0 / len(group)
        cap = float(parameters["enterprise_max_sample_ratio"])
        for _ in range(12):
            changed = False
            for enterprise in sorted(set(known)):
                company = sum(
                    weight
                    for weight, value in zip(weights, known, strict=True)
                    if value == enterprise
                )
                others = sum(weights) - company
                if others <= 0 or company / (company + others) <= cap:
                    continue
                target = cap / (1.0 - cap) * others if cap < 1.0 else company
                scale = target / company
                weights = [
                    weight * scale if value == enterprise else weight
                    for weight, value in zip(weights, known, strict=True)
                ]
                changed = True
            if not changed:
                break
        counts = {value: known.count(value) for value in set(known)}
        top = min(
            (value for value, count in counts.items() if count == max(counts.values())),
            default=None,
        )
        remaining = tuple(
            item for item, enterprise in zip(snapshots, known, strict=True) if enterprise != top
        )
        robustness: float | None = None
        remaining_cluster_count: int | None = None
        if remaining:
            try:
                _, reduced_groups, _ = self._cluster(profile, remaining, parameters)
            except ValueError as exc:
                if "did not produce a supported cluster" not in str(exc):
                    raise
                remaining_cluster_count = 0
            else:
                robustness = _membership_consistency(
                    groups, snapshots, reduced_groups, remaining
                )
                remaining_cluster_count = len(reduced_groups)
        total_weight = sum(weights)
        weighted_totals = {
            enterprise: sum(
                weight for weight, value in zip(weights, known, strict=True) if value == enterprise
            )
            for enterprise in set(known)
        }
        return _json_object(
            {
                "status": "applied",
                "enterprise_count": len(set(known)),
                "max_sample_ratio": cap,
                "similarity_threshold": threshold,
                "similar_group_count": similar_group_count,
                "concentration_before": _hhi([1.0] * len(known), known),
                "concentration_after": _hhi(weights, known),
                "top_enterprise": top,
                "top_enterprise_share_before": round(counts[top] / len(known), 6),
                "top_enterprise_share_after": round(weighted_totals[top] / total_weight, 6),
                "effective_sample_weight": round(total_weight, 6),
                "sample_weights": {
                    snapshot.jd_id: round(weight, 6)
                    for snapshot, weight in zip(snapshots, weights, strict=True)
                },
                "without_top_enterprise": {
                    "remaining_sample_count": len(remaining),
                    "cluster_count": remaining_cluster_count,
                    "member_consistency": robustness,
                },
            }
        )

    def _sensitivity(
        self,
        profile: AlgorithmProfile,
        snapshots: tuple[JDSnapshot, ...],
        base_groups: list[list[int]],
        parameters: JsonObject,
    ) -> tuple[JsonObject, ...]:
        delta = float(parameters["stability_perturbation"])
        parameter_values: dict[str, list[float | int]] = {
            "text_weight": sorted(
                {
                    round(max(0.0, float(parameters["text_weight"]) - delta), 6),
                    float(parameters["text_weight"]),
                    round(min(1.0, float(parameters["text_weight"]) + delta), 6),
                }
            ),
            "enterprise_max_sample_ratio": sorted(
                {
                    round(max(0.1, float(parameters["enterprise_max_sample_ratio"]) - 0.1), 6),
                    float(parameters["enterprise_max_sample_ratio"]),
                    round(min(1.0, float(parameters["enterprise_max_sample_ratio"]) + 0.1), 6),
                }
            ),
        }
        if profile.name == "multi_view":
            parameter_values = {
                name: sorted(
                    {
                        round(max(0.0, float(parameters[name]) - delta), 6),
                        float(parameters[name]),
                        round(min(1.0, float(parameters[name]) + delta), 6),
                    }
                )
                for name in (
                    "skill_cooccurrence_threshold",
                    "responsibility_similarity_threshold",
                    "supporting_view_threshold",
                )
            }
        if "semantic_weight" in parameters:
            parameter_values["semantic_weight"] = sorted(
                {
                    round(max(0.0, float(parameters["semantic_weight"]) - delta), 6),
                    float(parameters["semantic_weight"]),
                    round(min(1.0, float(parameters["semantic_weight"]) + delta), 6),
                }
            )
        if profile.clustering_name == "cosine-density-dbscan":
            parameter_values["eps"] = sorted(
                {
                    round(max(0.0, float(parameters["eps"]) - delta), 6),
                    float(parameters["eps"]),
                    round(min(1.0, float(parameters["eps"]) + delta), 6),
                }
            )
            minimum = int(parameters["min_samples"])
            parameter_values["min_samples"] = sorted({max(2, minimum - 1), minimum, minimum + 1})
        else:
            parameter_values["similarity_threshold"] = sorted(
                {
                    round(max(0.0, float(parameters["similarity_threshold"]) - delta), 6),
                    float(parameters["similarity_threshold"]),
                    round(min(1.0, float(parameters["similarity_threshold"]) + delta), 6),
                }
            )
        results = []
        for parameter, values in parameter_values.items():
            cluster_counts = []
            noise_ratios = []
            consistencies = []
            concentrations = []
            for value in values:
                variant = FrozenDict({**dict(parameters), parameter: value})
                embeddings, groups, noise = self._cluster(profile, snapshots, variant)
                cluster_counts.append(len(groups))
                noise_ratios.append(round(len(noise) / len(snapshots), 6))
                consistencies.append(
                    _membership_consistency(base_groups, snapshots, groups, snapshots)
                )
                enterprise = self._enterprise_analysis(
                    profile, snapshots, embeddings, groups, variant
                )
                concentrations.append(enterprise.get("concentration_after"))
            results.append(
                _json_object(
                    {
                        "parameter": parameter,
                        "tested_values": values,
                        "cluster_counts": cluster_counts,
                        "noise_ratios": noise_ratios,
                        "member_consistency": consistencies,
                        "enterprise_concentration": concentrations,
                    }
                )
            )
        return tuple(results)

    def evaluate(
        self,
        algorithm: str,
        snapshots: tuple[JDSnapshot, ...],
        parameters: JsonObject,
    ) -> AlgorithmEvaluationResult:
        profile = self._profiles.get(algorithm)
        if profile is None:
            raise ValueError(f"unsupported comparison algorithm: {algorithm}")
        unknown = set(parameters) - set(profile.defaults)
        if unknown:
            raise ValueError(f"unsupported parameter for {algorithm}: {sorted(unknown)[0]}")
        resolved = FrozenDict({**dict(profile.defaults), **dict(parameters)})
        self._validate_parameters(profile, resolved)
        started = perf_counter()
        embeddings, groups, noise = self._cluster(profile, snapshots, resolved)
        silhouette, intra, inter = _metrics(embeddings, groups)
        enterprise = self._enterprise_analysis(profile, snapshots, embeddings, groups, resolved)
        sensitivity = self._sensitivity(profile, snapshots, groups, resolved)
        consistency_values = [
            float(value)
            for item in sensitivity
            if item["parameter"] != "enterprise_max_sample_ratio"
            for value in item["member_consistency"]
        ]
        cluster_counts = [
            int(value)
            for item in sensitivity
            if item["parameter"] != "enterprise_max_sample_ratio"
            for value in item["cluster_counts"]
        ]
        count_range = max(cluster_counts) - min(cluster_counts) if cluster_counts else 0
        count_stability = 1.0 - min(count_range / max(len(groups), 1), 1.0)
        member_consistency = _mean(consistency_values) or 0.0
        stability_score = round(0.4 * count_stability + 0.6 * member_consistency, 6)
        stability = _json_object(
            {
                "method": "deterministic-parameter-perturbation-v1",
                "run_count": len(consistency_values),
                "cluster_count_min": min(cluster_counts) if cluster_counts else len(groups),
                "cluster_count_max": max(cluster_counts) if cluster_counts else len(groups),
                "cluster_count_range": count_range,
                "member_consistency": round(member_consistency, 6),
                "stability_score": stability_score,
            }
        )
        enterprise_robustness = 0.5
        enterprise_diversity = 0.5
        if enterprise.get("status") == "applied":
            without_top = enterprise["without_top_enterprise"]
            if isinstance(without_top, FrozenDict):
                value = without_top.get("member_consistency")
                if value is not None:
                    enterprise_robustness = float(value)
            enterprise_diversity = 1.0 - float(enterprise["concentration_after"])
        raw_recommendation_score = (
            0.2 * ((silhouette + 1.0) / 2.0 if silhouette is not None else 0.5)
            + 0.2 * ((intra + 1.0) / 2.0 if intra is not None else 0.5)
            + 0.15 * (min(max(inter / 2.0, 0.0), 1.0) if inter is not None else 0.5)
            + 0.25 * stability_score
            + 0.1 * (1.0 - len(noise) / len(snapshots))
            + 0.1 * ((enterprise_robustness + enterprise_diversity) / 2.0)
        )
        recommendation_score = round(min(max(raw_recommendation_score, 0.0), 1.0), 6)
        runtime_ms = round((perf_counter() - started) * 1000.0, 3)
        clusters = tuple(
            ComparisonClusterResult(
                cluster_key=min(snapshots[index].jd_id for index in group),
                member_jd_ids=tuple(sorted(snapshots[index].jd_id for index in group)),
                member_fact_ids=tuple(sorted(snapshots[index].source_fact_id for index in group)),
            )
            for group in groups
        )
        noise_points = tuple(
            FrozenDict(
                {
                    "jd_id": snapshots[index].jd_id,
                    "source_fact_id": snapshots[index].source_fact_id,
                    "source_fact_version": snapshots[index].source_fact_version,
                }
            )
            for index in noise
        )
        return AlgorithmEvaluationResult(
            algorithm=profile.name,
            feature_name=profile.feature_name,
            clustering_name=profile.clustering_name,
            parameters=resolved,
            cluster_count=len(groups),
            noise_ratio=round(len(noise) / len(snapshots), 6),
            silhouette_coefficient=silhouette,
            intra_cluster_similarity=intra,
            inter_cluster_difference=inter,
            runtime_ms=runtime_ms,
            clusters=clusters,
            noise_points=noise_points,
            enterprise_debias=enterprise,
            stability_analysis=stability,
            parameter_sensitivity=sensitivity,
            recommendation_score=recommendation_score,
        )
