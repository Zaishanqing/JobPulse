"""Simple evidence-gated multi-view candidate discovery."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.domain.discovery import AlgorithmCluster, JDSnapshot
from app.domain.values import FrozenDict, JsonObject
from app.infrastructure.semantic_embeddings import SemanticProviderUnavailable


@dataclass(frozen=True)
class MultiViewResult:
    embeddings: tuple[tuple[float, ...], ...]
    clusters: tuple[AlgorithmCluster, ...]
    metadata: JsonObject


def _tokens(text: str) -> list[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]{2,}", lowered)
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    words.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return words


def _tfidf(texts: tuple[str, ...], *, dimensions: int = 128) -> tuple[tuple[float, ...], ...]:
    documents = [_tokens(value) for value in texts]
    document_token_sets = [set(document) for document in documents]
    vocabulary = sorted({token for tokens in document_token_sets for token in tokens})
    frequencies = Counter(token for tokens in document_token_sets for token in tokens)
    columns = {token: index % dimensions for index, token in enumerate(vocabulary)}
    if not vocabulary:
        return tuple((0.0,) * dimensions for _ in texts)
    matrix = np.zeros((len(texts), dimensions), dtype=float)
    for row, document in enumerate(documents):
        counts = Counter(document)
        for token in vocabulary:
            if counts[token]:
                frequency = frequencies[token]
                column = columns[token]
                matrix[row, column] += (counts[token] / max(len(document), 1)) * (
                    math.log((1 + len(documents)) / (1 + frequency)) + 1
                )
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1, norms)
    return tuple(tuple(float(value) for value in row) for row in matrix.round(12))


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _skills(snapshot: JDSnapshot) -> set[str]:
    return {
        str(item.identity).strip().casefold()
        for item in snapshot.structured_data.required_skills + snapshot.structured_data.bonus_skills
        if item.identity and str(item.identity).strip()
    }


def _skill_similarity(left: JDSnapshot, right: JDSnapshot) -> float:
    left_skills, right_skills = _skills(left), _skills(right)
    union = left_skills | right_skills
    return len(left_skills & right_skills) / len(union) if union else 0.0


def _centroid(vectors: tuple[tuple[float, ...], ...], indices: list[int]) -> tuple[float, ...]:
    values = np.asarray([vectors[index] for index in indices], dtype=float)
    center = values.mean(axis=0)
    norm = np.linalg.norm(center)
    if norm:
        center = center / norm
    return tuple(float(value) for value in center.round(12))


def discover_multi_view(
    snapshots: tuple[JDSnapshot, ...],
    semantic_provider: Any,
    config: JsonObject,
    *,
    random_seed: int = 20260802,
) -> MultiViewResult:
    semantic_threshold = float(config.get("semantic_candidate_threshold", 0.72))
    skill_threshold = float(config.get("skill_cooccurrence_threshold", 0.50))
    responsibility_threshold = float(config.get("responsibility_similarity_threshold", 0.05))
    supporting_threshold = float(config.get("supporting_view_threshold", 0.20))
    min_cluster_size = int(config.get("minimum_cluster_size", 2))
    if min_cluster_size < 2:
        raise ValueError("minimum_cluster_size must be at least two")

    lexical = _tfidf(
        tuple(
            " ".join(
                (
                    item.title,
                    item.structured_data.position_title or "",
                    *item.structured_data.responsibilities,
                    *sorted(_skills(item)),
                )
            )
            for item in snapshots
        )
    )
    responsibilities = _tfidf(
        tuple(" ".join(item.structured_data.responsibilities) for item in snapshots)
    )
    semantic_status = "available"
    try:
        semantic = semantic_provider.embed(snapshots)
    except SemanticProviderUnavailable:
        if config.get("semantic_failure_mode", "mark_unavailable") == "fail":
            raise
        semantic = None
        semantic_status = "unavailable"

    parents = list(range(len(snapshots)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    accepted_edges: list[dict[str, Any]] = []
    candidate_counts = {
        "text_semantic": 0,
        "skill_set": 0,
        "responsibility_expression": 0,
    }
    for left in range(len(snapshots)):
        for right in range(left + 1, len(snapshots)):
            semantic_score = (
                _cosine(semantic[left], semantic[right]) if semantic is not None else None
            )
            skill_score = _skill_similarity(snapshots[left], snapshots[right])
            responsibility_score = _cosine(responsibilities[left], responsibilities[right])
            candidates = {
                "text_semantic": semantic_score is not None
                and semantic_score >= semantic_threshold,
                "skill_set": skill_score >= skill_threshold,
                "responsibility_expression": responsibility_score >= responsibility_threshold,
            }
            for name, accepted in candidates.items():
                candidate_counts[name] += int(accepted)
            supporting_views = sum(candidates.values())
            semantic_supported = bool(candidates["text_semantic"]) and (
                skill_score >= supporting_threshold or responsibility_score >= supporting_threshold
            )
            skill_set_fallback = (
                semantic_status == "unavailable"
                and candidates["skill_set"]
            )
            accepted = supporting_views >= 2 or semantic_supported or skill_set_fallback
            if not accepted:
                continue
            union(left, right)
            perturbed = 0.05
            robust_candidates = {
                "text_semantic": semantic_score is not None
                and semantic_score >= min(1.0, semantic_threshold + perturbed),
                "skill_set": skill_score >= min(1.0, skill_threshold + perturbed),
                "responsibility_expression": responsibility_score
                >= min(1.0, responsibility_threshold + perturbed),
            }
            robust = (
                sum(robust_candidates.values()) >= 2
                or bool(robust_candidates["text_semantic"])
                and (
                    skill_score >= min(1.0, supporting_threshold + perturbed)
                    or responsibility_score >= min(1.0, supporting_threshold + perturbed)
                )
                or (
                    semantic_status == "unavailable"
                    and robust_candidates["skill_set"]
                )
            )
            accepted_edges.append(
                {
                    "left_source_jd_id": snapshots[left].jd_id,
                    "right_source_jd_id": snapshots[right].jd_id,
                    "semantic_similarity": (
                        round(semantic_score, 6) if semantic_score is not None else "unavailable"
                    ),
                    "skill_cooccurrence": round(skill_score, 6),
                    "responsibility_consistency": round(responsibility_score, 6),
                    "supporting_views": tuple(
                        name for name, supported in candidates.items() if supported
                    ),
                    "robust_under_threshold_perturbation": robust,
                }
            )

    components: dict[int, list[int]] = {}
    for index in range(len(snapshots)):
        components.setdefault(find(index), []).append(index)

    working_vectors = semantic if semantic is not None else lexical
    clusters: list[AlgorithmCluster] = []
    for indices in sorted(components.values(), key=lambda values: snapshots[values[0]].jd_id):
        if len(indices) < min_cluster_size:
            continue
        members = tuple(snapshots[index] for index in indices)
        member_ids = {item.jd_id for item in members}
        edges = tuple(
            edge
            for edge in accepted_edges
            if edge["left_source_jd_id"] in member_ids and edge["right_source_jd_id"] in member_ids
        )
        skill_counts = Counter(skill for item in members for skill in _skills(item))
        core_skills = tuple(
            skill
            for skill, count in sorted(
                skill_counts.items(), key=lambda value: (-value[1], value[0])
            )
            if count >= math.ceil(len(members) / 2)
        )
        responsibility_counts = Counter(
            responsibility.strip()
            for item in members
            for responsibility in item.structured_data.responsibilities
            if responsibility.strip()
        )
        core_responsibilities = tuple(value for value, _ in responsibility_counts.most_common(5))
        sources = sorted(
            {source for edge in edges for source in edge["supporting_views"]}
            | {"tfidf_skill_baseline"}
        )
        edge_scores = [float(bool(edge["robust_under_threshold_perturbation"])) for edge in edges]
        key = min(member_ids)
        label = (
            " / ".join(core_skills[:3])
            or Counter(item.title for item in members).most_common(1)[0][0]
        )
        semantic_centroid = _centroid(semantic, indices) if semantic is not None else ()
        clusters.append(
            AlgorithmCluster(
                key=key,
                cluster_name=f"{label} 岗位簇",
                members=members,
                core_skills=core_skills,
                stability_score=round(sum(edge_scores) / len(edge_scores), 6),
                centroid=_centroid(working_vectors, indices),
                algorithm_version="evidence-multiview-v2",
                similarity_threshold=semantic_threshold,
                random_seed=random_seed,
                core_responsibilities=core_responsibilities,
                semantic_centroid=semantic_centroid,
                algorithm_sources=tuple(sources),
                merge_basis=FrozenDict(
                    {
                        "rule": (
                            "embedding_is_candidate_only; "
                            "merge_requires_two_views_or_semantic_plus_support; "
                            "formal_runs_require_semantic; skill_set_is_a_supporting_view"
                        ),
                        "semantic_status": semantic_status,
                        "thresholds": FrozenDict(
                            {
                                "semantic": semantic_threshold,
                                "skill": skill_threshold,
                                "responsibility": responsibility_threshold,
                                "supporting": supporting_threshold,
                            }
                        ),
                        "accepted_edges": tuple(FrozenDict(edge) for edge in edges),
                    }
                ),
            )
        )

    return MultiViewResult(
        embeddings=working_vectors,
        clusters=tuple(clusters),
        metadata=FrozenDict(
            {
                "semantic_status": semantic_status,
                "candidate_counts": FrozenDict(candidate_counts),
                "merge_rule_version": "evidence-multiview-merge-v1",
                "outcome": (
                    "supported_clusters"
                    if clusters
                    else "no_supported_cluster"
                ),
            }
        ),
    )
