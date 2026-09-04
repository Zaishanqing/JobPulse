from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, replace
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    AblationResult,
    AblationType,
    CertificateStatus,
    ConclusionRecomputePort,
    ConclusionScore,
    ConstrainedAgglomerationRules,
    DistributionEntry,
    EvidenceAggregationResult,
    EvidenceIndependenceSummary,
    EvidenceRecord,
    IndependenceRequest,
    IndependenceWeightRules,
    MassProjectionCertificate,
    MassCappedAggregationRules,
    MissingAwareScoringRules,
    PairDecision,
    ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
    RobustAggregationRules,
    SourceAwareClusteringRules,
    UncertaintyState,
)
from app.contexts.evidence_independence.temporal import (
    TEMPORAL_FRESHNESS_VERSION,
    TIME_PROVENANCE_POLICY,
    TemporalFreshnessCertificate,
    TemporalFreshnessError,
    TemporalFreshnessRules,
    all_clusters_stale,
    build_temporal_freshness,
    cluster_staleness_states,
    derive_temporal_reasons,
)


INDEPENDENCE_WEIGHT_RULES_V1 = IndependenceWeightRules()

_WHITESPACE = re.compile(r"\s+")


def text_fingerprint(text: str) -> str:
    """Deterministic content fingerprint used by the independence graph."""

    normalized = _WHITESPACE.sub(" ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalized_url(value: str) -> str:
    """Normalize URL identity while preserving query parameters."""

    parts = urlsplit(value.strip())
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if scheme == "http" and port == 80:
        port = None
    if scheme == "https" and port == 443:
        port = None
    netloc = host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/") if parts.path and parts.path != "/" else parts.path
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def build_independent_clusters(
    records: Sequence[EvidenceRecord],
    near_duplicate_window_days: int = 7,
) -> tuple[tuple[str, ...], ...]:
    """Connect records that represent the same hiring event.

    The graph uses normalized URL equality, text fingerprint equality,
    template cluster membership, and enterprise+position+publish-window
    near-duplicates. Connected components are returned in stable order.
    """

    _require_unique_evidence_ids(records)
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for index, record in enumerate(records):
        for other_index in range(index + 1, len(records)):
            other = records[other_index]
            if _related(record, other, near_duplicate_window_days):
                union(index, other_index)

    grouped: dict[int, list[str]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record.evidence_id)
    clusters = [tuple(sorted(evidence_ids)) for evidence_ids in grouped.values()]
    return tuple(sorted(clusters, key=lambda cluster: cluster[0]))


def build_independent_clusters_v3(
    records: Sequence[EvidenceRecord],
    near_duplicate_window_days: int = 7,
    rules: SourceAwareClusteringRules = SourceAwareClusteringRules(),
) -> tuple[tuple[str, ...], ...]:
    """Source-aware constrained clustering for ``evidence-independence.v3``.

    Template and semantic similarity can nominate a duplicate candidate but
    cannot merge across independent enterprises/sources by itself.  Unions are
    checked against the whole cluster compatibility set to avoid the
    single-link chaining effect (A~B, B~C must not silently absorb C).
    """

    _require_unique_evidence_ids(records)
    parent = list(range(len(records)))
    cluster_members: dict[int, set[int]] = {
        index: {index} for index in range(len(records))
    }

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def compatible_pair(left: EvidenceRecord, right: EvidenceRecord) -> bool:
        if _strong_identity(left, right):
            return True
        same_company = _same_optional(left.enterprise_id, right.enterprise_id)
        same_source = _same_optional(left.source_id, right.source_id)
        return same_company or same_source

    def compatible_clusters(
        left_root: int, right_root: int
    ) -> bool:
        if left_root == right_root:
            return True
        return all(
            compatible_pair(records[left_index], records[right_index])
            for left_index in cluster_members[left_root]
            for right_index in cluster_members[right_root]
        )

    def union(left_index: int, right_index: int) -> bool:
        root_left = find(left_index)
        root_right = find(right_index)
        if root_left == root_right:
            return True
        if not compatible_clusters(root_left, root_right):
            return False
        if len(cluster_members[root_left]) < len(cluster_members[root_right]):
            root_left, root_right = root_right, root_left
        parent[root_right] = root_left
        cluster_members[root_left].update(cluster_members[root_right])
        del cluster_members[root_right]
        return True

    for index, record in enumerate(records):
        for other_index in range(index + 1, len(records)):
            other = records[other_index]
            score = _source_aware_pair_score(
                record, other, near_duplicate_window_days, rules
            )
            if _strong_identity(record, other) or score >= rules.merge_threshold:
                union(index, other_index)

    grouped: dict[int, list[str]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record.evidence_id)
    clusters = [tuple(sorted(evidence_ids)) for evidence_ids in grouped.values()]
    return tuple(sorted(clusters, key=lambda cluster: cluster[0]))


def build_independent_clusters_v3_1(
    records: Sequence[EvidenceRecord],
    near_duplicate_window_days: int = 7,
    rules: MissingAwareScoringRules = MissingAwareScoringRules(),
) -> tuple[tuple[str, ...], ...]:
    """Missing-aware constrained clustering for ``evidence-independence.v3.1``.

    The pair dependency score is normalized by the available signal weight:
    missing publish dates or weak metadata reduce decision confidence instead
    of zeroing the score.  Pairs with high score and sufficient coverage merge;
    low-score pairs stay independent; the rest is review_required and does not
    merge by default.
    """

    clusters, _pair_decisions = build_independent_clusters_v3_1_with_decisions(
        records,
        near_duplicate_window_days,
        rules,
    )
    return clusters


def build_independent_clusters_v3_1_with_decisions(
    records: Sequence[EvidenceRecord],
    near_duplicate_window_days: int = 7,
    rules: MissingAwareScoringRules = MissingAwareScoringRules(),
) -> tuple[
    tuple[tuple[str, ...], ...],
    tuple[tuple[str, str, str], ...],
]:
    """V3.1 clustering plus persisted three-interval pair decisions.

    A pair whose normalized score says ``merge`` is downgraded to
    ``review_required`` when the conservative compatibility gate (strong
    document identity or same enterprise) blocks the union; a low-score pair
    with high deterministic text corroboration and incomplete identity/time
    metadata is upgraded to ``review_required``.  ``review_required`` never
    merges.
    """

    _require_unique_evidence_ids(records)
    parent = list(range(len(records)))
    cluster_members: dict[int, set[int]] = {
        index: {index} for index in range(len(records))
    }
    decisions: dict[tuple[int, int], str] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def compatible_pair(left: EvidenceRecord, right: EvidenceRecord) -> bool:
        if rules.strong_identity_merge and _strong_identity(left, right):
            return True
        same_company = _same_optional(left.enterprise_id, right.enterprise_id)
        return same_company

    def compatible_clusters(left_root: int, right_root: int) -> bool:
        if left_root == right_root:
            return True
        return all(
            compatible_pair(records[left_index], records[right_index])
            for left_index in cluster_members[left_root]
            for right_index in cluster_members[right_root]
        )

    def union(left_index: int, right_index: int) -> None:
        root_left = find(left_index)
        root_right = find(right_index)
        if root_left == root_right:
            return
        if not compatible_clusters(root_left, root_right):
            return
        if len(cluster_members[root_left]) < len(cluster_members[root_right]):
            root_left, root_right = root_right, root_left
        parent[root_right] = root_left
        cluster_members[root_left].update(cluster_members[root_right])
        del cluster_members[root_right]

    for index, record in enumerate(records):
        for other_index in range(index + 1, len(records)):
            other = records[other_index]
            score, coverage, _confidence = _source_aware_pair_score_v31(
                record,
                other,
                near_duplicate_window_days,
                rules,
            )
            decision = _dependency_decision(score, coverage, rules)
            if decision == "merge" and not compatible_pair(record, other):
                decision = "review_required"
            elif (
                decision == "independent"
                and not _metadata_complete(record, other)
                and _review_corroboration(record, other, rules)
            ):
                decision = "review_required"
            decisions[(index, other_index)] = decision
            if decision == "merge":
                merged = union(index, other_index)
                if not merged:
                    decision = "review_required"
                    decisions[(index, other_index)] = decision

    grouped: dict[int, list[str]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record.evidence_id)
    clusters = [tuple(sorted(evidence_ids)) for evidence_ids in grouped.values()]
    pair_decisions = tuple(
        (
            records[left_index].evidence_id,
            records[right_index].evidence_id,
            decisions[(left_index, right_index)],
        )
        for left_index, right_index in sorted(decisions)
    )
    return (
        tuple(sorted(clusters, key=lambda cluster: cluster[0])),
        pair_decisions,
    )


def build_independent_clusters_v3_2(
    records: Sequence[EvidenceRecord],
    near_duplicate_window_days: int = 14,
    rules: ConstrainedAgglomerationRules = ConstrainedAgglomerationRules(),
) -> tuple[
    tuple[tuple[str, ...], ...],
    tuple[PairDecision, ...],
]:
    """Two-stage constrained agglomeration for ``evidence-independence.v3.2``.

    Stage one scores every candidate pair independently with continuous
    lexical similarity and explicit hard-decision regions.  Stage two executes
    compatible cluster unions in confidence order.  ``merge`` pairs blocked by
    cluster incompatibility are downgraded to ``review_required`` with a
    certificate reason, so the final clusters and pair decisions are mutually
    consistent.
    """

    _require_unique_evidence_ids(records)
    parent = list(range(len(records)))
    cluster_members: dict[int, set[int]] = {
        index: {index} for index in range(len(records))
    }

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def compatible_pair(left: EvidenceRecord, right: EvidenceRecord) -> bool:
        if rules.strong_identity_merge and _strong_identity(left, right):
            return True
        return _same_optional(left.enterprise_id, right.enterprise_id)

    def compatible_clusters(left_root: int, right_root: int) -> bool:
        if left_root == right_root:
            return True
        return all(
            compatible_pair(records[left_index], records[right_index])
            for left_index in cluster_members[left_root]
            for right_index in cluster_members[right_root]
        )

    def union(left_index: int, right_index: int) -> bool:
        root_left = find(left_index)
        root_right = find(right_index)
        if root_left == root_right:
            return True
        if not compatible_clusters(root_left, root_right):
            return False
        if len(cluster_members[root_left]) < len(cluster_members[root_right]):
            root_left, root_right = root_right, root_left
        parent[root_right] = root_left
        cluster_members[root_left].update(cluster_members[root_right])
        del cluster_members[root_right]
        return True

    candidates: list[tuple[PairDecision, int, int]] = []
    for index, record in enumerate(records):
        for other_index in range(index + 1, len(records)):
            other = records[other_index]
            score, coverage, confidence, feature_reasons = (
                _source_aware_pair_score_v32(
                    record,
                    other,
                    near_duplicate_window_days,
                    rules,
                )
            )
            raw_decision = _v32_dependency_decision(
                record,
                other,
                score,
                coverage,
                near_duplicate_window_days,
                rules,
            )
            candidates.append(
                (
                    PairDecision(
                        left_evidence_id=record.evidence_id,
                        right_evidence_id=other.evidence_id,
                        raw_decision=raw_decision,
                        final_decision=raw_decision,
                        score=round(score, 6),
                        confidence=round(confidence, 6),
                        coverage=round(coverage, 6),
                        reasons=feature_reasons,
                    ),
                    index,
                    other_index,
                )
            )
    merge_candidates = sorted(
        (
            candidate
            for candidate in candidates
            if candidate[0].raw_decision == "merge"
        ),
        key=lambda item: (
            -item[0].confidence,
            -item[0].score,
            item[1],
            item[2],
        ),
    )
    for decision, index, other_index in merge_candidates:
        if not compatible_pair(records[index], records[other_index]):
            candidates = _replace_pair_decision(
                candidates,
                decision,
                final_decision="review_required",
                union_attempted=True,
                union_accepted=False,
                rejection_reason="pair_incompatibility",
            )
            continue
        accepted = union(index, other_index)
        candidates = _replace_pair_decision(
            candidates,
            decision,
            final_decision="merge" if accepted else "review_required",
            union_attempted=True,
            union_accepted=accepted,
            rejection_reason=None if accepted else "cluster_incompatibility",
        )

    grouped: dict[int, list[str]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record.evidence_id)
    clusters = [tuple(sorted(evidence_ids)) for evidence_ids in grouped.values()]
    pair_decisions = tuple(
        sorted(
            candidates,
            key=lambda item: (
                item[0].left_evidence_id,
                item[0].right_evidence_id,
            ),
        )
    )
    return (
        tuple(sorted(clusters, key=lambda cluster: cluster[0])),
        tuple(decision for decision, _left, _right in pair_decisions),
    )


def _replace_pair_decision(
    candidates: list[tuple[PairDecision, int, int]],
    original: PairDecision,
    *,
    final_decision: str,
    union_attempted: bool,
    union_accepted: bool | None,
    rejection_reason: str | None,
) -> list[tuple[PairDecision, int, int]]:
    return [
        (
            PairDecision(
                left_evidence_id=item[0].left_evidence_id,
                right_evidence_id=item[0].right_evidence_id,
                raw_decision=item[0].raw_decision,
                final_decision=(
                    final_decision
                    if (
                        item[0].left_evidence_id == original.left_evidence_id
                        and item[0].right_evidence_id
                        == original.right_evidence_id
                    )
                    else item[0].final_decision
                ),
                score=item[0].score,
                confidence=item[0].confidence,
                coverage=item[0].coverage,
                union_attempted=(
                    union_attempted
                    if (
                        item[0].left_evidence_id == original.left_evidence_id
                        and item[0].right_evidence_id
                        == original.right_evidence_id
                    )
                    else item[0].union_attempted
                ),
                union_accepted=(
                    union_accepted
                    if (
                        item[0].left_evidence_id == original.left_evidence_id
                        and item[0].right_evidence_id
                        == original.right_evidence_id
                    )
                    else item[0].union_accepted
                ),
                rejection_reason=(
                    rejection_reason
                    if (
                        item[0].left_evidence_id == original.left_evidence_id
                        and item[0].right_evidence_id
                        == original.right_evidence_id
                    )
                    else item[0].rejection_reason
                ),
                reasons=item[0].reasons,
            ),
            item[1],
            item[2],
        )
        for item in candidates
    ]


def cluster_weights(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    rules: IndependenceWeightRules,
) -> Mapping[str, float]:
    """Return one frozen weight per independent cluster.

    Duplicate copies inside a cluster do not add weight; N_eff is computed
    over independent clusters so repeated crawls cannot inflate it.
    """

    record_by_id = {record.evidence_id: record for record in records}
    weights: dict[str, float] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        weight = rules.base_weight
        if rules.quality_factor:
            weight *= 0.5 + 0.5 * max(
                record.quality_score for record in members
            )
        if rules.completeness_factor and not any(
            record.completeness for record in members
        ):
            weight *= 0.5
        weights[cluster[0]] = weight
    return weights


def cluster_weights_v2(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    rules: RobustAggregationRules = RobustAggregationRules(),
) -> Mapping[str, float]:
    """Diversity-aware weights for ``robust-evidence-aggregation.v2``.

    A cluster's weight is its quality/completeness-adjusted base weight times
    a diminishing-return factor per shared source/enterprise/template group.
    """

    record_by_id = {record.evidence_id: record for record in records}
    group_counts: dict[str, Counter] = {
        "source": Counter(),
        "enterprise": Counter(),
        "template": Counter(),
    }
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        group_counts["source"][_group_key(members, "source_id")] += 1
        group_counts["enterprise"][_group_key(members, "enterprise_id")] += 1
        group_counts["template"][_group_key(members, "template_cluster_id")] += 1
    weights: dict[str, float] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        weight = rules.base_weight
        if rules.quality_factor:
            weight *= 0.5 + 0.5 * max(record.quality_score for record in members)
        if rules.completeness_factor and not any(
            record.completeness for record in members
        ):
            weight *= 0.5
        weight *= _diversity_factor(
            group_counts["source"][_group_key(members, "source_id")],
            rules.source_cap,
        )
        weight *= _diversity_factor(
            group_counts["enterprise"][_group_key(members, "enterprise_id")],
            rules.enterprise_cap,
        )
        weight *= _diversity_factor(
            group_counts["template"][_group_key(members, "template_cluster_id")],
            rules.template_cap,
        )
        weights[cluster[0]] = round(weight, 6)
    return weights


def _group_key(members: Sequence[EvidenceRecord], field_name: str) -> str:
    value = next(
        (getattr(record, field_name) for record in members if getattr(record, field_name)),
        None,
    )
    return str(value or "unknown")


def _diversity_factor(group_size: int, cap: float) -> float:
    if group_size <= 0:
        return 1.0
    return round(min(1.0, cap / group_size), 6)


def cluster_weights_v3(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    rules: MassCappedAggregationRules = MassCappedAggregationRules(),
) -> Mapping[str, float]:
    """Weight-mass capped weights for ``robust-evidence-aggregation.v3``.

    Raw cluster weights are quality/completeness adjusted and normalized to a
    probability mass.  Each dimension (source -> enterprise -> template) is
    capped by rescaling violating groups and renormalizing until every cap is
    satisfied.  The returned weights sum to 1.0, so the Kish effective sample
    size is ``1 / sum(p_i^2)``.
    """

    record_by_id = {record.evidence_id: record for record in records}
    group_keys: dict[tuple[str, str], str] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        group_keys[("source", cluster[0])] = _group_key(members, "source_id")
        group_keys[("enterprise", cluster[0])] = _group_key(
            members, "enterprise_id"
        )
        group_keys[("template", cluster[0])] = _group_key(
            members, "template_cluster_id"
        )
    raw: dict[str, float] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        weight = rules.base_weight
        if rules.quality_factor:
            weight *= 0.5 + 0.5 * max(record.quality_score for record in members)
        if rules.completeness_factor and not any(
            record.completeness for record in members
        ):
            weight *= 0.5
        raw[cluster[0]] = weight
    total = sum(raw.values())
    if total <= 0:
        return {cluster_id: 0.0 for cluster_id in raw}
    probabilities = {cluster_id: weight / total for cluster_id, weight in raw.items()}
    caps = (
        ("source", rules.source_cap),
        ("enterprise", rules.enterprise_cap),
        ("template", rules.template_cap),
    )
    for _iteration in range(rules.max_iterations):
        for dimension, cap in caps:
            group_mass: dict[str, float] = {}
            for cluster_id, probability in probabilities.items():
                key = group_keys.get((dimension, cluster_id), "unknown")
                group_mass[key] = group_mass.get(key, 0.0) + probability
            for cluster_id, probability in probabilities.items():
                key = group_keys.get((dimension, cluster_id), "unknown")
                mass = group_mass.get(key, 0.0)
                if mass <= cap:
                    continue
                scale = cap / mass
                probabilities[cluster_id] = probability * scale
            mass_total = sum(probabilities.values())
            if mass_total > 0:
                probabilities = {
                    cluster_id: value / mass_total
                    for cluster_id, value in probabilities.items()
                }
        converged = True
        for dimension, cap in caps:
            post_mass: dict[str, float] = {}
            for cluster_id, probability in probabilities.items():
                key = group_keys.get((dimension, cluster_id), "unknown")
                post_mass[key] = post_mass.get(key, 0.0) + probability
            if any(
                mass - cap > rules.tolerance
                for mass in post_mass.values()
            ):
                converged = False
                break
        if converged:
            break
    return {
        cluster_id: round(probability, 8)
        for cluster_id, probability in probabilities.items()
    }


def effective_sample_size(weights: Sequence[float]) -> float:
    total = sum(weights)
    if total == 0:
        return 0.0
    squared_sum = sum(weight * weight for weight in weights)
    return round((total * total) / squared_sum, 4)


def cluster_weights_v4(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    rules: MassCappedAggregationRules = MassCappedAggregationRules(),
) -> tuple[Mapping[str, float], MassProjectionCertificate]:
    """KL-style projected mass weights with a feasibility certificate.

    Unlike the v3 rescale-and-renormalize loop, v4 keeps the raw quality
    probability as the reference distribution and applies a normalized
    projection under source/enterprise/template caps.  The returned certificate
    exposes convergence, maximum constraint violation and provable
    infeasibility instead of silently returning after 20 iterations.
    """

    record_by_id = {record.evidence_id: record for record in records}
    group_membership: dict[tuple[str, str], dict[str, float]] = {}
    for dimension, field_name in (
        ("source", "source_id"),
        ("enterprise", "enterprise_id"),
        ("template", "template_cluster_id"),
    ):
        for cluster_id, groups in _fractional_group_membership(
            records, clusters, field_name
        ).items():
            group_membership[(dimension, cluster_id)] = groups
    raw: dict[str, float] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        weight = rules.base_weight
        if rules.quality_factor:
            weight *= 0.5 + 0.5 * max(record.quality_score for record in members)
        if rules.completeness_factor and not any(
            record.completeness for record in members
        ):
            weight *= 0.5
        raw[cluster[0]] = weight
    total = sum(raw.values())
    if total <= 0:
        empty = {cluster_id: 0.0 for cluster_id in raw}
        certificate = MassProjectionCertificate(
            converged=False,
            infeasible=True,
            max_constraint_violation=0.0,
            iterations=0,
        )
        return empty, certificate
    probabilities = {
        cluster_id: weight / total for cluster_id, weight in raw.items()
    }
    caps = (
        ("source", rules.source_cap),
        ("enterprise", rules.enterprise_cap),
        ("template", rules.template_cap),
    )
    projected, certificate = project_mass_capped_weights_dual(
        probabilities,
        group_membership,
        caps,
        max_iterations=rules.max_iterations,
        tolerance=rules.tolerance,
    )
    return (
        {
            cluster_id: round(probability, 8)
            for cluster_id, probability in projected.items()
        },
        certificate,
    )


def build_cluster_freshness(
    clusters: Sequence[tuple[str, ...]],
    temporal_certificate: TemporalFreshnessCertificate,
) -> tuple[tuple[str, float, str], ...]:
    """Per-cluster trustable freshness = max member freshness weight.

    Each entry is ``(cluster_id, freshness_weight, representative_evidence_id)``
    where the representative is the member carrying the highest freshness
    weight (ties broken by the lowest evidence_id, guaranteeing determinism).
    """
    profile_by_id = {
        profile.evidence_id: profile
        for profile in temporal_certificate.profiles
    }
    result: list[tuple[str, float, str]] = []
    for cluster in clusters:
        members = [
            profile_by_id[evidence_id]
            for evidence_id in cluster
            if evidence_id in profile_by_id
        ]
        if not members:
            result.append((cluster[0], 0.0, cluster[0]))
            continue
        best = sorted(
            members,
            key=lambda profile: (-profile.freshness_weight, profile.evidence_id),
        )[0]
        result.append(
            (cluster[0], round(best.freshness_weight, 8), best.evidence_id)
        )
    return tuple(result)


def cluster_weights_v5(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    cluster_freshness: Mapping[str, float],
    rules: MassCappedAggregationRules = MassCappedAggregationRules(),
) -> tuple[Mapping[str, float], MassProjectionCertificate]:
    """Freshness-aware aggregation for ``robust-evidence-aggregation.v5``.

    The raw cluster prior is

    ``base_weight × quality_factor × completeness_factor × temporal_freshness``

    where temporal_freshness is the maximum trustable member freshness weight.
    The raw mass is normalized and then projected with the *same* KL/dual
    source -> enterprise -> template projection used by v4; no second
    projection is implemented here.
    """

    record_by_id = {record.evidence_id: record for record in records}
    group_membership: dict[tuple[str, str], dict[str, float]] = {}
    for dimension, field_name in (
        ("source", "source_id"),
        ("enterprise", "enterprise_id"),
        ("template", "template_cluster_id"),
    ):
        for cluster_id, groups in _fractional_group_membership(
            records, clusters, field_name
        ).items():
            group_membership[(dimension, cluster_id)] = groups
    raw: dict[str, float] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        weight = rules.base_weight
        if rules.quality_factor:
            weight *= 0.5 + 0.5 * max(record.quality_score for record in members)
        if rules.completeness_factor and not any(
            record.completeness for record in members
        ):
            weight *= 0.5
        weight *= cluster_freshness.get(cluster[0], 1.0)
        raw[cluster[0]] = weight
    total = sum(raw.values())
    if total <= 0:
        empty = {cluster_id: 0.0 for cluster_id in raw}
        certificate = MassProjectionCertificate(
            converged=False,
            infeasible=True,
            max_constraint_violation=0.0,
            iterations=0,
        )
        return empty, certificate
    probabilities = {
        cluster_id: weight / total for cluster_id, weight in raw.items()
    }
    caps = (
        ("source", rules.source_cap),
        ("enterprise", rules.enterprise_cap),
        ("template", rules.template_cap),
    )
    projected, certificate = project_mass_capped_weights_dual(
        probabilities,
        group_membership,
        caps,
        max_iterations=rules.max_iterations,
        tolerance=rules.tolerance,
    )
    return (
        {
            cluster_id: round(probability, 8)
            for cluster_id, probability in projected.items()
        },
        certificate,
    )


def project_mass_capped_weights(
    probabilities: Mapping[str, float],
    group_keys: Mapping[tuple[str, str], str],
    caps: Sequence[tuple[str, float]],
    *,
    max_iterations: int = 20,
    tolerance: float = 1e-6,
) -> tuple[dict[str, float], MassProjectionCertificate]:
    """Project probability mass under per-dimension group caps.

    Each iteration scales every violating group to its cap and renormalizes.
    If the dimension caps cannot cover probability mass one, the certificate is
    marked infeasible; otherwise non-convergence is reported with the maximum
    remaining constraint violation.
    """

    projected = dict(probabilities)
    infeasible_dimensions = _infeasible_dimensions(
        group_keys, caps, tolerance
    )
    iterations = 0
    max_violation = _max_group_violation(
        projected, group_keys, caps
    )
    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        for dimension, cap in caps:
            group_mass: dict[str, float] = {}
            for cluster_id, probability in projected.items():
                key = group_keys.get((dimension, cluster_id), "unknown")
                group_mass[key] = group_mass.get(key, 0.0) + probability
            for cluster_id, probability in projected.items():
                key = group_keys.get((dimension, cluster_id), "unknown")
                mass = group_mass.get(key, 0.0)
                if mass <= cap:
                    continue
                projected[cluster_id] = probability * (cap / mass)
            mass_total = sum(projected.values())
            if mass_total > 0:
                projected = {
                    cluster_id: value / mass_total
                    for cluster_id, value in projected.items()
                }
        max_violation = _max_group_violation(
            projected, group_keys, caps
        )
        if max_violation <= tolerance:
            break
    converged = max_violation <= tolerance
    certificate = MassProjectionCertificate(
        converged=converged,
        infeasible=bool(infeasible_dimensions),
        max_constraint_violation=round(max_violation, 8),
        iterations=iterations,
        infeasible_dimensions=tuple(sorted(infeasible_dimensions)),
    )
    return projected, certificate


def _fractional_group_membership(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    field_name: str,
) -> dict[str, dict[str, float]]:
    """Fractional cluster membership over distinct group values.

    A cluster supported by two sources contributes 0.5 to each source instead
    of being assigned wholesale to the first non-empty value.
    """

    record_by_id = {record.evidence_id: record for record in records}
    membership: dict[str, dict[str, float]] = {}
    for cluster in clusters:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        groups = sorted(
            {
                str(getattr(record, field_name) or "unknown")
                for record in members
            }
        )
        share = round(1.0 / len(groups), 8)
        membership[cluster[0]] = {
            group_id: share for group_id in groups
        }
    return membership


def project_mass_capped_weights_dual(
    probabilities: Mapping[str, float],
    group_membership: Mapping[tuple[str, str], Mapping[str, float]],
    caps: Sequence[tuple[str, float]],
    *,
    max_iterations: int = 500,
    tolerance: float = 1e-6,
    probability_tolerance: float = 1e-8,
    lambda_tolerance: float = 1e-8,
    learning_rate: float = 1.0,
    decay: float = 0.02,
) -> tuple[dict[str, float], MassProjectionCertificate]:
    """Projected dual KL projection with KKT certificate.

    ``p_i ∝ q_i exp(-(A^T λ)_i)``; every λ uses the projected update
    ``max(0, λ + eta * (mass - cap))`` so λ can decrease when a constraint
    becomes slack, matching KKT for inequality constraints.  The step uses
    ``eta = learning_rate / (1 + decay * (t - 1))`` so the tail converges
    instead of stalling at a small 1/sqrt(t) step.
    """

    cluster_ids = tuple(probabilities)
    q = {
        cluster_id: max(float(probability), 1e-12)
        for cluster_id, probability in probabilities.items()
    }
    lambdas: dict[tuple[str, str], float] = {}
    projected = dict(q)
    previous_projected = dict(q)
    max_violation = 0.0
    max_probability_delta = 0.0
    max_lambda_delta = 0.0
    max_complementarity_error = 0.0
    iterations = 0
    constraints = sorted(
        {
            (dimension, group)
            for dimension, _cap in caps
            for (dimension_key, _cluster_id), groups in group_membership.items()
            if dimension_key == dimension
            for group in groups
        }
    )
    infeasible_dims = _infeasible_dimensions_dual(
        group_membership, caps, tolerance
    )
    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        previous_projected = dict(projected)
        exponents = {
            cluster_id: sum(
                lambdas.get((dimension, group), 0.0) * fraction
                for (dimension, cluster_key), groups in group_membership.items()
                if cluster_key == cluster_id
                for group, fraction in groups.items()
            )
            for cluster_id in cluster_ids
        }
        raw = {
            cluster_id: q[cluster_id] * math.exp(-exponents[cluster_id])
            for cluster_id in cluster_ids
        }
        total = sum(raw.values())
        projected = {
            cluster_id: value / total for cluster_id, value in raw.items()
        }
        constraint_mass: dict[tuple[str, str], float] = {}
        for dimension, cap in caps:
            group_mass: dict[str, float] = {}
            for cluster_id, probability in projected.items():
                for group, fraction in group_membership.get(
                    (dimension, cluster_id), {}
                ).items():
                    group_mass[group] = (
                        group_mass.get(group, 0.0) + probability * fraction
                    )
            for group, mass in group_mass.items():
                constraint_mass[(dimension, group)] = mass
        eta = learning_rate / (1.0 + decay * (iteration - 1))
        max_lambda_delta = 0.0
        for key in constraints:
            old_lambda = lambdas.get(key, 0.0)
            cap = dict(caps)[key[0]]
            gradient = constraint_mass.get(key, 0.0) - cap
            new_lambda = max(0.0, old_lambda + eta * gradient)
            lambdas[key] = new_lambda
            max_lambda_delta = max(
                max_lambda_delta, abs(new_lambda - old_lambda)
            )
        max_violation = max(
            (
                max(constraint_mass.get(key, 0.0) - dict(caps)[key[0]], 0.0)
                for key in constraints
            ),
            default=0.0,
        )
        max_probability_delta = max(
            (
                abs(projected[cluster_id] - previous_projected[cluster_id])
                for cluster_id in cluster_ids
            ),
            default=0.0,
        )
        max_complementarity_error = max(
            (
                abs(
                    lambdas.get(key, 0.0)
                    * max(constraint_mass.get(key, 0.0) - dict(caps)[key[0]], 0.0)
                )
                for key in constraints
            ),
            default=0.0,
        )
        if (
            max_violation <= tolerance
            and max_probability_delta <= probability_tolerance
            and max_lambda_delta <= lambda_tolerance
        ):
            break
    objective_kl = sum(
        (
            projected[cluster_id]
            * math.log(projected[cluster_id] / q[cluster_id])
            if projected[cluster_id] > 0 and q[cluster_id] > 0
            else 0.0
        )
        for cluster_id in cluster_ids
    )
    converged = (
        max_violation <= tolerance
        and max_probability_delta <= probability_tolerance
        and max_lambda_delta <= lambda_tolerance
    )
    certificate = MassProjectionCertificate(
        converged=converged,
        infeasible=bool(infeasible_dims),
        max_constraint_violation=round(max_violation, 8),
        iterations=iterations,
        infeasible_dimensions=tuple(sorted(infeasible_dims)),
        max_lambda_delta=round(max_lambda_delta, 8),
        max_probability_delta=round(max_probability_delta, 8),
        objective_kl=round(objective_kl, 8),
        max_complementarity_error=round(max_complementarity_error, 8),
    )
    return projected, certificate


def _infeasible_dimensions_dual(
    group_membership: Mapping[tuple[str, str], Mapping[str, float]],
    caps: Sequence[tuple[str, float]],
    tolerance: float,
) -> set[str]:
    infeasible: set[str] = set()
    for dimension, cap in caps:
        groups = {
            group
            for (dimension_key, _cluster_id), members in group_membership.items()
            if dimension_key == dimension
            for group in members
        }
        if groups and cap * len(groups) < 1.0 - tolerance:
            infeasible.add(dimension)
    return infeasible


def _infeasible_dimensions(
    group_keys: Mapping[tuple[str, str], str],
    caps: Sequence[tuple[str, float]],
    tolerance: float,
) -> set[str]:
    infeasible: set[str] = set()
    for dimension, cap in caps:
        groups = {
            key
            for (dimension_key, _cluster_id), key in group_keys.items()
            if dimension_key == dimension
        }
        if groups and cap * len(groups) < 1.0 - tolerance:
            infeasible.add(dimension)
    return infeasible


def _max_group_violation(
    probabilities: Mapping[str, float],
    group_keys: Mapping[tuple[str, str], str],
    caps: Sequence[tuple[str, float]],
) -> float:
    max_violation = 0.0
    for dimension, cap in caps:
        group_mass: dict[str, float] = {}
        for cluster_id, probability in probabilities.items():
            key = group_keys.get((dimension, cluster_id), "unknown")
            group_mass[key] = group_mass.get(key, 0.0) + probability
        max_violation = max(
            max_violation,
            max(
                (mass - cap for mass in group_mass.values()),
                default=0.0,
            ),
        )
    return max_violation


def entropy_effective_size(weights: Sequence[float]) -> float:
    """Entropy effective sample size ``exp(H(p))`` over normalized weights."""

    total = sum(weights)
    if total <= 0:
        return 0.0
    probabilities = [weight / total for weight in weights if weight > 0]
    if not probabilities:
        return 0.0
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities
    )
    return round(math.exp(entropy), 4)


def build_evidence_aggregation(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
    rules: IndependenceWeightRules = INDEPENDENCE_WEIGHT_RULES_V1,
    source_aware_rules: SourceAwareClusteringRules = SourceAwareClusteringRules(),
    missing_aware_rules: MissingAwareScoringRules = MissingAwareScoringRules(),
    agglomeration_rules: ConstrainedAgglomerationRules = ConstrainedAgglomerationRules(),
    mass_capped_rules: MassCappedAggregationRules = MassCappedAggregationRules(),
    temporal_rules: TemporalFreshnessRules | None = None,
) -> EvidenceAggregationResult:
    """One shared clustering + aggregation pipeline for summary/experiment/LOO."""

    _require_unique_evidence_ids(records)
    records = _window_filtered(records, request)
    if request.algorithm_version == "evidence-independence.v3.2":
        clusters, pair_certificates = build_independent_clusters_v3_2(
            records,
            request.near_duplicate_window_days,
            agglomeration_rules,
        )
        pair_decisions = tuple(
            (
                decision.left_evidence_id,
                decision.right_evidence_id,
                decision.final_decision,
            )
            for decision in pair_certificates
        )
    elif request.algorithm_version == "evidence-independence.v3.1":
        clusters, pair_decisions = build_independent_clusters_v3_1_with_decisions(
            records,
            request.near_duplicate_window_days,
            missing_aware_rules,
        )
        pair_certificates = ()
    elif request.algorithm_version == "evidence-independence.v3":
        clusters = build_independent_clusters_v3(
            records,
            request.near_duplicate_window_days,
            source_aware_rules,
        )
        pair_decisions = ()
        pair_certificates = ()
    else:
        clusters = build_independent_clusters(
            records, request.near_duplicate_window_days
        )
        pair_decisions = ()
        pair_certificates = ()
    certificate: MassProjectionCertificate | None = None
    aggregation_version = request.aggregation_version
    if aggregation_version == "auto":
        aggregation_version = (
            "robust-evidence-aggregation.v3"
            if request.algorithm_version == "evidence-independence.v3.2"
            else "robust-evidence-aggregation.v1"
        )
    temporal_certificate: TemporalFreshnessCertificate | None = None
    cluster_freshness: tuple[tuple[str, float, str], ...] = ()
    temporal_algorithm_version: str | None = None
    temporal_reasons: tuple[str, ...] = ()
    if aggregation_version == ROBUST_EVIDENCE_AGGREGATION_VERSION_V5:
        if request.observation_reference_date is None:
            raise TemporalFreshnessError(
                "robust-evidence-aggregation.v5 requires an explicit "
                "observation_reference_date"
            )
        resolved_temporal_rules = temporal_rules or TemporalFreshnessRules()
        temporal_certificate = build_temporal_freshness(
            records,
            request.observation_reference_date,
            resolved_temporal_rules,
        )
        cluster_freshness = build_cluster_freshness(
            clusters, temporal_certificate
        )
        freshness_map = {
            cluster_id: freshness
            for cluster_id, freshness, _representative in cluster_freshness
        }
        weights, certificate = cluster_weights_v5(
            records,
            clusters,
            freshness_map,
            mass_capped_rules,
        )
        cluster_freshness_map = {
            cluster_id: (freshness, representative)
            for cluster_id, freshness, representative in cluster_freshness
        }
        staleness_map = cluster_staleness_states(clusters, temporal_certificate)
        temporal_reasons = derive_temporal_reasons(
            temporal_certificate,
            staleness_map,
            resolved_temporal_rules,
        )
        temporal_algorithm_version = TEMPORAL_FRESHNESS_VERSION
    elif aggregation_version == "robust-evidence-aggregation.v4":
        weights, certificate = cluster_weights_v4(
            records, clusters, mass_capped_rules
        )
    elif aggregation_version == "robust-evidence-aggregation.v3":
        weights = cluster_weights_v3(records, clusters, mass_capped_rules)
    elif aggregation_version == "robust-evidence-aggregation.v2":
        weights = cluster_weights_v2(records, clusters)
    else:
        weights = cluster_weights(records, clusters, rules)
    fields = ("source_id", "enterprise_id", "template_cluster_id")
    labels = ("source", "enterprise", "template")
    raw_distributions = {
        label: _distribution(records, field)
        for label, field in zip(labels, fields, strict=True)
    }
    cluster_distributions = {
        label: _cluster_group_distribution(records, clusters, field)
        for label, field in zip(labels, fields, strict=True)
    }
    effective_mass_distributions = {
        label: _effective_mass_distribution(
            weights, records, clusters, field
        )
        for label, field in zip(labels, fields, strict=True)
    }
    return EvidenceAggregationResult(
        evidence_ids=tuple(
            sorted(record.evidence_id for record in records)
        ),
        clusters=clusters,
        weights=tuple(sorted(weights.items())),
        kish_effective_size=effective_sample_size(list(weights.values())),
        entropy_effective_size=entropy_effective_size(
            list(weights.values())
        ),
        projection_certificate=certificate,
        raw_distributions=raw_distributions,
        cluster_distributions=cluster_distributions,
        effective_mass_distributions=effective_mass_distributions,
        pair_decisions=pair_decisions,
        pair_certificates=pair_certificates,
        temporal_certificate=temporal_certificate,
        cluster_freshness=cluster_freshness,
        temporal_algorithm_version=temporal_algorithm_version,
        temporal_reasons=temporal_reasons,
    )


def _cluster_group_distribution(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    field_name: str,
) -> tuple[DistributionEntry, ...]:
    membership = _fractional_group_membership(
        records, clusters, field_name
    )
    mass: dict[str, float] = {}
    for groups in membership.values():
        for group_id, fraction in groups.items():
            mass[group_id] = mass.get(group_id, 0.0) + fraction
    total = len(clusters)
    return tuple(
        sorted(
            (
                DistributionEntry(
                    group_id=group_id,
                    count=round(mass_value, 4),
                    share=(
                        round(mass_value / total, 4) if total else 0.0
                    ),
                )
                for group_id, mass_value in mass.items()
            ),
            key=lambda entry: (-entry.share, entry.group_id),
        )
    )


def _effective_mass_distribution(
    weights: Mapping[str, float],
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    field_name: str,
) -> tuple[DistributionEntry, ...]:
    membership = _fractional_group_membership(
        records, clusters, field_name
    )
    mass: dict[str, float] = {}
    for cluster_id, weight in weights.items():
        for group_id, fraction in membership.get(cluster_id, {}).items():
            mass[group_id] = mass.get(group_id, 0.0) + weight * fraction
    total = sum(weights.values())
    return tuple(
        sorted(
            (
                DistributionEntry(
                    group_id=group_id,
                    count=round(mass_value, 4),
                    share=(
                        round(mass_value / total, 4) if total else 0.0
                    ),
                )
                for group_id, mass_value in mass.items()
            ),
            key=lambda entry: (-entry.share, entry.group_id),
        )
    )


def build_summary(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
    rules: IndependenceWeightRules = INDEPENDENCE_WEIGHT_RULES_V1,
    source_aware_rules: SourceAwareClusteringRules = SourceAwareClusteringRules(),
    missing_aware_rules: MissingAwareScoringRules = MissingAwareScoringRules(),
    agglomeration_rules: ConstrainedAgglomerationRules = ConstrainedAgglomerationRules(),
    mass_capped_rules: MassCappedAggregationRules = MassCappedAggregationRules(),
    temporal_rules: TemporalFreshnessRules | None = None,
) -> EvidenceIndependenceSummary:
    _require_unique_evidence_ids(records)
    records = _window_filtered(records, request)
    aggregation = build_evidence_aggregation(
        records,
        request,
        rules=rules,
        source_aware_rules=source_aware_rules,
        missing_aware_rules=missing_aware_rules,
        agglomeration_rules=agglomeration_rules,
        mass_capped_rules=mass_capped_rules,
        temporal_rules=temporal_rules,
    )
    clusters = aggregation.clusters
    weights = dict(aggregation.weights)
    pair_decisions = aggregation.pair_decisions
    pair_certificates = aggregation.pair_certificates
    effective_size = aggregation.kish_effective_size
    source_distribution = aggregation.cluster_distributions["source"]
    enterprise_distribution = aggregation.cluster_distributions["enterprise"]
    template_distribution = aggregation.cluster_distributions["template"]
    unresolved_ratio = _unresolved_ratio(records)
    state, reasons = derive_uncertainty_state(
        records,
        clusters,
        request,
        effective_size,
        {
            "source": source_distribution,
            "enterprise": enterprise_distribution,
            "template": template_distribution,
        },
        # v5 disables the legacy dated-only ``all_observations_stale`` heuristic
        # inside the base-state derivation; the TEMP-LAG tri-state gate below is
        # the only stale source for v5, so the base state (insufficient /
        # source_concentrated / unresolved / ok) can never be silently rewritten
        # later.  Legacy auto/v4 keep the old behavior (True).
        apply_legacy_stale_gate=(
            request.aggregation_version != ROBUST_EVIDENCE_AGGREGATION_VERSION_V5
        ),
    )
    if aggregation.temporal_algorithm_version is not None:
        temporal_gate = temporal_rules or TemporalFreshnessRules()
        reasons = tuple(
            dict.fromkeys((*reasons, *aggregation.temporal_reasons))
        )
        # v5 temporal conclusion (TEMP-LAG-01): the tri-state cluster staleness
        # gate applies ON TOP of the temporal-independent base state.  The
        # legacy dated-only ``all_observations_stale`` heuristic is disabled for
        # v5 (see derive_uncertainty_state), so a non-stale base state (e.g.
        # insufficient_evidence / source_concentrated / unresolved) is NEVER
        # rewritten to ``ok`` or ``stale_observation`` just because some dated
        # rows are old.  Unknown-time clusters never force a global stale
        # conclusion.
        if (
            temporal_gate.stale_gate_enabled
            and aggregation.temporal_certificate is not None
        ):
            states = cluster_staleness_states(
                clusters, aggregation.temporal_certificate
            )
            all_stale = bool(states) and all(
                value == "stale" for value in states.values()
            ) and len(clusters) >= request.min_independent_clusters
            has_fresh = "fresh" in states.values()
            has_unknown = "unknown" in states.values()
            if all_stale:
                state = "stale_observation"
                reasons = tuple(
                    dict.fromkeys((*reasons, "all_clusters_stale"))
                )
            elif has_unknown and not has_fresh:
                # no fresh cluster and unknowns present -> the base state stays
                # whatever the temporal-independent logic decided; only escalate
                # the temporal_state_indeterminate reason.  Never promote
                # insufficient_evidence / source_concentrated / unresolved to
                # ``ok`` here.
                reasons = tuple(
                    dict.fromkeys((*reasons, "temporal_state_indeterminate"))
                )
    review_required_pairs = tuple(
        (left_id, right_id)
        for left_id, right_id, decision in pair_decisions
        if decision == "review_required"
    )
    return EvidenceIndependenceSummary(
        subject_ref=request.subject_ref,
        release_id=request.release_id,
        algorithm_version=request.algorithm_version,
        config_hash=config_hash(
            request,
            rules,
            source_aware_rules,
            missing_aware_rules,
            temporal_rules=temporal_rules,
        ),
        coverage_status=request.coverage_status,
        raw_evidence_count=len(records),
        evidence_ids=tuple(sorted(record.evidence_id for record in records)),
        independent_cluster_count=len(clusters),
        effective_sample_size=effective_size,
        unresolved_ratio=unresolved_ratio,
        source_distribution=source_distribution,
        enterprise_distribution=enterprise_distribution,
        template_distribution=template_distribution,
        uncertainty_state=state,
        uncertainty_reasons=reasons,
        pair_decision_count=len(pair_decisions),
        merge_pair_count=sum(
            1 for _left, _right, decision in pair_decisions if decision == "merge"
        ),
        review_required_pair_count=len(review_required_pairs),
        independent_pair_count=sum(
            1
            for _left, _right, decision in pair_decisions
            if decision == "independent"
        ),
        review_required_pairs=review_required_pairs,
        pair_decisions=pair_decisions,
        pair_certificates=pair_certificates,
        temporal_certificate=aggregation.temporal_certificate,
        temporal_algorithm_version=aggregation.temporal_algorithm_version,
        cluster_staleness=tuple(
            sorted(
                cluster_staleness_states(clusters, aggregation.temporal_certificate).items()
            )
        )
        if (
            aggregation.temporal_certificate is not None
            and aggregation.temporal_algorithm_version is not None
        )
        else (),
    )


def derive_uncertainty_state(
    records: Sequence[EvidenceRecord],
    clusters: Sequence[tuple[str, ...]],
    request: IndependenceRequest,
    effective_size: float,
    distributions: Mapping[str, tuple[DistributionEntry, ...]],
    *,
    apply_legacy_stale_gate: bool = True,
) -> tuple[UncertaintyState, tuple[str, ...]]:
    if request.release_id is None or not str(request.release_id).strip():
        return "blocked", ("release_identity_missing",)
    if not records:
        if request.coverage_status == "covered":
            return "not_observed", ("covered_window_no_observation",)
        return "insufficient_evidence", ("no_evidence",)
    dated = [record for record in records if record.published_at is not None]
    if (
        apply_legacy_stale_gate
        and request.observation_reference_date is not None
        and dated
    ):
        reference = request.observation_reference_date
        if all(
            (reference - record.published_at).days > request.stale_observation_days
            for record in dated
        ):
            return "stale_observation", ("all_observations_stale",)
    if len(clusters) < request.min_independent_clusters:
        return "insufficient_evidence", ("independent_clusters_below_minimum",)
    if effective_size < request.min_effective_sample_size:
        return "insufficient_evidence", ("effective_sample_size_below_minimum",)
    concentrated = _concentration_reasons(distributions, request)
    if concentrated:
        return "source_concentrated", concentrated
    if _unresolved_ratio(records) > request.unresolved_threshold:
        return "unresolved", ("unresolved_ratio_above_threshold",)
    return "ok", ()


def run_ablation(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
    ablation_type: AblationType,
    rules: IndependenceWeightRules = INDEPENDENCE_WEIGHT_RULES_V1,
    conclusion: ConclusionRecomputePort | None = None,
    temporal_rules: TemporalFreshnessRules | None = None,
) -> AblationResult:
    if ablation_type == "time_window":
        return _run_time_window_ablation(
            records, request, rules, conclusion, temporal_rules=temporal_rules
        )
    baseline = build_summary(records, request, rules, temporal_rules=temporal_rules)
    baseline_conclusion = (
        conclusion.evaluate(records, request)
        if conclusion is not None
        else None
    )
    group_id, share = _largest_group(records, ablation_type)
    if group_id is None:
        return _ablation_result(
            ablation_type=ablation_type,
            group_id=None,
            share=0.0,
            before_summary=baseline,
            after_summary=baseline,
            before_conclusion=baseline_conclusion,
            after_conclusion=baseline_conclusion,
            failure_reasons=("no_group_removed",),
        )
    remaining = _without_group(records, ablation_type, group_id)
    after = build_summary(remaining, request, rules, temporal_rules=temporal_rules)
    after_conclusion = (
        conclusion.evaluate(remaining, request)
        if conclusion is not None
        else None
    )
    return _ablation_result(
        ablation_type=ablation_type,
        group_id=group_id,
        share=share,
        before_summary=baseline,
        after_summary=after,
        before_conclusion=baseline_conclusion,
        after_conclusion=after_conclusion,
        failure_reasons=(),
    )


def build_certificate(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
    rules: IndependenceWeightRules = INDEPENDENCE_WEIGHT_RULES_V1,
    conclusion: ConclusionRecomputePort | None = None,
    temporal_rules: TemporalFreshnessRules | None = None,
) -> AblationCertificate:
    baseline = build_summary(records, request, rules, temporal_rules=temporal_rules)
    if conclusion is None:
        return AblationCertificate(
            subject_ref=request.subject_ref,
            release_id=request.release_id,
            algorithm_version=request.algorithm_version,
            config_hash=baseline.config_hash,
            conclusion_provider=None,
            baseline=baseline,
            ablations=(),
            certificate_status="not_applicable",
            certificate_reasons=("sensitivity_pending_verification",),
        )
    baseline_conclusion = conclusion.evaluate(records, request)
    ablations = tuple(
        run_ablation(
            records,
            request,
            ablation_type,
            rules,
            conclusion,
            temporal_rules=temporal_rules,
        )
        for ablation_type in ("source", "enterprise", "template", "time_window")
    )
    status, reasons = _certificate_status(
        baseline, ablations, baseline_conclusion.state
    )
    return AblationCertificate(
        subject_ref=request.subject_ref,
        release_id=request.release_id,
        algorithm_version=request.algorithm_version,
        config_hash=baseline.config_hash,
        conclusion_provider=(
            getattr(conclusion, "provider", None)
            or type(conclusion).__name__
        ),
        baseline=baseline,
        ablations=ablations,
        certificate_status=status,
        certificate_reasons=reasons,
    )


def config_hash(
    request: IndependenceRequest,
    rules: IndependenceWeightRules,
    source_aware_rules: SourceAwareClusteringRules = SourceAwareClusteringRules(),
    missing_aware_rules: MissingAwareScoringRules = MissingAwareScoringRules(),
    agglomeration_rules: ConstrainedAgglomerationRules = ConstrainedAgglomerationRules(),
    mass_capped_rules: MassCappedAggregationRules = MassCappedAggregationRules(),
    temporal_rules: TemporalFreshnessRules | None = None,
) -> str:
    payload = {
        "algorithm_version": request.algorithm_version,
        "coverage_status": request.coverage_status,
        "min_independent_clusters": request.min_independent_clusters,
        "min_effective_sample_size": request.min_effective_sample_size,
        "concentration_threshold": request.concentration_threshold,
        "unresolved_threshold": request.unresolved_threshold,
        "near_duplicate_window_days": request.near_duplicate_window_days,
        "stale_observation_days": request.stale_observation_days,
        "window_days": request.window_days,
        "ablation_window_days": request.ablation_window_days,
        "rules": asdict(rules),
        "source_aware_rules": asdict(source_aware_rules),
    }
    if request.algorithm_version == "evidence-independence.v3.1":
        payload["missing_aware_rules"] = asdict(missing_aware_rules)
    if request.algorithm_version == "evidence-independence.v3.2":
        payload["agglomeration_rules"] = asdict(agglomeration_rules)
        payload["mass_capped_rules"] = asdict(mass_capped_rules)
    if request.aggregation_version == ROBUST_EVIDENCE_AGGREGATION_VERSION_V5:
        # v5 config identity must carry the temporal-freshness.v1 version, the
        # time-provenance policy (v1 vs v2 give different identities), the full
        # TemporalFreshnessRules, and the reference-date policy.
        payload["temporal_freshness"] = {
            "algorithm_version": TEMPORAL_FRESHNESS_VERSION,
            "time_provenance_policy": TIME_PROVENANCE_POLICY,
            "reference_date_policy": (
                "explicit_observation_reference_date_required"
            ),
            "reference_date": (
                request.observation_reference_date.isoformat()
                if request.observation_reference_date is not None
                else None
            ),
            "rules": asdict(temporal_rules or TemporalFreshnessRules()),
        }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _related(
    left: EvidenceRecord,
    right: EvidenceRecord,
    near_duplicate_window_days: int,
) -> bool:
    if (
        left.normalized_url
        and right.normalized_url
        and normalized_url(left.normalized_url) == normalized_url(right.normalized_url)
    ):
        return True
    if (
        left.text_fingerprint
        and right.text_fingerprint
        and left.text_fingerprint == right.text_fingerprint
    ):
        return True
    if (
        left.template_cluster_id
        and right.template_cluster_id
        and left.template_cluster_id == right.template_cluster_id
    ):
        return True
    if (
        left.enterprise_id
        and right.enterprise_id
        and left.position_id
        and right.position_id
        and left.published_at
        and right.published_at
        and left.enterprise_id == right.enterprise_id
        and left.position_id == right.position_id
        and abs((left.published_at - right.published_at).days)
        <= near_duplicate_window_days
    ):
        return True
    return False


def _same_optional(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _strong_identity(left: EvidenceRecord, right: EvidenceRecord) -> bool:
    """Strong document identity that may merge even across independent sources."""

    if (
        left.text_fingerprint
        and right.text_fingerprint
        and left.text_fingerprint == right.text_fingerprint
    ):
        return True
    if (
        left.normalized_url
        and right.normalized_url
        and normalized_url(left.normalized_url) == normalized_url(right.normalized_url)
    ):
        return True
    return False


def _source_aware_pair_score(
    left: EvidenceRecord,
    right: EvidenceRecord,
    near_duplicate_window_days: int,
    rules: SourceAwareClusteringRules,
) -> float:
    """Return the source-aware duplicate candidate score D(i, j)."""

    text_similarity = 1.0 if _strong_identity(left, right) else 0.0
    entity_similarity = (
        1.0
        if _same_optional(left.enterprise_id, right.enterprise_id)
        and _same_optional(left.position_id, right.position_id)
        else 0.0
    )
    provenance_similarity = 1.0 if _same_optional(left.source_id, right.source_id) else 0.0
    structure_similarity = (
        1.0
        if _same_optional(left.template_cluster_id, right.template_cluster_id)
        else 0.0
    )
    independent_company = not _same_optional(
        left.enterprise_id, right.enterprise_id
    )
    independent_source = not _same_optional(left.source_id, right.source_id)
    score = (
        rules.text_weight * text_similarity
        + rules.entity_weight * entity_similarity
        + rules.provenance_weight * provenance_similarity
        + rules.structure_weight * structure_similarity
        - (
            rules.independent_company_penalty
            if independent_company
            else 0.0
        )
        - (
            rules.independent_source_penalty
            if independent_source
            else 0.0
        )
    )
    # Same hiring event near-duplicates (same company, position, window) retain
    # the deterministic time-window relation from v2.  Observed/collected time
    # is NOT a publish-time proxy: crawler batches share the same observed
    # timestamp and would over-merge independent postings.
    if (
        _same_optional(left.enterprise_id, right.enterprise_id)
        and _same_optional(left.position_id, right.position_id)
        and left.published_at
        and right.published_at
        and abs((left.published_at - right.published_at).days)
        <= near_duplicate_window_days
    ):
        score = max(score, rules.merge_threshold)
    return score


def _source_aware_pair_score_v31(
    left: EvidenceRecord,
    right: EvidenceRecord,
    near_duplicate_window_days: int,
    rules: MissingAwareScoringRules,
) -> tuple[float, float, float]:
    """Return (normalized_score, signal_coverage, confidence) for one pair."""

    dimensions = (
        (
            "text",
            rules.text_weight,
            1.0 if _strong_identity(left, right) else 0.0,
            bool(
                (left.text_fingerprint and right.text_fingerprint)
                or (left.normalized_url and right.normalized_url)
            ),
        ),
        (
            "entity",
            rules.entity_weight,
            1.0
            if _same_optional(left.enterprise_id, right.enterprise_id)
            and _same_optional(left.position_id, right.position_id)
            else 0.0,
            bool(left.enterprise_id and right.enterprise_id),
        ),
        (
            "provenance",
            rules.provenance_weight,
            1.0 if _same_optional(left.source_id, right.source_id) else 0.0,
            bool(left.source_id and right.source_id),
        ),
        (
            "structure",
            rules.structure_weight,
            1.0
            if _same_optional(left.template_cluster_id, right.template_cluster_id)
            else 0.0,
            bool(left.template_cluster_id and right.template_cluster_id),
        ),
        (
            "temporal",
            rules.temporal_weight,
            1.0
            if left.published_at
            and right.published_at
            and abs((left.published_at - right.published_at).days)
            <= near_duplicate_window_days
            else 0.0,
            bool(left.published_at and right.published_at),
        ),
    )
    available_weight = sum(weight for _name, weight, _value, available in dimensions if available)
    weighted_sum = sum(
        weight * value
        for _name, weight, value, available in dimensions
        if available
    )
    total_weight = sum(weight for _name, weight, _value, _available in dimensions)
    normalized_score = (
        round(weighted_sum / available_weight, 6)
        if available_weight
        else 0.0
    )
    signal_coverage = (
        round(available_weight / total_weight, 6) if total_weight else 0.0
    )
    confidence = round(normalized_score * signal_coverage, 6)
    return normalized_score, signal_coverage, confidence


def _ascii_terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _cjk_ngrams(value: str, n: int) -> set[str]:
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", value.casefold())
    ngrams: set[str] = set()
    for run in cjk_runs:
        ngrams.update(
            run[index : index + n] for index in range(max(0, len(run) - n + 1))
        )
    return ngrams


def _dice(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return round(2 * overlap / (len(left) + len(right)), 6)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return round(overlap / len(left | right), 6)


def lexical_similarity(left: str, right: str) -> float:
    """Deterministic hybrid text similarity for Chinese/ASCII evidence.

    ASCII term dice plus CJK bigram/trigram Jaccard.  The CJK n-grams avoid
    the "whole paragraph is one token" failure mode of plain token sets and
    work fully offline until a semantic embedding feature is added later.
    """

    if not left or not right:
        return 0.0
    ascii_left = _ascii_terms(left)
    ascii_right = _ascii_terms(right)
    cjk2_left = _cjk_ngrams(left, 2)
    cjk2_right = _cjk_ngrams(right, 2)
    cjk3_left = _cjk_ngrams(left, 3)
    cjk3_right = _cjk_ngrams(right, 3)
    ascii_score = _dice(ascii_left, ascii_right)
    cjk2_score = _jaccard(cjk2_left, cjk2_right)
    cjk3_score = _jaccard(cjk3_left, cjk3_right)
    return round(
        0.35 * ascii_score + 0.40 * cjk2_score + 0.25 * cjk3_score,
        6,
    )


def _source_aware_pair_score_v32(
    left: EvidenceRecord,
    right: EvidenceRecord,
    temporal_window_days: int,
    rules: ConstrainedAgglomerationRules,
) -> tuple[float, float, float, tuple[str, ...]]:
    """Return (score, coverage, confidence, reasons) for a v3.2 pair."""

    strong_identity = _strong_identity(left, right)
    identity_available = bool(
        (left.text_fingerprint and right.text_fingerprint)
        or (left.normalized_url and right.normalized_url)
    )
    both_text = bool(left.text and right.text)
    same_company = _same_optional(left.enterprise_id, right.enterprise_id)
    same_position = _same_optional(left.position_id, right.position_id)
    same_platform = _same_optional(left.source_id, right.source_id)
    same_template = _same_optional(
        left.template_cluster_id, right.template_cluster_id
    )
    both_timestamp = bool(left.published_at and right.published_at)
    near_temporal = (
        bool(
            left.published_at
            and right.published_at
            and abs((left.published_at - right.published_at).days)
            <= temporal_window_days
        )
        if both_timestamp
        else False
    )
    dimensions = (
        (
            "document_identity",
            rules.document_identity_weight,
            1.0 if strong_identity else 0.0,
            identity_available,
        ),
        (
            "lexical_similarity",
            rules.lexical_weight,
            (
                1.0
                if strong_identity
                else lexical_similarity(left.text or "", right.text or "")
            ),
            both_text,
        ),
        (
            "entity_consistency",
            rules.entity_weight,
            1.0 if same_company and same_position else 0.0,
            bool(left.enterprise_id and right.enterprise_id),
        ),
        (
            "provenance_identity",
            rules.provenance_weight,
            1.0 if same_platform else 0.0,
            bool(left.source_id and right.source_id),
        ),
        (
            "template_similarity",
            rules.template_weight,
            1.0 if same_template else 0.0,
            bool(left.template_cluster_id and right.template_cluster_id),
        ),
        (
            "temporal_proximity",
            rules.temporal_weight,
            1.0 if near_temporal else 0.0,
            both_timestamp,
        ),
    )
    available_weight = sum(
        weight
        for _name, weight, _value, available in dimensions
        if available
    )
    weighted_sum = sum(
        weight * value
        for _name, weight, value, available in dimensions
        if available
    )
    total_weight = sum(
        weight for _name, weight, _value, _available in dimensions
    )
    normalized_score = (
        round(weighted_sum / available_weight, 6)
        if available_weight
        else 0.0
    )
    signal_coverage = (
        round(available_weight / total_weight, 6)
        if total_weight
        else 0.0
    )
    confidence = round(normalized_score * signal_coverage, 6)
    reasons: list[str] = []
    if strong_identity:
        reasons.append("strong_identity")
    if same_company:
        reasons.append("same_enterprise")
    if same_position:
        reasons.append("same_position")
    if same_platform:
        reasons.append("same_platform")
    if same_template:
        reasons.append("same_template")
    if near_temporal:
        reasons.append("temporal_near")
    elif both_timestamp:
        reasons.append("temporal_far")
    elif not both_timestamp:
        reasons.append("timestamp_missing")
    if both_text:
        reasons.append(f"lexical:{lexical_similarity(left.text or '', right.text or ''):.3f}")
    return normalized_score, signal_coverage, confidence, tuple(reasons)


def _v32_dependency_decision(
    left: EvidenceRecord,
    right: EvidenceRecord,
    score: float,
    coverage: float,
    temporal_window_days: int,
    rules: ConstrainedAgglomerationRules,
) -> str:
    """Hard-region decision policy for v3.2 pair candidates."""

    strong_identity = _strong_identity(left, right)
    lexical = (
        1.0
        if strong_identity
        else lexical_similarity(left.text or "", right.text or "")
    )
    same_company = _same_optional(left.enterprise_id, right.enterprise_id)
    both_enterprise = bool(left.enterprise_id and right.enterprise_id)
    different_company = both_enterprise and not same_company
    same_position = _same_optional(left.position_id, right.position_id)
    same_template = _same_optional(
        left.template_cluster_id, right.template_cluster_id
    )
    both_timestamp = bool(left.published_at and right.published_at)
    near_temporal = (
        bool(
            left.published_at
            and right.published_at
            and abs((left.published_at - right.published_at).days)
            <= temporal_window_days
        )
        if both_timestamp
        else False
    )
    if rules.strong_identity_merge and strong_identity:
        return "merge"
    if (
        same_company
        and same_position
        and same_template
    ):
        if near_temporal or not both_timestamp:
            return "merge"
        return "review_required"
    if (
        same_company
        and same_position
        and lexical >= rules.lexical_merge_threshold
        and score >= rules.score_merge_threshold
        and coverage >= rules.min_coverage
    ):
        if near_temporal or not both_timestamp:
            return "merge"
        return "review_required"
    if (
        different_company
        and not same_template
        and not strong_identity
        and lexical < rules.lexical_independent_threshold
    ):
        return "independent"
    if different_company:
        return "review_required"
    if same_company and not same_template:
        return "independent"
    if not both_enterprise and same_template and same_position:
        return "review_required"
    if not both_enterprise and lexical >= rules.lexical_review_threshold:
        return "review_required"
    if lexical >= rules.lexical_review_threshold:
        return "review_required"
    if score <= rules.score_review_threshold and coverage >= rules.min_coverage:
        return "independent"
    return "review_required"


def _text_similarity(left: EvidenceRecord, right: EvidenceRecord) -> float:
    """Deterministic lexical corroboration used by v3.1 review routing.

    Strong document identity (fingerprint/URL equality) is perfect
    corroboration; otherwise the overlap of token sets on the raw evidence
    text is used.  This lets cross-source reposts with incomplete metadata be
    routed to ``review_required`` instead of silently staying independent.
    """

    if _strong_identity(left, right):
        return 1.0
    if not (left.text and right.text):
        return 0.0
    left_terms = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", left.text.casefold()))
    right_terms = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", right.text.casefold()))
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    return round(2 * overlap / (len(left_terms) + len(right_terms)), 6)


def _review_corroboration(
    left: EvidenceRecord,
    right: EvidenceRecord,
    rules: MissingAwareScoringRules,
) -> bool:
    """High semantic/structural corroboration used by review routing."""

    if _text_similarity(left, right) >= rules.text_review_threshold:
        return True
    return bool(
        left.template_cluster_id
        and right.template_cluster_id
        and left.template_cluster_id == right.template_cluster_id
        and _same_optional(left.position_id, right.position_id)
    )


def _metadata_complete(left: EvidenceRecord, right: EvidenceRecord) -> bool:
    """Both records carry the identity/time signals needed for a merge."""

    return bool(
        left.enterprise_id
        and right.enterprise_id
        and left.published_at
        and right.published_at
    )


def _dependency_decision(
    score: float,
    coverage: float,
    rules: MissingAwareScoringRules,
) -> str:
    if score >= rules.high_threshold and coverage >= rules.min_coverage:
        return "merge"
    if score <= rules.low_threshold:
        return "independent"
    return "review_required"


def _distribution(
    records: Sequence[EvidenceRecord],
    field_name: str,
) -> tuple[DistributionEntry, ...]:
    counts = Counter(
        str(getattr(record, field_name) or "unknown") for record in records
    )
    total = len(records)
    entries = (
        DistributionEntry(
            group_id=group_id,
            count=count,
            share=round(count / total, 4) if total else 0.0,
        )
        for group_id, count in counts.items()
    )
    return tuple(
        sorted(entries, key=lambda entry: (-entry.count, entry.group_id))
    )


def _unresolved_ratio(records: Sequence[EvidenceRecord]) -> float:
    if not records:
        return 0.0
    unresolved = sum(
        record.resolution_status == "unresolved" for record in records
    )
    return round(unresolved / len(records), 4)


def _concentration_reasons(
    distributions: Mapping[str, tuple[DistributionEntry, ...]],
    request: IndependenceRequest,
) -> tuple[str, ...]:
    reasons: list[str] = []
    for label, key in (
        ("source", "source_id"),
        ("enterprise", "enterprise_id"),
        ("template", "template_cluster_id"),
    ):
        known = [
            entry
            for entry in distributions.get(label, ())
            if entry.group_id != "unknown"
        ]
        if known and known[0].share > request.concentration_threshold:
            reasons.append(f"{key}_concentrated")
    return tuple(reasons)


def _largest_group(
    records: Sequence[EvidenceRecord],
    ablation_type: AblationType,
) -> tuple[str | None, float]:
    counts = Counter(
        str(getattr(record, _group_field(ablation_type)) or "unknown")
        for record in records
    )
    candidates = [
        (group_id, count)
        for group_id, count in counts.items()
        if group_id != "unknown"
    ]
    if not candidates:
        return None, 0.0
    group_id, count = sorted(
        candidates, key=lambda item: (-item[1], item[0])
    )[0]
    return group_id, round(count / len(records), 4)


def _without_group(
    records: Sequence[EvidenceRecord],
    ablation_type: AblationType,
    group_id: str,
) -> list[EvidenceRecord]:
    field = _group_field(ablation_type)
    return [
        record
        for record in records
        if str(getattr(record, field) or "unknown") != group_id
    ]


def _run_time_window_ablation(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
    rules: IndependenceWeightRules,
    conclusion: ConclusionRecomputePort | None,
    temporal_rules: TemporalFreshnessRules | None = None,
) -> AblationResult:
    baseline = build_summary(records, request, rules, temporal_rules=temporal_rules)
    baseline_conclusion = (
        conclusion.evaluate(records, request)
        if conclusion is not None
        else None
    )
    if (
        request.window_days is None
        or request.observation_reference_date is None
    ):
        return _ablation_result(
            ablation_type="time_window",
            group_id=None,
            share=0.0,
            before_summary=baseline,
            after_summary=baseline,
            before_conclusion=baseline_conclusion,
            after_conclusion=baseline_conclusion,
            failure_reasons=("time_window_not_configured",),
        )
    after_window_days = (
        request.ablation_window_days
        if request.ablation_window_days is not None
        else max(1, request.window_days // 2)
    )
    after_request = replace(
        request,
        window_days=after_window_days,
        ablation_window_days=None,
    )
    after = build_summary(records, after_request, rules, temporal_rules=temporal_rules)
    before_total = baseline.raw_evidence_count
    after_total = after.raw_evidence_count
    share = (
        round((before_total - after_total) / before_total, 4)
        if before_total
        else 0.0
    )
    after_conclusion = (
        conclusion.evaluate(records, after_request)
        if conclusion is not None
        else None
    )
    return _ablation_result(
        ablation_type="time_window",
        group_id=f"window:{request.window_days}d->{after_window_days}d",
        share=share,
        before_summary=baseline,
        after_summary=after,
        before_conclusion=baseline_conclusion,
        after_conclusion=after_conclusion,
        failure_reasons=(),
    )


def _window_filtered(
    records: Sequence[EvidenceRecord],
    request: IndependenceRequest,
) -> list[EvidenceRecord]:
    if request.window_days is None or request.observation_reference_date is None:
        return list(records)
    reference = request.observation_reference_date
    return [
        record
        for record in records
        if record.published_at is not None
        and 0 <= (reference - record.published_at).days < request.window_days
    ]


def _ablation_result(
    *,
    ablation_type: AblationType,
    group_id: str | None,
    share: float,
    before_summary: EvidenceIndependenceSummary,
    after_summary: EvidenceIndependenceSummary,
    before_conclusion: ConclusionScore | None,
    after_conclusion: ConclusionScore | None,
    failure_reasons: tuple[str, ...],
) -> AblationResult:
    before_state = (
        before_conclusion.state
        if before_conclusion is not None
        else before_summary.uncertainty_state
    )
    after_state = (
        after_conclusion.state
        if after_conclusion is not None
        else after_summary.uncertainty_state
    )
    before_business_state = (
        before_conclusion.business_state if before_conclusion is not None else ""
    )
    after_business_state = (
        after_conclusion.business_state if after_conclusion is not None else ""
    )
    before_target_found = (
        before_conclusion.target_found if before_conclusion is not None else True
    )
    after_target_found = (
        after_conclusion.target_found if after_conclusion is not None else True
    )
    state_changed = (
        after_state != before_state
        or before_business_state != after_business_state
        or before_target_found != after_target_found
    )
    if before_conclusion is not None and after_conclusion is not None:
        threshold_crossed = (
            before_conclusion.threshold_crossed
            != after_conclusion.threshold_crossed
        )
    else:
        threshold_crossed = False
    reasons = list(failure_reasons)
    if after_conclusion is not None:
        reasons.extend(after_conclusion.failure_reasons)
    else:
        reasons.extend(after_summary.uncertainty_reasons)
    return AblationResult(
        ablation_type=ablation_type,
        removed_group_id=group_id,
        removed_share=share,
        removed_count=max(
            0,
            before_summary.raw_evidence_count - after_summary.raw_evidence_count,
        ),
        before_state=before_state,
        after_state=after_state,
        before_effective_sample_size=before_summary.effective_sample_size,
        after_effective_sample_size=after_summary.effective_sample_size,
        before_score=(
            before_conclusion.score
            if before_conclusion is not None
            else None
        ),
        after_score=(
            after_conclusion.score if after_conclusion is not None else None
        ),
        before_rank=(
            before_conclusion.rank
            if before_conclusion is not None
            else None
        ),
        after_rank=(
            after_conclusion.rank if after_conclusion is not None else None
        ),
        before_business_state=before_business_state,
        after_business_state=after_business_state,
        before_target_found=before_target_found,
        after_target_found=after_target_found,
        threshold_crossed=threshold_crossed,
        state_changed=state_changed,
        failure_reasons=tuple(dict.fromkeys(reasons)),
    )


def _group_field(ablation_type: AblationType) -> str:
    if ablation_type == "source":
        return "source_id"
    if ablation_type == "enterprise":
        return "enterprise_id"
    return "template_cluster_id"


def _certificate_status(
    baseline: EvidenceIndependenceSummary,
    ablations: tuple[AblationResult, ...],
    baseline_state: UncertaintyState | None = None,
) -> tuple[CertificateStatus, tuple[str, ...]]:
    state = baseline_state or baseline.uncertainty_state
    if state == "blocked":
        return "not_applicable", ("baseline_blocked",)
    if state in ("not_observed", "insufficient_evidence"):
        return "insufficient_evidence", ("baseline_insufficient_evidence",)
    if state in ("unresolved", "stale_observation"):
        return "not_applicable", (f"baseline_{state}",)
    mandatory = tuple(
        ablation
        for ablation in ablations
        if ablation.ablation_type in ("source", "enterprise", "template")
    )
    if any(
        ablation.removed_count == 0
        or "no_group_removed" in ablation.failure_reasons
        for ablation in mandatory
    ):
        return "not_applicable", ("required_ablation_not_executed",)
    after_states = tuple(ablation.after_state for ablation in mandatory)
    if state == "source_concentrated":
        if any(after_state == "ok" for after_state in after_states):
            return "conditionally_robust", ("baseline_source_concentrated",)
        return "source_fragile", ("baseline_source_concentrated",)
    if any(
        not ablation.after_target_found or ablation.threshold_crossed
        for ablation in mandatory
    ):
        return "source_fragile", ("business_target_or_threshold_failed",)
    if any(
        after_state in ("insufficient_evidence", "not_observed")
        for after_state in after_states
    ):
        return "source_fragile", ("ablation_reduced_evidence_below_gate",)
    if any(
        after_state != "ok"
        or ablation.before_business_state != ablation.after_business_state
        or ablation.before_rank != ablation.after_rank
        for ablation, after_state in zip(mandatory, after_states, strict=True)
    ):
        return "conditionally_robust", ("ablation_changed_conclusion_state",)
    return "robust", ("all_mandatory_ablations_survived",)


def _require_unique_evidence_ids(records: Sequence[EvidenceRecord]) -> None:
    ids = [record.evidence_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("evidence_id values must be unique")


__all__ = [
    "INDEPENDENCE_WEIGHT_RULES_V1",
    "build_certificate",
    "build_cluster_freshness",
    "build_evidence_aggregation",
    "build_independent_clusters",
    "build_independent_clusters_v3_1_with_decisions",
    "build_summary",
    "cluster_weights",
    "cluster_weights_v2",
    "cluster_weights_v3",
    "cluster_weights_v4",
    "cluster_weights_v5",
    "config_hash",
    "derive_uncertainty_state",
    "effective_sample_size",
    "entropy_effective_size",
    "normalized_url",
    "project_mass_capped_weights",
    "project_mass_capped_weights_dual",
    "run_ablation",
    "text_fingerprint",
]
