"""Explainable adjacent-window cluster evolution matching."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


DECISION_VERSION = "adjacent-window-lineage-v3"
DEFAULT_LINEAGE_CONFIG: dict[str, float] = {
    "member_overlap_weight": 0.40,
    "core_skill_overlap_weight": 0.30,
    "semantic_similarity_weight": 0.30,
    "match_threshold": 0.35,
}


@dataclass(frozen=True)
class ClusterLineageSpec:
    cluster_id: str
    members: frozenset[str]
    centroid: tuple[float, ...]
    skills: frozenset[str]
    window_id: str = "unavailable"
    identity_key: str | None = None


@dataclass(frozen=True)
class LineageScore:
    member_overlap: float
    core_skill_overlap: float
    semantic_center_similarity: float
    semantic_center_distance: float
    combined_score: float


@dataclass(frozen=True)
class LineageRelation:
    relation_type: str
    predecessor_cluster_id: str | None
    successor_cluster_id: str | None
    similarity_score: float
    evidence_cluster_ids: tuple[str, ...]
    score: LineageScore | None
    predecessor_window_id: str | None = None
    successor_window_id: str | None = None
    decision_version: str = DECISION_VERSION
    threshold: float = DEFAULT_LINEAGE_CONFIG["match_threshold"]
    decision_reason: str = ""


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _same_identity(left: ClusterLineageSpec, right: ClusterLineageSpec) -> bool:
    return bool(
        left.identity_key
        and right.identity_key
        and left.identity_key == right.identity_key
    )


def match_cluster_lineage(
    previous: list[ClusterLineageSpec],
    current: list[ClusterLineageSpec],
    threshold: float | None = None,
    config: Mapping[str, float] | None = None,
) -> list[LineageRelation]:
    merged = {**DEFAULT_LINEAGE_CONFIG, **(config or {})}
    if threshold is not None:
        merged["match_threshold"] = threshold
    weights = (
        float(merged["member_overlap_weight"]),
        float(merged["core_skill_overlap_weight"]),
        float(merged["semantic_similarity_weight"]),
    )
    if abs(sum(weights) - 1.0) > 1e-9 or any(value < 0 for value in weights):
        raise ValueError("lineage weights must be non-negative and sum to one")
    boundary = float(merged["match_threshold"])
    if not 0 <= boundary <= 1:
        raise ValueError("lineage match threshold must be between zero and one")

    scores: dict[tuple[str, str], LineageScore] = {}
    for before in previous:
        for after in current:
            member = _jaccard(before.members, after.members)
            skill = _jaccard(before.skills, after.skills)
            semantic = max(0.0, _cosine(before.centroid, after.centroid))
            combined = weights[0] * member + weights[1] * skill + weights[2] * semantic
            scores[(before.cluster_id, after.cluster_id)] = LineageScore(
                round(member, 6),
                round(skill, 6),
                round(semantic, 6),
                round(1.0 - semantic, 6),
                round(combined, 6),
            )

    before_matches = {
        before.cluster_id: [
            after.cluster_id
            for after in current
            if (
                scores[(before.cluster_id, after.cluster_id)].combined_score >= boundary
                or _same_identity(before, after)
            )
        ]
        for before in previous
    }
    after_matches = {
        after.cluster_id: [
            before.cluster_id
            for before in previous
            if (
                scores[(before.cluster_id, after.cluster_id)].combined_score >= boundary
                or _same_identity(before, after)
            )
        ]
        for after in current
    }
    before_by_id = {item.cluster_id: item for item in previous}
    after_by_id = {item.cluster_id: item for item in current}
    consumed_before: set[str] = set()
    consumed_after: set[str] = set()
    relations: list[LineageRelation] = []

    for after_id in sorted(after_matches):
        predecessors = sorted(after_matches[after_id])
        if len(predecessors) <= 1:
            continue
        primary = max(
            predecessors,
            key=lambda before_id: (
                scores[(before_id, after_id)].combined_score,
                before_id,
            ),
        )
        relations.append(
            _relation(
                "merge",
                before_by_id[primary],
                after_by_id[after_id],
                predecessors,
                scores[(primary, after_id)],
                boundary,
            )
        )
        for before_id in predecessors:
            if before_id != primary:
                relations.append(
                    _relation(
                        "absorbed",
                        before_by_id[before_id],
                        after_by_id[after_id],
                        predecessors,
                        scores[(before_id, after_id)],
                        boundary,
                    )
                )
        consumed_before.update(predecessors)
        consumed_after.add(after_id)

    for before_id in sorted(before_matches):
        successors = [
            value for value in sorted(before_matches[before_id]) if value not in consumed_after
        ]
        if len(successors) <= 1 or before_id in consumed_before:
            continue
        for after_id in successors:
            relations.append(
                _relation(
                    "split",
                    before_by_id[before_id],
                    after_by_id[after_id],
                    successors,
                    scores[(before_id, after_id)],
                    boundary,
                )
            )
        consumed_before.add(before_id)
        consumed_after.update(successors)

    for before_id in sorted(before_matches):
        if before_id in consumed_before:
            continue
        successors = [value for value in before_matches[before_id] if value not in consumed_after]
        if len(successors) == 1 and len(after_matches[successors[0]]) == 1:
            after_id = successors[0]
            relations.append(
                _relation(
                    "continue",
                    before_by_id[before_id],
                    after_by_id[after_id],
                    [before_id, after_id],
                    scores[(before_id, after_id)],
                    boundary,
                )
            )
            consumed_before.add(before_id)
            consumed_after.add(after_id)

    for before in sorted(previous, key=lambda item: item.cluster_id):
        if before.cluster_id not in consumed_before:
            relations.append(
                _relation("decline", before, None, [before.cluster_id], None, boundary)
            )
    for after in sorted(current, key=lambda item: item.cluster_id):
        if after.cluster_id not in consumed_after:
            relations.append(_relation("birth", None, after, [after.cluster_id], None, boundary))
    return relations


def _relation(
    event: str,
    before: ClusterLineageSpec | None,
    after: ClusterLineageSpec | None,
    evidence_cluster_ids: list[str],
    score: LineageScore | None,
    threshold: float,
) -> LineageRelation:
    reason = {
        "birth": "no predecessor reached the configured matching threshold",
        "continue": "exactly one predecessor and one successor reached the threshold",
        "split": "one predecessor reached the threshold for multiple successors",
        "merge": "multiple predecessors reached one successor; this is the strongest edge",
        "absorbed": "a non-primary predecessor was incorporated into a merged successor",
        "decline": "no successor reached the configured matching threshold",
    }[event]
    if (
        event == "continue"
        and before is not None
        and after is not None
        and _same_identity(before, after)
        and score is not None
        and score.combined_score < threshold
    ):
        reason = "stable occupation identity matched across incompatible vector spaces"
    return LineageRelation(
        relation_type=event,
        predecessor_cluster_id=before.cluster_id if before else None,
        successor_cluster_id=after.cluster_id if after else None,
        similarity_score=score.combined_score if score else 0.0,
        evidence_cluster_ids=tuple(evidence_cluster_ids),
        score=score,
        predecessor_window_id=before.window_id if before else None,
        successor_window_id=after.window_id if after else None,
        threshold=threshold,
        decision_reason=reason,
    )
