"""Explicit Candidate lineage for cross-window structural evolution.

The lineage layer is deliberately additive.  It never rewrites ordinary
identity assignment, lifecycle observations, or cluster refinement.  A
``CandidateLineageRelation`` is an independent structure that explains how a
historical Candidate can continue, split into multiple current Candidates, or
merge into one new Candidate.  The resolver accepts only production-observable
evidence; evaluator/Gold metadata is rejected by the leakage audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping, Sequence


LINEAGE_MODEL_VERSION = "candidate-lineage-model.v1"
LINEAGE_RESOLVER_VERSION = "candidate-lineage-resolver.v1"
LINEAGE_RESOLVER_VERSION_V2 = "candidate-lineage-resolver.v2"
LINEAGE_EVALUATOR_VERSION = "candidate-lineage-evaluator.v1"

RELATION_TYPES = ("CONTINUE", "SPLIT", "MERGE")
DECISION_TYPES = ("CONTINUE", "SPLIT", "MERGE", "NEW", "REVIEW")

FORBIDDEN_LINEAGE_METADATA_KEYS = frozenset(
    {
        "gold_candidate_id",
        "gold_candidate_ids",
        "gold_candidate_rank",
        "union_gold_candidate_rank",
        "unit_gold_id",
        "gold_unit_id",
        "gold_pair/case_id",
        "gold_ref",
        "gold_relation",
        "expected_candidate_id",
        "expected_lineage",
        "formal_verdict",
        "ambiguity_verdict",
        "ambiguity_classification",
        "case_label",
        "formal_label",
    }
)

DEFAULT_CANDIDATE_LINEAGE_CONFIG: dict[str, float] = {
    "continuity_confidence_min": 0.55,
    "split_role_structure_min": 0.45,
    "merge_role_distinction_max": 0.35,
    "merge_role_alignment_min": 0.40,
    "company_bias_max_weight": 0.25,
    "split_min_child_clusters": 2.0,
    "merge_min_parent_candidates": 2.0,
}


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_CANDIDATE_LINEAGE_CONFIG, **(config or {})}
    for key in (
        "continuity_confidence_min",
        "split_role_structure_min",
        "merge_role_distinction_max",
        "merge_role_alignment_min",
        "company_bias_max_weight",
    ):
        value = float(merged[key])
        if not 0 <= value <= 1:
            raise ValueError(f"candidate lineage {key} must be between zero and one")
    for key in ("split_min_child_clusters", "merge_min_parent_candidates"):
        if float(merged[key]) < 2:
            raise ValueError(f"candidate lineage {key} must be at least two")
    if merged.get("lineage_admission_version") == "v2":
        merged.setdefault("split_min_parent_continuity_v2", 0.60)
        merged.setdefault("split_min_child_role_distinction_v2", 0.55)
        merged.setdefault("split_ambiguity_margin_min_v2", 0.01)
        merged.setdefault("merge_min_parent_continuity_v2", 0.60)
        merged.setdefault("merge_parent_distinction_max_v2", 0.25)
        merged.setdefault("merge_role_alignment_min_v2", 0.50)
        merged.setdefault("merge_min_gain_v2", 0.02)
        merged.setdefault("merge_ambiguity_margin_min_v2", 0.01)
        for key in (
            "split_min_parent_continuity_v2",
            "split_min_child_role_distinction_v2",
            "split_ambiguity_margin_min_v2",
            "merge_min_parent_continuity_v2",
            "merge_parent_distinction_max_v2",
            "merge_role_alignment_min_v2",
            "merge_min_gain_v2",
            "merge_ambiguity_margin_min_v2",
        ):
            value = float(merged[key])
            if not 0 <= value <= 1:
                raise ValueError(f"candidate lineage {key} must be between zero and one")
    return merged


def lineage_config_version(
    config: Mapping[str, Any] | None = None,
) -> str:
    merged = _config(config)
    digest = sha256(_canonical_json(merged).encode("utf-8")).hexdigest()[:16]
    return f"{LINEAGE_RESOLVER_VERSION}/sha256:{digest}"


@dataclass(frozen=True)
class CandidateLineageProfile:
    """Frozen, production-observable historical Candidate profile."""

    candidate_id: str
    window_id: str
    titles: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    responsibilities: frozenset[str] = frozenset()
    member_jd_ids: frozenset[str] = frozenset()
    company_ids: frozenset[str] = frozenset()
    source_evidence_refs: frozenset[str] = frozenset()
    observed_window_ids: tuple[str, ...] = ()
    support_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "window_id": self.window_id,
            "titles": sorted(self.titles),
            "skills": sorted(self.skills),
            "responsibilities": sorted(self.responsibilities),
            "member_jd_ids": sorted(self.member_jd_ids),
            "company_ids": sorted(self.company_ids),
            "source_evidence_refs": sorted(self.source_evidence_refs),
            "observed_window_ids": list(self.observed_window_ids),
            "support_count": self.support_count,
        }


@dataclass(frozen=True)
class CurrentClusterProfile:
    """Refined current cluster with structural safety flags."""

    cluster_id: str
    window_id: str
    titles: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    responsibilities: frozenset[str] = frozenset()
    member_jd_ids: frozenset[str] = frozenset()
    company_ids: frozenset[str] = frozenset()
    source_evidence_refs: frozenset[str] = frozenset()
    coherent: bool = True
    bundle_safe: bool = True
    support_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "window_id": self.window_id,
            "titles": sorted(self.titles),
            "skills": sorted(self.skills),
            "responsibilities": sorted(self.responsibilities),
            "member_jd_ids": sorted(self.member_jd_ids),
            "company_ids": sorted(self.company_ids),
            "source_evidence_refs": sorted(self.source_evidence_refs),
            "coherent": self.coherent,
            "bundle_safe": self.bundle_safe,
            "support_count": self.support_count,
        }


@dataclass(frozen=True)
class OrdinaryIdentityProposal:
    """Existing automatic assignment evidence for one current cluster."""

    cluster_id: str
    candidate_id: str | None
    decision: str
    identity_score: float | None = None
    decision_basis: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "identity_score": self.identity_score,
            "decision_basis": list(self.decision_basis),
        }


@dataclass(frozen=True)
class LineageEvidence:
    name: str
    value: float
    kind: str
    detail: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(float(self.value), 6),
            "kind": self.kind,
            "detail": list(self.detail),
        }


@dataclass(frozen=True)
class LineageHypothesis:
    hypothesis_id: str
    relation_type: str
    source_candidate_ids: tuple[str, ...]
    target_cluster_ids: tuple[str, ...]
    confidence: float
    evidence: tuple[LineageEvidence, ...]
    decision_basis: tuple[str, ...]
    review_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "relation_type": self.relation_type,
            "source_candidate_ids": list(self.source_candidate_ids),
            "target_cluster_ids": list(self.target_cluster_ids),
            "confidence": round(float(self.confidence), 6),
            "evidence": [item.to_dict() for item in self.evidence],
            "decision_basis": list(self.decision_basis),
            "review_required": self.review_required,
        }


@dataclass(frozen=True)
class CandidateLineageRelation:
    """Independent Candidate lineage relation (never a masked identity ``same``)."""

    relation_id: str
    relation_type: str
    source_candidate_ids: tuple[str, ...]
    target_candidate_ids: tuple[str, ...]
    source_window_id: str
    target_window_id: str
    confidence: float
    evidence: tuple[LineageEvidence, ...]
    decision_basis: tuple[str, ...]
    review_required: bool
    algorithm_version: str = LINEAGE_RESOLVER_VERSION
    model_version: str = LINEAGE_MODEL_VERSION
    source_cluster_ids: tuple[str, ...] = ()
    target_cluster_ids: tuple[str, ...] = ()
    proposed_target_candidate_ids: tuple[str, ...] = ()
    support_inflation: int = 0
    observation_delta: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "relation_type": self.relation_type,
            "source_candidate_ids": list(self.source_candidate_ids),
            "target_candidate_ids": list(self.target_candidate_ids),
            "source_window_id": self.source_window_id,
            "target_window_id": self.target_window_id,
            "confidence": round(float(self.confidence), 6),
            "evidence": [item.to_dict() for item in self.evidence],
            "decision_basis": list(self.decision_basis),
            "review_required": self.review_required,
            "algorithm_version": self.algorithm_version,
            "model_version": self.model_version,
            "source_cluster_ids": list(self.source_cluster_ids),
            "target_cluster_ids": list(self.target_cluster_ids),
            "proposed_target_candidate_ids": list(self.proposed_target_candidate_ids),
            "support_inflation": self.support_inflation,
            "observation_delta": self.observation_delta,
        }


@dataclass(frozen=True)
class LineageDecision:
    decision_type: str
    window_id: str
    cluster_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    review_required: bool
    decision_basis: tuple[str, ...]
    hypotheses: tuple[LineageHypothesis, ...] = ()
    confidence: float | None = None
    relation: CandidateLineageRelation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type,
            "window_id": self.window_id,
            "cluster_ids": list(self.cluster_ids),
            "candidate_ids": list(self.candidate_ids),
            "review_required": self.review_required,
            "decision_basis": list(self.decision_basis),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "confidence": round(float(self.confidence), 6) if self.confidence is not None else None,
            "relation": self.relation.to_dict() if self.relation is not None else None,
        }


@dataclass(frozen=True)
class LineageResolution:
    relations: tuple[CandidateLineageRelation, ...]
    decisions: tuple[LineageDecision, ...]
    config_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relations": [item.to_dict() for item in self.relations],
            "decisions": [item.to_dict() for item in self.decisions],
            "config_version": self.config_version,
        }


def _temporal_adjacency(
    source_window_id: str,
    target_window_id: str,
    window_order: Sequence[str],
) -> float:
    try:
        return 1.0 if window_order.index(target_window_id) == window_order.index(source_window_id) + 1 else 0.0
    except ValueError:
        return 0.0


def _continuity_score(
    profile: CandidateLineageProfile,
    cluster: CurrentClusterProfile,
    window_order: Sequence[str],
) -> float:
    membership = _jaccard(profile.member_jd_ids, cluster.member_jd_ids)
    title = _jaccard(profile.titles, cluster.titles)
    responsibility = _jaccard(profile.responsibilities, cluster.responsibilities)
    skill = _jaccard(profile.skills, cluster.skills)
    temporal = _temporal_adjacency(profile.window_id, cluster.window_id, window_order)
    return round(
        0.30 * membership + 0.20 * title + 0.20 * responsibility + 0.20 * skill + 0.10 * temporal,
        6,
    )


def _role_structure_score(
    profile: CandidateLineageProfile,
    cluster: CurrentClusterProfile,
) -> float:
    responsibility = _jaccard(profile.responsibilities, cluster.responsibilities)
    skill = _jaccard(profile.skills, cluster.skills)
    return round((responsibility + skill) / 2.0, 6)


def _role_distinction(left: CurrentClusterProfile, right: CurrentClusterProfile) -> float:
    responsibility = 1.0 - _jaccard(left.responsibilities, right.responsibilities)
    skill = 1.0 - _jaccard(left.skills, right.skills)
    return round(max(responsibility, skill), 6)


def _candidate_role_distinction(
    left: CandidateLineageProfile,
    right: CandidateLineageProfile,
) -> float:
    responsibility = 1.0 - _jaccard(left.responsibilities, right.responsibilities)
    skill = 1.0 - _jaccard(left.skills, right.skills)
    return round(max(responsibility, skill), 6)


def _company_separation(left: CurrentClusterProfile, right: CurrentClusterProfile) -> float:
    if not left.company_ids or not right.company_ids:
        return 0.0
    return 1.0 if left.company_ids.isdisjoint(right.company_ids) else 0.0


def _candidate_company_separation(
    left: CandidateLineageProfile,
    right: CandidateLineageProfile,
) -> float:
    if not left.company_ids or not right.company_ids:
        return 0.0
    return 1.0 if left.company_ids.isdisjoint(right.company_ids) else 0.0


def _cluster_order_key(cluster: CurrentClusterProfile) -> tuple[Any, ...]:
    """Stable, content-first ordering independent of random cluster UUIDs."""
    return (
        tuple(sorted(cluster.member_jd_ids)),
        tuple(sorted(cluster.titles)),
        tuple(sorted(cluster.skills)),
        tuple(sorted(cluster.responsibilities)),
        tuple(sorted(cluster.company_ids)),
        tuple(sorted(cluster.source_evidence_refs)),
        cluster.window_id,
        cluster.support_count,
        cluster.coherent,
        cluster.bundle_safe,
        cluster.cluster_id,
    )


def _proposal_score(
    proposal: OrdinaryIdentityProposal,
    profile: CandidateLineageProfile,
    cluster: CurrentClusterProfile,
    window_order: Sequence[str],
) -> float:
    if proposal.identity_score is not None:
        return min(1.0, max(0.0, float(proposal.identity_score)))
    return _continuity_score(profile, cluster, window_order)


def _relation_id(
    source_window_id: str,
    target_window_id: str,
    relation_type: str,
    index: int,
) -> str:
    return f"relation-{source_window_id}-{target_window_id}-{relation_type}-{index:04d}"


def _split_candidate_id(
    parent_id: str,
    target_window_id: str,
    cluster_id: str,
) -> str:
    return f"cand-lineage-split-{target_window_id}-{parent_id}-{cluster_id}"[:64]


def _merged_candidate_id(target_window_id: str, cluster_id: str) -> str:
    return f"cand-lineage-merge-{target_window_id}-{cluster_id}"[:64]


def resolve_candidate_lineage(
    *,
    source_window_id: str,
    target_window_id: str,
    historical_candidates: Sequence[CandidateLineageProfile],
    current_clusters: Sequence[CurrentClusterProfile],
    ordinary_proposals: Sequence[OrdinaryIdentityProposal],
    window_order: Sequence[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> LineageResolution:
    """Resolve deterministic, conservative Candidate lineage hypotheses.

    Ordinary 1-to-1 identity continues first.  SPLIT/MERGE are generated only
    when the ordinary path leaves a structurally inconsistent local
    neighborhood and the production-observable role evidence is strong.
    """
    merged = _config(config)
    admission_version = str(merged.get("lineage_admission_version") or "v1")
    resolver_version = (
        LINEAGE_RESOLVER_VERSION_V2
        if admission_version == "v2"
        else LINEAGE_RESOLVER_VERSION
    )
    ordered_windows = tuple(window_order or ())
    candidates = sorted(
        historical_candidates,
        key=lambda item: item.candidate_id,
    )
    clusters = sorted(
        current_clusters,
        key=_cluster_order_key,
    )
    by_cluster = {item.cluster_id: item for item in clusters}
    proposals = sorted(
        ordinary_proposals,
        key=lambda item: (
            _cluster_order_key(by_cluster[item.cluster_id]),
            item.candidate_id or "",
            item.decision,
        ),
    )
    by_candidate = {item.candidate_id: item for item in candidates}
    proposals_by_cluster: dict[str, list[OrdinaryIdentityProposal]] = {}
    same_by_candidate: dict[str, list[tuple[str, OrdinaryIdentityProposal]]] = {}
    same_by_cluster: dict[str, list[tuple[str, OrdinaryIdentityProposal]]] = {}
    for proposal in proposals:
        if proposal.cluster_id not in by_cluster:
            continue
        proposals_by_cluster.setdefault(proposal.cluster_id, []).append(proposal)
        if proposal.decision == "same" and proposal.candidate_id in by_candidate:
            key = proposal.candidate_id
            same_by_candidate.setdefault(key, []).append(
                (proposal.cluster_id, proposal)
            )
            same_by_cluster.setdefault(proposal.cluster_id, []).append(
                (key, proposal)
            )

    split_groups = {
        candidate_id: entries
        for candidate_id, entries in same_by_candidate.items()
        if len(entries) >= int(merged["split_min_child_clusters"])
    }
    merge_groups = {
        cluster_id: entries
        for cluster_id, entries in same_by_cluster.items()
        if len(entries) >= int(merged["merge_min_parent_candidates"])
    }
    split_group_clusters = {
        cluster_id
        for entries in split_groups.values()
        for cluster_id, _ in entries
    }
    overlap_cluster_ids = sorted(
        set(merge_groups) & split_group_clusters,
        key=lambda cluster_id: _cluster_order_key(by_cluster[cluster_id]),
    )

    relations: list[CandidateLineageRelation] = []
    decisions: list[LineageDecision] = []
    consumed_cluster_ids: set[str] = set()
    relation_index = 0

    def review_decision(
        decision_basis: Sequence[str],
        cluster_ids: Sequence[str],
        candidate_ids: Sequence[str] = (),
        hypotheses: Sequence[LineageHypothesis] = (),
        confidence: float | None = None,
    ) -> LineageDecision:
        return LineageDecision(
            decision_type="REVIEW",
            window_id=target_window_id,
            cluster_ids=tuple(sorted(cluster_ids)),
            candidate_ids=tuple(sorted(candidate_ids)),
            review_required=True,
            decision_basis=tuple(decision_basis),
            hypotheses=tuple(hypotheses),
            confidence=confidence,
        )

    def make_evidence(
        *,
        ordinary_value: float,
        profile: CandidateLineageProfile,
        cluster: CurrentClusterProfile,
        temporal_value: float,
        role_value: float,
        company_value: float,
        coherence_value: float,
        bundle_value: float,
        extra: Sequence[LineageEvidence] = (),
    ) -> tuple[LineageEvidence, ...]:
        return (
            LineageEvidence(
                "ordinary_assignment_continuity",
                ordinary_value,
                "ordinary_assignment",
            ),
            LineageEvidence(
                "membership_overlap",
                _jaccard(profile.member_jd_ids, cluster.member_jd_ids),
                "membership",
            ),
            LineageEvidence(
                "title_overlap",
                _jaccard(profile.titles, cluster.titles),
                "title",
            ),
            LineageEvidence(
                "responsibility_overlap",
                _jaccard(profile.responsibilities, cluster.responsibilities),
                "responsibility",
            ),
            LineageEvidence(
                "skill_overlap",
                _jaccard(profile.skills, cluster.skills),
                "skill",
            ),
            LineageEvidence("temporal_adjacency", temporal_value, "temporal"),
            LineageEvidence("role_structure_alignment", role_value, "role_structure"),
            LineageEvidence("company_auxiliary", company_value, "company"),
            LineageEvidence("cluster_coherent", coherence_value, "cluster_refinement"),
            LineageEvidence("bundle_safe", bundle_value, "cluster_refinement"),
            *extra,
        )

    # Unambiguous ordinary continue (single candidate, single cluster).
    for cluster in clusters:
        same = same_by_cluster.get(cluster.cluster_id, ())
        if len(same) != 1:
            continue
        candidate_id, proposal = same[0]
        if len(same_by_candidate.get(candidate_id, ())) != 1:
            continue
        profile = by_candidate[candidate_id]
        score = _proposal_score(proposal, profile, cluster, ordered_windows)
        role = _role_structure_score(profile, cluster)
        temporal = _temporal_adjacency(profile.window_id, cluster.window_id, ordered_windows)
        evidence = make_evidence(
            ordinary_value=score,
            profile=profile,
            cluster=cluster,
            temporal_value=temporal,
            role_value=role,
            company_value=(
                1.0 if profile.company_ids and profile.company_ids.isdisjoint(cluster.company_ids) else 0.0
            ),
            coherence_value=1.0 if cluster.coherent else 0.0,
            bundle_value=1.0 if cluster.bundle_safe else 0.0,
        )
        relation = CandidateLineageRelation(
            relation_id=_relation_id(
                source_window_id,
                target_window_id,
                "CONTINUE",
                relation_index,
            ),
            relation_type="CONTINUE",
            source_candidate_ids=(candidate_id,),
            target_candidate_ids=(candidate_id,),
            source_window_id=source_window_id,
            target_window_id=target_window_id,
            confidence=round(score, 6),
            evidence=evidence,
            decision_basis=("ordinary_1to1_continue",),
            review_required=False,
            algorithm_version=resolver_version,
            source_cluster_ids=(),
            target_cluster_ids=(cluster.cluster_id,),
            proposed_target_candidate_ids=(candidate_id,),
        )
        relation_index += 1
        relations.append(relation)
        consumed_cluster_ids.add(cluster.cluster_id)
        decisions.append(
            LineageDecision(
                decision_type="CONTINUE",
                window_id=target_window_id,
                cluster_ids=(cluster.cluster_id,),
                candidate_ids=(candidate_id,),
                review_required=False,
                decision_basis=("ordinary_1to1_continue",),
                hypotheses=(
                    LineageHypothesis(
                        hypothesis_id=f"hyp-{relation.relation_id}",
                        relation_type="CONTINUE",
                        source_candidate_ids=(candidate_id,),
                        target_cluster_ids=(cluster.cluster_id,),
                        confidence=round(score, 6),
                        evidence=evidence,
                        decision_basis=("ordinary_1to1_continue",),
                    ),
                ),
                confidence=round(score, 6),
                relation=relation,
            )
        )

    # Conservative guard: never auto-resolve an overlapping split/merge
    # neighborhood, and never turn a cluster-over-merge error into lineage.
    for cluster_id in overlap_cluster_ids:
        cluster = by_cluster[cluster_id]
        decisions.append(
            review_decision(
                ("overlapping_split_merge_neighborhood",),
                (cluster_id,),
                tuple(candidate_id for candidate_id, _ in merge_groups[cluster_id]),
            )
        )
        consumed_cluster_ids.add(cluster_id)

    # MERGE: multiple historical Candidates with strong continuity to one
    # coherent refined cluster that cannot be distinguished any further.
    for cluster_id in sorted(
        merge_groups,
        key=lambda item: _cluster_order_key(by_cluster[item]),
    ):
        if cluster_id in consumed_cluster_ids:
            continue
        cluster = by_cluster[cluster_id]
        entries = sorted(
            merge_groups[cluster_id],
            key=lambda item: (item[0],),
        )
        parent_ids = [candidate_id for candidate_id, _ in entries]
        parents = [by_candidate[candidate_id] for candidate_id in parent_ids]
        scores = [
            _proposal_score(proposal, parent, cluster, ordered_windows)
            for parent, (_, proposal) in zip(parents, entries, strict=True)
        ]
        alignments = [
            _role_structure_score(parent, cluster)
            for parent in parents
        ]
        distinction = max(
            (
                _candidate_role_distinction(left, right)
                for left, right in zip(
                    parents,
                    parents[1:],
                    strict=False,
                )
            ),
            default=0.0,
        )
        best_single = max(scores)
        merge_confidence = min(
            1.0,
            round(best_single + 0.08 * (1.0 - distinction) + 0.02, 6),
        )
        bases = [
            "multiple_parents_same_cluster",
            f"cluster_coherent={cluster.coherent}",
            f"parent_role_distinction={distinction}",
            f"merge_stronger_than_single={merge_confidence > best_single}",
        ]
        ambiguity_margin: float | None = None
        v2_reject_reason: str | None = None
        if admission_version == "v2":
            ambiguity_margin = round(merge_confidence - best_single, 6)
            if min(scores) < float(merged["merge_min_parent_continuity_v2"]):
                v2_reject_reason = "merge_parent_continuity_below_v2_min"
            elif distinction > float(merged["merge_parent_distinction_max_v2"]):
                v2_reject_reason = "parents_still_distinguishable_v2"
            elif min(alignments) < float(merged["merge_role_alignment_min_v2"]):
                v2_reject_reason = "parent_role_alignment_too_low_v2"
            elif ambiguity_margin < float(merged["merge_ambiguity_margin_min_v2"]):
                v2_reject_reason = "merge_ambiguity_margin_non_positive_v2"
            bases.append(f"admission_version={admission_version}")
            bases.append(f"ambiguity_margin={ambiguity_margin}")
        hypotheses = (
            LineageHypothesis(
                hypothesis_id=f"hyp-merge-{cluster_id}",
                relation_type="MERGE",
                source_candidate_ids=tuple(parent_ids),
                target_cluster_ids=(cluster_id,),
                confidence=merge_confidence,
                evidence=(
                    LineageEvidence("merge_parent_distinction", distinction, "role_structure"),
                    LineageEvidence("merge_best_single_parent", best_single, "ordinary_assignment"),
                    LineageEvidence(
                        "merge_vs_single_parent_gain",
                        round(merge_confidence - best_single, 6),
                        "role_structure",
                    ),
                    LineageEvidence(
                        "company_auxiliary",
                        max(
                            (
                                _candidate_company_separation(left, right)
                                for left, right in zip(parents, parents[1:], strict=False)
                            ),
                            default=0.0,
                        ),
                        "company",
                    ),
                )
                + (
                    (
                        LineageEvidence(
                            "ambiguity_margin",
                            float(ambiguity_margin),
                            "admission",
                        ),
                    )
                    if ambiguity_margin is not None
                    else ()
                ),
                decision_basis=tuple(bases),
            ),
        )
        reject = (
            not cluster.coherent
            or distinction > float(merged["merge_role_distinction_max"])
            or min(alignments) < float(merged["merge_role_alignment_min"])
            or merge_confidence <= best_single
            or v2_reject_reason is not None
        )
        if reject:
            reason = (
                "cluster_not_coherent_merge_guard"
                if not cluster.coherent
                else (
                    v2_reject_reason
                    if v2_reject_reason is not None
                    else (
                        "parents_still_distinguishable"
                        if distinction > float(merged["merge_role_distinction_max"])
                        else (
                            "parent_role_alignment_too_low"
                            if min(alignments)
                            < float(merged["merge_role_alignment_min"])
                            else "merge_not_stronger_than_single_parent"
                        )
                    )
                )
            )
            decisions.append(
                review_decision(
                    ("merge_rejected", reason),
                    (cluster_id,),
                    parent_ids,
                    hypotheses=hypotheses,
                    confidence=merge_confidence,
                )
            )
            consumed_cluster_ids.add(cluster_id)
            continue
        target_id = _merged_candidate_id(target_window_id, cluster_id)
        temporal = _temporal_adjacency(
            max((parent.window_id for parent in parents), default=source_window_id),
            cluster.window_id,
            ordered_windows,
        )
        relation = CandidateLineageRelation(
            relation_id=_relation_id(
                source_window_id,
                target_window_id,
                "MERGE",
                relation_index,
            ),
            relation_type="MERGE",
            source_candidate_ids=tuple(parent_ids),
            target_candidate_ids=(target_id,),
            source_window_id=source_window_id,
            target_window_id=target_window_id,
            confidence=merge_confidence,
            evidence=hypotheses[0].evidence,
            decision_basis=tuple(bases),
            review_required=False,
            algorithm_version=resolver_version,
            source_cluster_ids=(),
            target_cluster_ids=(cluster_id,),
            proposed_target_candidate_ids=(target_id,),
        )
        relation_index += 1
        relations.append(relation)
        consumed_cluster_ids.add(cluster_id)
        decisions.append(
            LineageDecision(
                decision_type="MERGE",
                window_id=target_window_id,
                cluster_ids=(cluster_id,),
                candidate_ids=tuple(parent_ids),
                review_required=False,
                decision_basis=tuple(bases),
                hypotheses=hypotheses,
                confidence=merge_confidence,
                relation=relation,
            )
        )

    # SPLIT: one historical Candidate with continuity to multiple refined
    # clusters that cannot be safely bundled and show role-structure separation.
    for candidate_id in sorted(split_groups):
        entries = sorted(
            split_groups[candidate_id],
            key=lambda item: _cluster_order_key(by_cluster[item[0]]),
        )
        child_ids = [cluster_id for cluster_id, _ in entries]
        if any(cluster_id in consumed_cluster_ids for cluster_id in child_ids):
            continue
        profile = by_candidate[candidate_id]
        children = [by_cluster[cluster_id] for cluster_id in child_ids]
        if not all(child.coherent for child in children):
            decisions.append(
                review_decision(
                    ("split_rejected", "child_cluster_not_coherent"),
                    child_ids,
                    (candidate_id,),
                )
            )
            consumed_cluster_ids.update(child_ids)
            continue
        if all(child.bundle_safe for child in children):
            continue
        role_structure = min(
            (
                _role_distinction(left, right)
                for left, right in zip(children, children[1:], strict=False)
            ),
            default=0.0,
        )
        scores = [
            _proposal_score(proposal, profile, child, ordered_windows)
            for child, (_, proposal) in zip(children, entries, strict=True)
        ]
        company_separation = min(
            (
                _company_separation(left, right)
                for left, right in zip(children, children[1:], strict=False)
            ),
            default=0.0,
        )
        company_aux = min(
            company_separation,
            float(merged["company_bias_max_weight"]) / 0.25,
        ) * 0.05
        min_score = min(scores)
        confidence = min(
            1.0,
            round(
                min_score * (0.55 + 0.45 * role_structure) + company_aux,
                6,
            ),
        )
        role_ok = role_structure >= float(merged["split_role_structure_min"])
        bases = [
            "one_parent_multiple_child_clusters",
            f"role_structure={role_structure}",
            f"children_not_bundle_safe={not all(child.bundle_safe for child in children)}",
            f"company_auxiliary={company_separation}",
        ]
        ambiguity_margin: float | None = None
        v2_reject_reason: str | None = None
        if admission_version == "v2":
            ambiguity_margin = round(
                min_score - float(merged["continuity_confidence_min"]),
                6,
            )
            if role_structure < float(merged["split_min_child_role_distinction_v2"]):
                v2_reject_reason = "child_role_separation_below_v2_min"
            elif min_score < float(merged["split_min_parent_continuity_v2"]):
                v2_reject_reason = "parent_continuity_below_v2_min"
            elif ambiguity_margin < float(merged["split_ambiguity_margin_min_v2"]):
                v2_reject_reason = "split_ambiguity_margin_non_positive_v2"
            bases.append(f"admission_version={admission_version}")
            bases.append(f"ambiguity_margin={ambiguity_margin}")
        admission_evidence = (
            (
                LineageEvidence(
                    "ambiguity_margin",
                    float(ambiguity_margin),
                    "admission",
                ),
            )
            if ambiguity_margin is not None
            else ()
        )
        hypotheses = tuple(
            LineageHypothesis(
                hypothesis_id=f"hyp-split-{candidate_id}-{child_id}",
                relation_type="SPLIT",
                source_candidate_ids=(candidate_id,),
                target_cluster_ids=(child_id,),
                confidence=confidence,
                evidence=(
                    LineageEvidence("child_role_distinction", role_structure, "role_structure"),
                    LineageEvidence("min_child_continuity", min_score, "ordinary_assignment"),
                    LineageEvidence("company_auxiliary", company_separation, "company"),
                )
                + admission_evidence,
                decision_basis=tuple(bases),
            )
            for child_id in child_ids
        )
        if (
            not role_ok
            or confidence < float(merged["continuity_confidence_min"])
            or v2_reject_reason is not None
        ):
            reason = (
                "role_structure_below_threshold"
                if not role_ok
                else (
                    v2_reject_reason
                    if v2_reject_reason is not None
                    else "continuity_confidence_below_threshold"
                )
            )
            decisions.append(
                review_decision(
                    ("split_rejected", reason),
                    child_ids,
                    (candidate_id,),
                    hypotheses=hypotheses,
                    confidence=confidence,
                )
            )
            consumed_cluster_ids.update(child_ids)
            continue
        target_ids = tuple(
            _split_candidate_id(candidate_id, target_window_id, child_id)
            for child_id in child_ids
        )
        temporal = _temporal_adjacency(profile.window_id, children[0].window_id, ordered_windows)
        evidence = make_evidence(
            ordinary_value=min_score,
            profile=profile,
            cluster=children[0],
            temporal_value=temporal,
            role_value=role_structure,
            company_value=company_separation,
            coherence_value=1.0,
            bundle_value=0.0,
            extra=(
                LineageEvidence("child_role_distinction", role_structure, "role_structure"),
                *admission_evidence,
            ),
        )
        relation = CandidateLineageRelation(
            relation_id=_relation_id(
                source_window_id,
                target_window_id,
                "SPLIT",
                relation_index,
            ),
            relation_type="SPLIT",
            source_candidate_ids=(candidate_id,),
            target_candidate_ids=target_ids,
            source_window_id=source_window_id,
            target_window_id=target_window_id,
            confidence=confidence,
            evidence=evidence,
            decision_basis=tuple(bases),
            review_required=False,
            algorithm_version=resolver_version,
            source_cluster_ids=(),
            target_cluster_ids=tuple(child_ids),
            proposed_target_candidate_ids=target_ids,
        )
        relation_index += 1
        relations.append(relation)
        consumed_cluster_ids.update(child_ids)
        decisions.append(
            LineageDecision(
                decision_type="SPLIT",
                window_id=target_window_id,
                cluster_ids=tuple(child_ids),
                candidate_ids=(candidate_id,),
                review_required=False,
                decision_basis=tuple(bases),
                hypotheses=hypotheses,
                confidence=confidence,
                relation=relation,
            )
        )

    # CONTINUE bundles: multiple current clusters are safe to bundle into one
    # current Candidate, so the historical identity continues unchanged.
    for candidate_id in sorted(same_by_candidate):
        entries = sorted(
            same_by_candidate[candidate_id],
            key=lambda item: _cluster_order_key(by_cluster[item[0]]),
        )
        cluster_ids = [cluster_id for cluster_id, _ in entries]
        if not cluster_ids or any(cluster_id in consumed_cluster_ids for cluster_id in cluster_ids):
            continue
        profile = by_candidate[candidate_id]
        children = [by_cluster[cluster_id] for cluster_id in cluster_ids]
        scores = [
            _proposal_score(proposal, profile, child, ordered_windows)
            for child, (_, proposal) in zip(children, entries, strict=True)
        ]
        role_structure = min(
            (
                _role_distinction(left, right)
                for left, right in zip(children, children[1:], strict=False)
            ),
            default=0.0,
        )
        if not all(child.bundle_safe for child in children):
            decisions.append(
                review_decision(
                    ("continue_rejected", "clusters_not_bundle_safe_without_split_evidence"),
                    cluster_ids,
                    (candidate_id,),
                    confidence=min(scores),
                )
            )
            consumed_cluster_ids.update(cluster_ids)
            continue
        score = min(scores)
        temporal = _temporal_adjacency(profile.window_id, children[0].window_id, ordered_windows)
        evidence = make_evidence(
            ordinary_value=score,
            profile=profile,
            cluster=children[0],
            temporal_value=temporal,
            role_value=role_structure,
            company_value=0.0,
            coherence_value=1.0 if all(child.coherent for child in children) else 0.0,
            bundle_value=1.0,
        )
        relation = CandidateLineageRelation(
            relation_id=_relation_id(
                source_window_id,
                target_window_id,
                "CONTINUE",
                relation_index,
            ),
            relation_type="CONTINUE",
            source_candidate_ids=(candidate_id,),
            target_candidate_ids=(candidate_id,),
            source_window_id=source_window_id,
            target_window_id=target_window_id,
            confidence=round(score, 6),
            evidence=evidence,
            decision_basis=("safe_bundle_continue",),
            review_required=False,
            algorithm_version=resolver_version,
            source_cluster_ids=(),
            target_cluster_ids=tuple(cluster_ids),
            proposed_target_candidate_ids=(candidate_id,),
        )
        relation_index += 1
        relations.append(relation)
        consumed_cluster_ids.update(cluster_ids)
        decisions.append(
            LineageDecision(
                decision_type="CONTINUE",
                window_id=target_window_id,
                cluster_ids=tuple(cluster_ids),
                candidate_ids=(candidate_id,),
                review_required=False,
                decision_basis=("safe_bundle_continue",),
                hypotheses=(
                    LineageHypothesis(
                        hypothesis_id=f"hyp-{relation.relation_id}",
                        relation_type="CONTINUE",
                        source_candidate_ids=(candidate_id,),
                        target_cluster_ids=tuple(cluster_ids),
                        confidence=round(score, 6),
                        evidence=evidence,
                        decision_basis=("safe_bundle_continue",),
                    ),
                ),
                confidence=round(score, 6),
                relation=relation,
            )
        )

    # Remaining current clusters: weak evidence, abstention, or genuine NEW.
    for cluster in clusters:
        if cluster.cluster_id in consumed_cluster_ids:
            continue
        pending = proposals_by_cluster.get(cluster.cluster_id, ())
        if any(item.decision == "review_required" for item in pending):
            decisions.append(
                review_decision(
                    ("ordinary_abstention_preserved",),
                    (cluster.cluster_id,),
                    tuple(
                        sorted(
                            {
                                str(item.candidate_id)
                                for item in pending
                                if item.candidate_id is not None
                            }
                        )
                    ),
                )
            )
        elif any(item.decision == "same" and item.candidate_id for item in pending):
            decisions.append(
                review_decision(
                    ("weak_lineage_evidence",),
                    (cluster.cluster_id,),
                    tuple(
                        sorted(
                            {
                                str(item.candidate_id)
                                for item in pending
                                if item.candidate_id is not None
                            }
                        )
                    ),
                )
            )
        else:
            decisions.append(
                LineageDecision(
                    decision_type="NEW",
                    window_id=target_window_id,
                    cluster_ids=(cluster.cluster_id,),
                    candidate_ids=(),
                    review_required=False,
                    decision_basis=("no_historical_continuity",),
                )
            )
        consumed_cluster_ids.add(cluster.cluster_id)

    ordered_relations = tuple(sorted(relations, key=lambda item: item.relation_id))
    ordered_decisions = tuple(
        sorted(
            decisions,
            key=lambda item: (
                item.window_id,
                item.cluster_ids,
                item.decision_type,
            ),
        )
    )
    return LineageResolution(
        relations=ordered_relations,
        decisions=ordered_decisions,
        config_version=lineage_config_version(config),
    )


def lineage_inputs_from_events(
    events: Sequence[Mapping[str, Any]],
    *,
    strip_forbidden_metadata: bool = False,
) -> tuple[
    list[CandidateLineageProfile],
    list[CurrentClusterProfile],
    list[OrdinaryIdentityProposal],
    str,
    str,
]:
    """Convert raw production event dictionaries to resolver inputs.

    The resolver only ever receives the allowed production-observable fields.
    When ``strip_forbidden_metadata`` is true the raw event dictionaries are
    also recursively cleared of evaluator/Gold fields so the leakage audit can
    prove the resolver decision signature is independent of them.
    """

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): clean(item)
                for key, item in value.items()
                if str(key) not in FORBIDDEN_LINEAGE_METADATA_KEYS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = [clean(event) for event in events] if strip_forbidden_metadata else list(events)
    candidates: list[CandidateLineageProfile] = []
    clusters: list[CurrentClusterProfile] = []
    proposals: list[OrdinaryIdentityProposal] = []
    source_window_id = ""
    target_window_id = ""
    for event in cleaned:
        source_window_id = str(event.get("source_window_id") or source_window_id)
        target_window_id = str(event.get("target_window_id") or target_window_id)
        for raw in event.get("historical_candidates", ()) or ():
            candidates.append(
                CandidateLineageProfile(
                    candidate_id=str(raw["candidate_id"]),
                    window_id=str(raw.get("window_id") or source_window_id),
                    titles=frozenset(str(item) for item in raw.get("titles", ())),
                    skills=frozenset(str(item).casefold() for item in raw.get("skills", ())),
                    responsibilities=frozenset(
                        str(item).casefold() for item in raw.get("responsibilities", ())
                    ),
                    member_jd_ids=frozenset(
                        str(item) for item in raw.get("member_jd_ids", ())
                    ),
                    company_ids=frozenset(
                        str(item) for item in raw.get("company_ids", ())
                    ),
                    source_evidence_refs=frozenset(
                        str(item) for item in raw.get("source_evidence_refs", ())
                    ),
                    observed_window_ids=tuple(
                        str(item) for item in raw.get("observed_window_ids", ())
                    ),
                    support_count=int(raw.get("support_count", 0)),
                )
            )
        for raw in event.get("current_clusters", ()) or ():
            clusters.append(
                CurrentClusterProfile(
                    cluster_id=str(raw["cluster_id"]),
                    window_id=str(raw.get("window_id") or target_window_id),
                    titles=frozenset(str(item) for item in raw.get("titles", ())),
                    skills=frozenset(str(item).casefold() for item in raw.get("skills", ())),
                    responsibilities=frozenset(
                        str(item).casefold() for item in raw.get("responsibilities", ())
                    ),
                    member_jd_ids=frozenset(
                        str(item) for item in raw.get("member_jd_ids", ())
                    ),
                    company_ids=frozenset(
                        str(item) for item in raw.get("company_ids", ())
                    ),
                    source_evidence_refs=frozenset(
                        str(item) for item in raw.get("source_evidence_refs", ())
                    ),
                    coherent=bool(raw.get("coherent", True)),
                    bundle_safe=bool(raw.get("bundle_safe", True)),
                    support_count=int(raw.get("support_count", 0)),
                )
            )
        for raw in event.get("ordinary_proposals", ()) or ():
            proposals.append(
                OrdinaryIdentityProposal(
                    cluster_id=str(raw["cluster_id"]),
                    candidate_id=(
                        str(raw["candidate_id"]) if raw.get("candidate_id") else None
                    ),
                    decision=str(raw.get("decision", "review_required")),
                    identity_score=(
                        float(raw["identity_score"])
                        if raw.get("identity_score") is not None
                        else None
                    ),
                    decision_basis=tuple(
                        str(item) for item in raw.get("decision_basis", ())
                    ),
                )
            )
    return (
        candidates,
        clusters,
        proposals,
        source_window_id,
        target_window_id,
    )


def lineage_decision_signature(resolution: LineageResolution) -> dict[str, Any]:
    return {
        "relations": [
            {
                "relation_type": item.relation_type,
                "source_candidate_ids": sorted(item.source_candidate_ids),
                "target_candidate_ids": sorted(item.target_candidate_ids),
                "target_cluster_ids": sorted(item.target_cluster_ids),
                "confidence": round(float(item.confidence), 6),
                "decision_basis": sorted(item.decision_basis),
                "evidence": sorted(
                    {
                        (evidence.name, round(float(evidence.value), 6))
                        for evidence in item.evidence
                    }
                ),
            }
            for item in resolution.relations
        ],
        "decisions": [
            {
                "decision_type": item.decision_type,
                "cluster_ids": sorted(item.cluster_ids),
                "candidate_ids": sorted(item.candidate_ids),
                "review_required": item.review_required,
                "decision_basis": sorted(item.decision_basis),
                "confidence": (
                    round(float(item.confidence), 6)
                    if item.confidence is not None
                    else None
                ),
            }
            for item in resolution.decisions
        ],
    }


def _forbidden_metadata_tokens(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    tokens: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key) in FORBIDDEN_LINEAGE_METADATA_KEYS:
                    if isinstance(item, str) and item.strip():
                        tokens.add(item.strip())
                    elif isinstance(item, (list, tuple)):
                        tokens.update(
                            str(entry).strip()
                            for entry in item
                            if isinstance(entry, str) and entry.strip()
                        )
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for event in events:
        for key, value in event.items():
            if str(key) in FORBIDDEN_LINEAGE_METADATA_KEYS:
                if isinstance(value, str) and value.strip():
                    tokens.add(value.strip())
                elif isinstance(value, (list, tuple)):
                    tokens.update(
                        str(item).strip()
                        for item in value
                        if isinstance(item, str) and item.strip()
                    )
            walk(value)
    return tuple(sorted(tokens))


def audit_lineage_leakage(
    events: Sequence[Mapping[str, Any]],
    *,
    window_order: Sequence[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove lineage hypotheses and decision signatures are Gold-independent."""
    full_inputs = lineage_inputs_from_events(events, strip_forbidden_metadata=False)
    stripped_inputs = lineage_inputs_from_events(events, strip_forbidden_metadata=True)
    full = resolve_candidate_lineage(
        source_window_id=full_inputs[3],
        target_window_id=full_inputs[4],
        historical_candidates=full_inputs[0],
        current_clusters=full_inputs[1],
        ordinary_proposals=full_inputs[2],
        window_order=window_order,
        config=config,
    )
    stripped = resolve_candidate_lineage(
        source_window_id=stripped_inputs[3],
        target_window_id=stripped_inputs[4],
        historical_candidates=stripped_inputs[0],
        current_clusters=stripped_inputs[1],
        ordinary_proposals=stripped_inputs[2],
        window_order=window_order,
        config=config,
    )
    full_signature = lineage_decision_signature(full)
    stripped_signature = lineage_decision_signature(stripped)
    serialized_full = _canonical_json(full_signature)
    serialized_stripped = _canonical_json(stripped_signature)
    forbidden_tokens = _forbidden_metadata_tokens(events)
    joined = _canonical_json(
        {
            "relations": [
                {
                    "decision_basis": sorted(item.decision_basis),
                    "evidence": [evidence.name for evidence in item.evidence],
                }
                for item in full.relations
            ],
            "decisions": [
                {
                    "decision_basis": sorted(item.decision_basis),
                    "hypotheses": [
                        {
                            "decision_basis": sorted(hypothesis.decision_basis),
                            "evidence": [
                                evidence.name for evidence in hypothesis.evidence
                            ],
                        }
                        for hypothesis in item.hypotheses
                    ],
                }
                for item in full.decisions
            ],
        }
    )
    leaked = any(token and token in joined for token in forbidden_tokens)
    identical = (
        full_signature == stripped_signature
        and serialized_full == serialized_stripped
        and not leaked
    )
    return {
        "schema_version": "candidate-lineage-leakage-audit.v1",
        "experiment_id": "D-CANDIDATE-LINEAGE",
        "status": "confirmed" if identical else "failed",
        "event_count": len(events),
        "gold_metadata_stripped": True,
        "hypotheses_and_decisions_identical": identical,
        "forbidden_metadata_tokens_found": [token for token in forbidden_tokens if token],
        "forbidden_tokens_in_signature": leaked,
        "original_signature_hash": "sha256:" + sha256(serialized_full.encode("utf-8")).hexdigest(),
        "stripped_signature_hash": "sha256:" + sha256(serialized_stripped.encode("utf-8")).hexdigest(),
        "forbidden_inputs": sorted(FORBIDDEN_LINEAGE_METADATA_KEYS),
        "allowed_inputs": [
            "historical Candidate profile",
            "adjacent-window CandidateWindowObservation",
            "refined current clusters",
            "title / responsibility / skill evidence",
            "membership / company / source evidence",
            "temporal adjacency",
            "existing automatic assignment evidence",
        ],
    }


def lineage_support_inflation(relations: Sequence[CandidateLineageRelation]) -> int:
    """Lineage history must never inflate current-window support counts."""
    return sum(
        1
        for relation in relations
        if relation.support_inflation != 0 or relation.observation_delta != 0
    )


def lineage_duplicate_observation_count(
    relations: Sequence[CandidateLineageRelation],
) -> int:
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for relation in relations:
        keys = set()
        for candidate_id in relation.source_candidate_ids:
            keys.add((candidate_id, relation.source_window_id))
        for candidate_id in relation.target_candidate_ids:
            keys.add((candidate_id, relation.target_window_id))
        for key in keys:
            if key in seen:
                duplicates += 1
            seen.add(key)
    return duplicates


def validate_lineage_integrity(
    relations: Sequence[CandidateLineageRelation],
) -> dict[str, Any]:
    """Validate independent lineage invariants before commit."""
    seen_ids: set[str] = set()
    for relation in relations:
        if relation.relation_id in seen_ids:
            raise ValueError(f"duplicate lineage relation id: {relation.relation_id}")
        seen_ids.add(relation.relation_id)
        if relation.relation_type not in RELATION_TYPES:
            raise ValueError(f"unsupported lineage relation type: {relation.relation_type}")
        if not relation.source_candidate_ids or not relation.target_candidate_ids:
            raise ValueError(f"relation {relation.relation_id} needs source and target candidates")
        if not relation.source_window_id or not relation.target_window_id:
            raise ValueError(f"relation {relation.relation_id} needs source and target windows")
        if not 0 <= float(relation.confidence) <= 1:
            raise ValueError(f"relation {relation.relation_id} confidence out of range")
        if relation.support_inflation != 0 or relation.observation_delta != 0:
            raise ValueError(
                f"relation {relation.relation_id} would inflate lifecycle observations"
            )
    support_inflation = lineage_support_inflation(relations)
    duplicate_observations = lineage_duplicate_observation_count(relations)
    if support_inflation or duplicate_observations:
        raise ValueError(
            "lineage integrity failed: "
            f"support_inflation={support_inflation}, duplicate_observations={duplicate_observations}"
        )
    return {
        "valid": True,
        "relation_count": len(relations),
        "support_inflation": support_inflation,
        "duplicate_observation_count": duplicate_observations,
        "unique_observation_keys": len(
            {
                key
                for relation in relations
                for key in (
                    *((candidate_id, relation.source_window_id) for candidate_id in relation.source_candidate_ids),
                    *((candidate_id, relation.target_window_id) for candidate_id in relation.target_candidate_ids),
                )
            }
        ),
    }


@dataclass
class CandidateLineageTransaction:
    """Buffered lineage batch with explicit commit/rollback semantics."""

    _pending: list[CandidateLineageRelation] = field(default_factory=list)
    _committed: tuple[CandidateLineageRelation, ...] = ()

    @property
    def pending_relations(self) -> tuple[CandidateLineageRelation, ...]:
        return tuple(self._pending)

    @property
    def committed_relations(self) -> tuple[CandidateLineageRelation, ...]:
        return self._committed

    def add(self, relation: CandidateLineageRelation) -> None:
        self._pending.append(relation)

    def commit(self) -> tuple[CandidateLineageRelation, ...]:
        validate_lineage_integrity(self._pending)
        self._committed = (*self._committed, *self._pending)
        self._pending.clear()
        return self._committed

    def rollback(self) -> None:
        self._pending.clear()
