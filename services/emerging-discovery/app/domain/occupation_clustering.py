"""Deterministic occupation clusters used by the formal EMERGE chain.

The frozen EMERGE v3.2 experiment defines a JD as an observation and an
occupation cluster as the Stage-2 analysis unit.  Semantic similarity remains
part of Stage-1 mature-reference comparison; it must not transitively merge
different occupations merely because they share generic terms such as
``大模型`` or ``Python``.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict

import numpy as np

from app.domain.discovery import AlgorithmCluster, JDSnapshot
from app.domain.values import FrozenDict


_ROLE_SUFFIXES = (
    "工程师",
    "专家",
    "开发",
    "研发",
    "实习生",
    "算法工程师",
    "开发工程师",
    "研发工程师",
    "算法专家",
    "开发专家",
    "岗",
)
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def occupation_key(title: str) -> str:
    """Return the exact occupation identity frozen by EXP-EMERGE-01 v3.2."""
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = _PAREN_RE.sub("", text)
    text = re.sub(r"[\s\-_·｜|/\\:：,，。.]+", "", text)
    for suffix in _ROLE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text or unicodedata.normalize("NFKC", title or "").casefold()


def _skills(snapshot: JDSnapshot) -> set[str]:
    return {
        str(item.identity).strip().casefold()
        for item in snapshot.structured_data.required_skills
        + snapshot.structured_data.bonus_skills
        if item.identity and str(item.identity).strip()
    }


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def cluster_by_occupation(
    snapshots: tuple[JDSnapshot, ...],
    embeddings: tuple[tuple[float, ...], ...],
    *,
    random_seed: int = 20260823,
) -> tuple[AlgorithmCluster, ...]:
    """Group every observation by the frozen title-derived occupation key."""
    if len(snapshots) != len(embeddings):
        raise ValueError("embedding count must equal snapshot count")
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, snapshot in enumerate(snapshots):
        grouped[occupation_key(snapshot.title)].append(index)

    clusters: list[AlgorithmCluster] = []
    for key, indices in sorted(grouped.items()):
        members = tuple(snapshots[index] for index in indices)
        title = Counter(member.title for member in members).most_common(1)[0][0]
        skill_counts = Counter(skill for member in members for skill in _skills(member))
        core_skills = tuple(
            skill
            for skill, count in sorted(skill_counts.items(), key=lambda item: (-item[1], item[0]))
            if count >= math.ceil(len(members) / 2)
        )
        responsibility_counts = Counter(
            value.strip()
            for member in members
            for value in member.structured_data.responsibilities
            if value.strip()
        )
        vectors = [embeddings[index] for index in indices]
        centroid_array = np.mean(vectors, axis=0)
        centroid_norm = np.linalg.norm(centroid_array)
        if centroid_norm:
            centroid_array = centroid_array / centroid_norm
        pair_scores = [
            _cosine(embeddings[left], embeddings[right])
            for offset, left in enumerate(indices)
            for right in indices[offset + 1 :]
        ]
        clusters.append(
            AlgorithmCluster(
                key=key,
                cluster_name=title,
                members=members,
                core_skills=core_skills,
                stability_score=(
                    round(sum(pair_scores) / len(pair_scores), 6) if pair_scores else 1.0
                ),
                centroid=tuple(float(value) for value in centroid_array.round(12)),
                algorithm_version="occupation-title-key-v1",
                similarity_threshold=1.0,
                random_seed=random_seed,
                core_responsibilities=tuple(
                    value for value, _ in responsibility_counts.most_common(5)
                ),
                algorithm_sources=("occupation_title_key",),
                merge_basis=FrozenDict(
                    {
                        "rule": "exact_frozen_occupation_key",
                        "occupation_key": key,
                        "semantic_role": "stage1_mature_reference_comparison_only",
                    }
                ),
            )
        )
    return tuple(clusters)


__all__ = ["cluster_by_occupation", "occupation_key"]
