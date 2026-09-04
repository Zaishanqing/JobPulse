"""Application orchestration for candidate identity and lifecycle updates."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import combinations
from types import SimpleNamespace
from uuid import uuid4

from app.application.contracts import DiscoveryConfig
from app.application.discovery_mapping import generated_definition_contract
from app.domain.candidate_identity import (
    CandidateIdentityComponents,
    CandidateIdentityMatch,
    CandidateIdentitySpec,
    PENDING_IDENTITY_REVIEW,
    PendingIdentityResolution,
    dedup_cluster_identity,
    evidence_identity,
    identity_decision,
    select_candidate_identity,
    select_candidate_identity_hypotheses,
    template_cluster_identity,
)
from app.domain.candidate_lineage import (
    CandidateLineageRelation,
    CandidateLineageProfile,
    CurrentClusterProfile,
    LineageResolution,
    OrdinaryIdentityProposal,
    resolve_candidate_lineage,
    validate_lineage_integrity,
)
from app.domain.candidate_lifecycle import (
    CANDIDATE_LIFECYCLE_V2_VERSION,
    DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG,
    LifecycleTransitionResult,
    SUPPRESSED_MISSING_PENDING_IDENTITY_REVIEW,
    TERMINAL_STATUSES,
    LifecycleCoverageState,
    append_lifecycle_observation,
    empty_lifecycle_state_v2,
    record_missing_window,
    trajectory_signals_from_state,
    transition_candidate,
    transition_for_missing_windows,
)
from app.domain.values import FrozenDict, freeze, thaw
from app.ports.providers import CandidateRepository
from app.ports.records import (
    CandidateObservationRecord,
    CandidateLineageReviewRecord,
    CandidateRecord,
    CandidateTransitionRecord,
    ClusterAggregate,
    SnapshotRecord,
)


def _company_from_payload(payload) -> str | None:
    data = payload.get("structured_data") or {}
    for key in ("enterprise_id", "company_id", "company_name"):
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _snapshot_skills(payload) -> tuple[str, ...]:
    data = payload.get("structured_data") or {}
    values: set[str] = set()
    for key in ("required_skills", "bonus_skills"):
        for item in data.get(key, ()):
            if not isinstance(item, dict):
                continue
            value = item.get("normalized_skill_id") or item.get("raw_skill")
            if value is not None and str(value).strip():
                values.add(str(value).strip())
    return tuple(sorted(values))


def _snapshot_responsibilities(payload) -> tuple[str, ...]:
    data = payload.get("structured_data") or {}
    return tuple(
        str(value).strip()
        for value in data.get("responsibilities", ())
        if str(value).strip()
    )


def _snapshot_content_hash(snapshot: SnapshotRecord) -> str | None:
    explicit = str(snapshot.payload.get("content_hash") or "").casefold()
    version = str(snapshot.input_version).casefold()
    for value in (explicit, version):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            return value
    return None


def _new_candidate(
    candidate_id: str,
    window_id: str,
    title: str,
    created_at: datetime,
) -> CandidateRecord:
    return CandidateRecord(
        id=candidate_id,
        status="weak_signal",
        first_seen_window_id=window_id,
        last_seen_window_id=window_id,
        age=1,
        current_cluster_id=None,
        previous_cluster_ids=(),
        canonical_title=title,
        display_title=title,
        definition=FrozenDict(),
        support_count=0,
        company_coverage=0,
        skill_similarity=None,
        responsibility_similarity=None,
        title_similarity=None,
        membership_overlap=None,
        identity_similarity=1.0,
        novelty_score=0.0,
        emergence_score=0.0,
        evidence=FrozenDict(),
        identity_stability=0,
        titles=(title,),
        skills=(),
        responsibilities=(),
        member_jd_ids=(),
        observed_window_ids=(window_id,),
        semantic_centroid=(),
        created_at=created_at,
        updated_at=created_at,
        identity_certificate=FrozenDict(),
        lifecycle_state_v2=empty_lifecycle_state_v2(),
        identity_resolution_state=None,
        identity_resolution=FrozenDict(),
        canonical_candidate_id=None,
    )


@dataclass(frozen=True)
class _ClusterInputs:
    title: str
    member_jd_ids: tuple[str, ...]
    companies: tuple[str, ...]
    responsibilities: tuple[str, ...]
    evidence_titles: frozenset[str]
    evidence_skills: frozenset[str]
    evidence_responsibilities: frozenset[str]
    member_evidence_ids: frozenset[str]
    member_dedup_cluster_ids: frozenset[str]
    member_template_cluster_ids: frozenset[str]
    spec: CandidateIdentitySpec


@dataclass(frozen=True)
class _Bundle:
    candidate_id: str
    primary_cluster_id: str
    primary_cluster_key: str
    contributing_cluster_ids: tuple[str, ...]
    decision: str
    support_count: int
    company_count: int
    inputs: _ClusterInputs
    hypotheses: tuple["_IdentityHypothesis", ...] = ()
    assigned_rank: int | None = None
    assignment_reason: str = "pending"
    bundle_compatibility: FrozenDict = field(default_factory=FrozenDict)


@dataclass(frozen=True)
class _AssignmentResolution:
    decisions: Mapping[str, str]
    bundles: Mapping[str, _Bundle]
    diagnostics: "_AssignmentDiagnostics"


@dataclass(frozen=True)
class _AssignmentDiagnostics:
    coherent_bundle_count: int
    bundled_cluster_count: int
    top1_conflict_group_count: int
    top1_conflict_loser_count: int
    reassigned_to_top2_count: int
    reassigned_to_top3_count: int
    unresolved_review_count: int
    genuine_conflict_count: int

    def to_dict(self) -> Mapping[str, int]:
        return {
            "coherent_bundle_count": self.coherent_bundle_count,
            "bundled_cluster_count": self.bundled_cluster_count,
            "top1_conflict_group_count": self.top1_conflict_group_count,
            "top1_conflict_loser_count": self.top1_conflict_loser_count,
            "reassigned_to_top2_count": self.reassigned_to_top2_count,
            "reassigned_to_top3_count": self.reassigned_to_top3_count,
            "unresolved_review_count": self.unresolved_review_count,
            "genuine_conflict_count": self.genuine_conflict_count,
        }


def _cluster_inputs(
    cluster: ClusterAggregate,
    snapshot_by_id: dict[str, SnapshotRecord],
) -> _ClusterInputs:
    title = (
        cluster.representative_titles[0]
        if cluster.representative_titles
        else cluster.cluster_name
    )
    member_jd_ids = sorted(
        {
            snapshot_by_id[membership.input_snapshot_id].source_jd_id
            for membership in cluster.memberships
            if membership.input_snapshot_id in snapshot_by_id
        }
    )
    companies = sorted(
        {
            company
            for membership in cluster.memberships
            if membership.input_snapshot_id in snapshot_by_id
            for company in [
                _company_from_payload(snapshot_by_id[membership.input_snapshot_id].payload)
            ]
            if company
        }
    )
    responsibilities = tuple(cluster.core_responsibilities) or tuple(
        cluster.assessment.generated_definition.core_responsibilities
    )
    member_snapshots = tuple(
        snapshot_by_id[membership.input_snapshot_id]
        for membership in cluster.memberships
        if membership.input_snapshot_id in snapshot_by_id
    )
    evidence_titles = frozenset(
        str(snapshot.payload.get("title") or "").strip()
        for snapshot in member_snapshots
        if str(snapshot.payload.get("title") or "").strip()
    )
    evidence_skills = frozenset(
        skill
        for snapshot in member_snapshots
        for skill in _snapshot_skills(snapshot.payload)
    )
    evidence_responsibilities = frozenset(
        value
        for snapshot in member_snapshots
        for value in _snapshot_responsibilities(snapshot.payload)
    )
    member_evidence_ids = frozenset(
        evidence_identity(
            str(snapshot.payload.get("source_fact_id") or snapshot.source_jd_id),
            str(snapshot.input_version),
        )
        for snapshot in member_snapshots
    )
    content_hashes = [
        content_hash
        for snapshot in member_snapshots
        if (content_hash := _snapshot_content_hash(snapshot)) is not None
    ]
    duplicate_hashes = {
        value for value, count in Counter(content_hashes).items() if count > 1
    }
    member_dedup_cluster_ids = frozenset(
        dedup_cluster_identity(content_hash) for content_hash in duplicate_hashes
    )
    member_template_cluster_ids = frozenset(
        template_cluster_identity(
            _company_from_payload(snapshot.payload) or "",
            str(snapshot.payload.get("title") or ""),
            _snapshot_responsibilities(snapshot.payload),
        )
        for snapshot in member_snapshots
    )
    spec = CandidateIdentitySpec(
        titles=frozenset({title}),
        skills=frozenset(str(item).casefold() for item in cluster.core_skills),
        responsibilities=frozenset(
            str(item).casefold() for item in responsibilities
        ),
        member_jd_ids=frozenset(member_jd_ids),
        semantic_centroid=tuple(cluster.semantic_centroid),
        evidence_titles=evidence_titles,
        evidence_skills=evidence_skills,
        evidence_responsibilities=evidence_responsibilities,
        member_evidence_ids=member_evidence_ids,
        member_dedup_cluster_ids=member_dedup_cluster_ids,
        member_template_cluster_ids=member_template_cluster_ids,
    )
    return _ClusterInputs(
        title=title,
        member_jd_ids=member_jd_ids,
        companies=companies,
        responsibilities=responsibilities,
        evidence_titles=evidence_titles,
        evidence_skills=evidence_skills,
        evidence_responsibilities=evidence_responsibilities,
        member_evidence_ids=member_evidence_ids,
        member_dedup_cluster_ids=member_dedup_cluster_ids,
        member_template_cluster_ids=member_template_cluster_ids,
        spec=spec,
    )


def _union_cluster_inputs(inputs: tuple[_ClusterInputs, ...]) -> _ClusterInputs:
    first = inputs[0]
    responsibilities: list[str] = []
    for value in inputs:
        for item in value.responsibilities:
            if item not in responsibilities:
                responsibilities.append(item)
    return _ClusterInputs(
        title=first.title,
        member_jd_ids=tuple(
            sorted({item for value in inputs for item in value.member_jd_ids})
        ),
        companies=tuple(
            sorted({item for value in inputs for item in value.companies})
        ),
        responsibilities=tuple(responsibilities),
        evidence_titles=frozenset(
            item for value in inputs for item in value.evidence_titles
        ),
        evidence_skills=frozenset(
            item for value in inputs for item in value.evidence_skills
        ),
        evidence_responsibilities=frozenset(
            item for value in inputs for item in value.evidence_responsibilities
        ),
        member_evidence_ids=frozenset(
            item for value in inputs for item in value.member_evidence_ids
        ),
        member_dedup_cluster_ids=frozenset(
            item for value in inputs for item in value.member_dedup_cluster_ids
        ),
        member_template_cluster_ids=frozenset(
            item for value in inputs for item in value.member_template_cluster_ids
        ),
        spec=first.spec,
    )


class SyncCandidateLifecycle:
    """Match current clusters to stable candidate identities in one transaction."""

    def __init__(self, candidates: CandidateRepository) -> None:
        self.candidates = candidates
        self.last_diagnostics: dict[str, int] | None = None

    def execute(
        self,
        *,
        run_id: str,
        window_ids: tuple[str, ...],
        clusters: list[ClusterAggregate],
        snapshot_records: list[SnapshotRecord],
        config: DiscoveryConfig,
        historical_backfill: bool = False,
    ) -> None:
        merged_config = {
            **DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
            **thaw(config.values),
        }
        if (
            str(merged_config.get("candidate_lifecycle_version"))
            == CANDIDATE_LIFECYCLE_V2_VERSION
        ):
            merged_config = {**DEFAULT_CANDIDATE_LIFECYCLE_V2_CONFIG, **merged_config}
        historical_registry = {
            item.id: item
            for item in self.candidates.active_candidates()
            if not historical_backfill or item.last_seen_window_id in window_ids
        }
        snapshot_by_id = {item.id: item for item in snapshot_records}
        window_id = window_ids[-1] if window_ids else "unavailable"
        now = datetime.now(timezone.utc)
        coverage_by_window = self._coverage_by_window(
            window_ids,
            snapshot_records,
            merged_config,
        )
        coverage_state = (
            coverage_by_window.get(window_id)
            or LifecycleCoverageState(
                window_id,
                False,
                reasons=("coverage_unavailable",),
            )
        )
        accepted_clusters: list[ClusterAggregate] = []
        for cluster in clusters:
            admission = (cluster.assessment.evidence_package or {}).get(
                "admission_certificate"
            )
            if admission and admission.get("decision") == "REJECT_OFF_TARGET":
                continue
            accepted_clusters.append(cluster)

        clusters_by_id = {cluster.id: cluster for cluster in accepted_clusters}
        inputs_by_cluster = {
            cluster.id: _cluster_inputs(cluster, snapshot_by_id)
            for cluster in accepted_clusters
        }
        proposals: list[_IdentityProposal] = []
        for cluster in sorted(
            accepted_clusters,
            key=lambda item: (item.cluster_key, item.id),
        ):
            _, _, transition = self._observe_cluster(
                historical_registry,
                cluster,
                inputs_by_cluster[cluster.id],
                run_id,
                window_id,
                window_ids,
                now,
                merged_config,
                coverage_state=coverage_state,
                protected_candidate_ids=set(),
            )
            proposals.append(_IdentityProposal.from_cluster(cluster, transition))

        resolution = self._resolve_assignments(
            proposals,
            inputs_by_cluster,
            merged_config,
            window_id,
            window_ids,
        )
        self.last_diagnostics = resolution.diagnostics.to_dict()

        lineage_persist = getattr(self.candidates, "save_lineage", None)
        lineage_relations: list[CandidateLineageRelation] = []
        lineage_reviews: list[CandidateLineageReviewRecord] = []
        if lineage_persist is not None:
            source_window_id = (
                window_ids[-2] if len(window_ids) >= 2 else window_id
            )
            lineage_resolution = resolve_candidate_lineage(
                source_window_id=source_window_id,
                target_window_id=window_id,
                historical_candidates=_historical_lineage_profiles(
                    historical_registry
                ),
                current_clusters=_current_lineage_clusters(
                    accepted_clusters,
                    inputs_by_cluster,
                    window_id,
                ),
                ordinary_proposals=_ordinary_lineage_proposals(
                    proposals,
                    historical_registry,
                ),
                window_order=window_ids,
                config=merged_config,
            )
            validate_lineage_integrity(lineage_resolution.relations)
            bundles = _apply_lineage_bundles(
                lineage_resolution,
                resolution.bundles,
                proposals,
                inputs_by_cluster,
            )
            lineage_relations = list(lineage_resolution.relations)
            lineage_reviews = _lineage_review_records(
                lineage_resolution,
                source_window_id=source_window_id,
                target_window_id=window_id,
            )
        else:
            bundles = dict(resolution.bundles)

        working_registry = dict(historical_registry)
        observed_candidate_ids: set[str] = set()
        protected_candidate_ids: set[str] = set()
        window_observation_keys: set[tuple[str, str]] = set()
        window_bundles = _enforce_unique_window_candidate_claims(
            tuple(
                {item.primary_cluster_id: item for item in bundles.values()}.values()
            ),
            window_id,
        )
        for bundle in window_bundles:
            cluster = clusters_by_id[bundle.primary_cluster_id]
            candidate_id, updated, transition = self._observe_cluster(
                historical_registry,
                cluster,
                bundle.inputs,
                run_id,
                window_id,
                window_ids,
                now,
                merged_config,
                coverage_state=coverage_state,
                protected_candidate_ids=protected_candidate_ids,
                bundle=bundle,
            )
            window_key = (candidate_id, window_id)
            if window_key in window_observation_keys:
                raise ValueError(
                    f"candidate {candidate_id} already observed in window {window_id}"
                )
            window_observation_keys.add(window_key)
            working_registry[candidate_id] = updated
            observed_candidate_ids.add(candidate_id)
            self.candidates.save(updated)
            self.candidates.add_observation(
                CandidateObservationRecord(
                    id=str(uuid4()),
                    candidate_id=candidate_id,
                    run_id=run_id,
                    cluster_id=cluster.id,
                    window_id=window_id,
                    title=updated.display_title,
                    status=updated.status,
                    emergence_score=updated.emergence_score,
                    support_count=updated.support_count,
                    company_count=updated.company_coverage,
                    identity_similarity=updated.identity_similarity,
                    skill_similarity=updated.skill_similarity,
                    responsibility_similarity=updated.responsibility_similarity,
                    title_similarity=updated.title_similarity,
                    membership_overlap=updated.membership_overlap,
                    semantic_similarity=transition.semantic_similarity,
                    evidence=updated.evidence,
                    match_evidence=transition.match_evidence,
                )
            )
            if transition.from_status != transition.to_status:
                self.candidates.add_transition(
                    CandidateTransitionRecord(
                        id=str(uuid4()),
                        candidate_id=candidate_id,
                        from_status=transition.from_status,
                        to_status=transition.to_status,
                        reason=transition.reason,
                        run_id=run_id,
                        window_id=window_id,
                        timestamp=now,
                        transition_version=str(
                            merged_config.get("candidate_lifecycle_version", "candidate-lifecycle-v1")
                        ),
                        details=transition.details,
                    )
                )

        if lineage_persist is not None:
            lineage_persist(run_id, lineage_relations, lineage_reviews)

        for candidate_id, candidate in sorted(working_registry.items()):
            if candidate_id in observed_candidate_ids:
                continue
            if candidate_id in protected_candidate_ids:
                next_state = record_missing_window(
                    candidate.lifecycle_state_v2,
                    coverage_state=coverage_by_window.get(window_id),
                    config=merged_config,
                    suppression_reasons=(
                        SUPPRESSED_MISSING_PENDING_IDENTITY_REVIEW,
                    ),
                )
                working_registry[candidate_id] = replace(
                    candidate,
                    lifecycle_state_v2=next_state,
                    updated_at=now,
                )
                self.candidates.save(working_registry[candidate_id])
                continue
            if candidate.status in TERMINAL_STATUSES or candidate.status == "official_position":
                continue
            coverage_state = coverage_by_window.get(window_id)
            if str(merged_config.get("candidate_lifecycle_version")) == CANDIDATE_LIFECYCLE_V2_VERSION:
                next_state = record_missing_window(
                    candidate.lifecycle_state_v2,
                    coverage_state=coverage_state,
                    config=merged_config,
                )
                missed = int(next_state.get("missed_eligible_windows", 0))
                result = transition_for_missing_windows(
                    candidate.status,
                    missed,
                    merged_config,
                    coverage_state=coverage_state,
                )
                updated = replace(
                    candidate,
                    status=result.to_status,
                    lifecycle_state_v2=next_state,
                    updated_at=now,
                )
            else:
                missed = self._missed_window_count(
                    window_ids, candidate.last_seen_window_id
                )
                if missed <= 0:
                    continue
                result = transition_for_missing_windows(
                    candidate.status,
                    missed,
                    merged_config,
                )
                if not result.changed:
                    continue
                updated = replace(candidate, status=result.to_status, updated_at=now)
            working_registry[candidate_id] = updated
            self.candidates.save(updated)
            if result.changed:
                self.candidates.add_transition(
                    CandidateTransitionRecord(
                        id=str(uuid4()),
                        candidate_id=candidate_id,
                        from_status=candidate.status,
                        to_status=result.to_status,
                        reason=result.reason,
                        run_id=run_id,
                        window_id=window_id,
                        timestamp=now,
                        transition_version=str(
                            merged_config.get(
                                "candidate_lifecycle_version",
                                "candidate-lifecycle-v1",
                            )
                        ),
                        details=result.details,
                    )
                )

    def _coverage_by_window(
        self,
        window_ids: tuple[str, ...],
        snapshot_records: list[SnapshotRecord],
        config: dict[str, object],
    ) -> dict[str, LifecycleCoverageState]:
        historical = {
            item.window_id: item.coverage
            for item in self.candidates.lifecycle_windows()
            if item.window_id in window_ids
        }
        current: dict[str, dict[str, int]] = {}
        for snapshot in snapshot_records:
            payload = snapshot.payload or {}
            source = str(payload.get("source_name") or "").strip()
            company = _company_from_payload(payload)
            item = current.setdefault(
                snapshot.window_id,
                {"source_count": 0, "company_count": 0, "jd_ids": set()},
            )
            if source:
                item.setdefault("sources", set()).add(source)
            if company:
                item.setdefault("companies", set()).add(company)
            item["jd_ids"].add(snapshot.source_jd_id)

        result: dict[str, LifecycleCoverageState] = {}
        for window_id in window_ids:
            if window_id in current:
                value = current[window_id]
                result[window_id] = self._coverage_state(
                    window_id,
                    source_count=len(value.get("sources", ())),
                    company_count=len(value.get("companies", ())),
                    eligible_jd_count=len(value["jd_ids"]),
                    config=config,
                )
            elif window_id in historical:
                coverage = historical[window_id]
                result[window_id] = self._coverage_state(
                    window_id,
                    source_count=coverage.get("source_count"),
                    company_count=coverage.get("company_count"),
                    eligible_jd_count=coverage.get("jd_count"),
                    config=config,
                )
            else:
                result[window_id] = LifecycleCoverageState(
                    window_id,
                    False,
                    reasons=("window_not_declared",),
                )
        return result

    @staticmethod
    def _coverage_state(
        window_id: str,
        *,
        source_count: int | None,
        company_count: int | None,
        eligible_jd_count: int | None,
        config: dict[str, object],
    ) -> LifecycleCoverageState:
        reasons: list[str] = []
        min_source = int(config.get("coverage_min_source_count", 1))
        min_company = int(config.get("coverage_min_company_count", 1))
        min_jd = int(config.get("coverage_min_jd_count", 1))
        if source_count is None or source_count < min_source:
            reasons.append("source_coverage_insufficient")
        if company_count is None or company_count < min_company:
            reasons.append("company_coverage_insufficient")
        if eligible_jd_count is None or eligible_jd_count < min_jd:
            reasons.append("eligible_jd_volume_insufficient")
        return LifecycleCoverageState(
            window_id=window_id,
            valid=not reasons,
            source_count=int(source_count or 0),
            company_count=int(company_count or 0),
            eligible_jd_count=int(eligible_jd_count or 0),
            reasons=tuple(reasons),
        )

    def _observe_cluster(
        self,
        registry: dict[str, CandidateRecord],
        cluster: ClusterAggregate,
        inputs: _ClusterInputs,
        run_id: str,
        window_id: str,
        window_ids: tuple[str, ...],
        now: datetime,
        config: dict[str, object],
        coverage_state: LifecycleCoverageState,
        protected_candidate_ids: set[str],
        force_review_required: bool = False,
        bundle: _Bundle | None = None,
    ) -> tuple[str, CandidateRecord, "_ClusterObservation"]:
        title = inputs.title
        member_jd_ids = inputs.member_jd_ids
        companies = inputs.companies
        responsibilities = inputs.responsibilities
        evidence_titles = inputs.evidence_titles
        evidence_skills = inputs.evidence_skills
        evidence_responsibilities = inputs.evidence_responsibilities
        member_evidence_ids = inputs.member_evidence_ids
        member_dedup_cluster_ids = inputs.member_dedup_cluster_ids
        member_template_cluster_ids = inputs.member_template_cluster_ids
        candidate_specs = tuple(_record_to_spec(item) for item in registry.values())
        matches = select_candidate_identity_hypotheses(
            inputs.spec,
            candidate_specs,
            config,
            current_window_id=window_id,
            window_order=window_ids,
            top_k=3,
        )
        if (
            bundle is not None
            and bundle.assigned_rank is not None
            and 1 <= bundle.assigned_rank <= len(matches)
        ):
            match = matches[bundle.assigned_rank - 1]
        else:
            match = matches[0]
        hypotheses = tuple(_IdentityHypothesis.from_match(item) for item in matches)
        if bundle is not None:
            decision = bundle.decision
        elif force_review_required:
            decision = "review_required"
        else:
            decision = identity_decision(match)
        if decision == "review_required" and match.candidate_id:
            protected_candidate_ids.add(match.candidate_id)
        if bundle is not None:
            candidate_id = bundle.candidate_id
        elif decision == "same" and match.candidate_id:
            candidate_id = match.candidate_id
        else:
            candidate_id = self._candidate_id(window_id, cluster.cluster_key)

        existing = registry.get(candidate_id)
        is_new = existing is None
        if is_new:
            existing = _new_candidate(candidate_id, window_id, title, now)
        is_pending = decision == "review_required" or (
            existing.identity_resolution_state == PENDING_IDENTITY_REVIEW
        )
        from_status = (
            existing.status
            if is_pending
            else (None if is_new else existing.status)
        )

        observed_windows = tuple(
            dict.fromkeys((*existing.observed_window_ids, window_id))
        )
        definition = generated_definition_contract(cluster.assessment.generated_definition)
        evidence = cluster.assessment.evidence_package
        support_count = (
            bundle.support_count if bundle is not None else cluster.sample_count
        )
        company_count = (
            bundle.company_count if bundle is not None else len(companies)
        )
        identity_similarity = match.identity_similarity
        certificate = self._continuity_certificate(
            match=match,
            decision=decision,
            candidate_id=candidate_id,
            run_id=run_id,
            window_id=window_id,
            evidence_refs=tuple(sorted(member_evidence_ids)),
            abstention_policy_version=str(
                config.get(
                    "identity_abstention_policy_version",
                    "candidate-identity-abstention.v1",
                )
            ),
        )
        identity_stability = (
            existing.identity_stability + 1
            if identity_similarity >= float(config.get("identity_stability_threshold", 0.60))
            else 0
        )
        admission = (cluster.assessment.evidence_package or {}).get(
            "admission_certificate"
        )
        if is_pending:
            trajectory_state = existing.lifecycle_state_v2
            result = LifecycleTransitionResult(
                existing.status,
                "pending identity review blocks automatic lifecycle promotion",
                False,
                triggered_rules=("pending_identity_review",),
                details=FrozenDict(
                    {
                        "previous_status": existing.status,
                        "new_status": existing.status,
                        "triggered_rules": ("pending_identity_review",),
                    }
                ),
            )
        else:
            trajectory_state = append_lifecycle_observation(
                existing.lifecycle_state_v2,
                support_count=support_count,
                company_count=company_count,
                emergence_score=cluster.assessment.result.germination_score,
                eligible=coverage_state.valid,
                coverage_state=coverage_state,
                config=config,
            )
            result = transition_candidate(
                existing.status,
                supported_window_count=len(observed_windows),
                support_count=support_count,
                company_count=company_count,
                emergence_score=cluster.assessment.result.germination_score,
                identity_similarity=identity_similarity,
                identity_stability=identity_stability,
                config=config,
                trajectory=trajectory_signals_from_state(
                    trajectory_state, coverage_state
                ),
            )
            if (
                admission
                and result.to_status in ("emerging_candidate", "stable_emerging_role")
                and admission.get("decision") != "ADMIT"
            ):
                result = LifecycleTransitionResult(
                    existing.status,
                    "admission policy blocks automatic promotion to "
                    f"{result.to_status}: {admission.get('decision_reason')}",
                    False,
                    triggered_rules=result.triggered_rules,
                    details=result.details,
                )
        bundle_cluster_ids = (
            bundle.contributing_cluster_ids
            if bundle is not None
            else (cluster.id,)
        )
        previous_cluster_ids = tuple(
            dict.fromkeys(
                item
                for item in (
                    existing.current_cluster_id,
                    *existing.previous_cluster_ids,
                    *bundle_cluster_ids,
                )
                if item and item != cluster.id
            )
        )
        updated = CandidateRecord(
            id=candidate_id,
            status=result.to_status,
            first_seen_window_id=existing.first_seen_window_id,
            last_seen_window_id=window_id,
            age=len(observed_windows),
            current_cluster_id=cluster.id,
            previous_cluster_ids=previous_cluster_ids,
            canonical_title=existing.canonical_title or title,
            display_title=title,
            definition=definition,
            support_count=support_count,
            company_coverage=company_count,
            skill_similarity=match.components.skill_similarity,
            responsibility_similarity=match.components.responsibility_similarity,
            title_similarity=match.components.title_similarity,
            membership_overlap=match.components.membership_overlap,
            identity_similarity=identity_similarity,
            novelty_score=cluster.distance_from_existing_positions,
            emergence_score=cluster.assessment.result.germination_score,
            evidence=evidence,
            identity_stability=identity_stability,
            titles=sorted(set(existing.titles) | {title}),
            skills=sorted(
                set(existing.skills) | {str(item).casefold() for item in cluster.core_skills}
            ),
            responsibilities=sorted(
                set(existing.responsibilities)
                | {str(item).casefold() for item in responsibilities}
            ),
            member_jd_ids=sorted(set(existing.member_jd_ids) | set(member_jd_ids)),
            observed_window_ids=observed_windows,
            semantic_centroid=tuple(cluster.semantic_centroid)
            or existing.semantic_centroid,
            created_at=existing.created_at,
            updated_at=now,
            evidence_titles=sorted(
                set(existing.evidence_titles) | set(evidence_titles)
            ),
            evidence_skills=sorted(
                set(existing.evidence_skills) | set(evidence_skills)
            ),
            evidence_responsibilities=sorted(
                set(existing.evidence_responsibilities)
                | set(evidence_responsibilities)
            ),
            member_evidence_ids=sorted(
                set(existing.member_evidence_ids) | set(member_evidence_ids)
            ),
            member_dedup_cluster_ids=sorted(
                set(existing.member_dedup_cluster_ids)
                | set(member_dedup_cluster_ids)
            ),
            member_template_cluster_ids=sorted(
                set(existing.member_template_cluster_ids)
                | set(member_template_cluster_ids)
            ),
            identity_certificate=certificate,
            lifecycle_state_v2=trajectory_state,
            identity_resolution_state=(
                PENDING_IDENTITY_REVIEW
                if is_pending
                else existing.identity_resolution_state
            ),
            identity_resolution=(
                freeze(
                    PendingIdentityResolution(
                        provisional_candidate_id=candidate_id,
                        closest_candidate_id=match.candidate_id,
                        identity_score=match.identity_similarity,
                        decision_margin=match.margin,
                        decision_basis=match.decision_basis,
                        continuity_certificate=certificate,
                        window_id=window_id,
                        cluster_id=cluster.id,
                        created_at=now,
                        run_id=run_id,
                        algorithm_version=match.decision_version,
                    ).to_dict()
                )
                if is_pending
                else existing.identity_resolution
            ),
            canonical_candidate_id=existing.canonical_candidate_id,
        )
        observation = _ClusterObservation(
            candidate_id=candidate_id,
            decision=decision,
            identity_similarity=match.identity_similarity,
            margin=match.margin,
            closest_candidate_id=match.candidate_id,
            review_required=decision == "review_required",
            decision_basis=match.decision_basis,
            components=match.components,
            from_status=from_status,
            to_status=result.to_status,
            reason=result.reason,
            match_evidence=self._match_evidence(match, decision, certificate),
            semantic_similarity=match.components.semantic_similarity,
            details=result.details,
            hypotheses=hypotheses,
        )
        if admission:
            observation = replace(
                observation,
                match_evidence=FrozenDict(
                    {
                        **observation.match_evidence,
                        "admission_certificate": FrozenDict(admission),
                    }
                ),
            )
        if bundle is not None:
            observation = replace(
                observation,
                match_evidence=FrozenDict(
                    {
                        **observation.match_evidence,
                        "contributing_cluster_ids": bundle.contributing_cluster_ids,
                        "bundle_compatibility": bundle.bundle_compatibility,
                        "historical_hypotheses": tuple(
                            hypothesis.to_dict()
                            for hypothesis in bundle.hypotheses
                        ),
                        "final_assignment_reason": bundle.assignment_reason,
                        "assigned_rank": bundle.assigned_rank,
                    }
                ),
            )
        return candidate_id, updated, observation

    @staticmethod
    def _resolve_assignments(
        proposals: list["_IdentityProposal"],
        inputs_by_cluster: dict[str, _ClusterInputs],
        config: dict[str, object],
        window_id: str,
        window_ids: tuple[str, ...],
    ) -> _AssignmentResolution:
        sorted_proposals = sorted(
            proposals,
            key=lambda item: (item.cluster_key, item.cluster_id),
        )
        claims_by_candidate: dict[str, list[_IdentityProposal]] = {}
        for proposal in sorted_proposals:
            if proposal.decision == "same" and proposal.candidate_id:
                claims_by_candidate.setdefault(proposal.candidate_id, []).append(
                    proposal
                )

        initial_bundles: list[_Bundle] = []
        handled_cluster_ids: set[str] = set()
        for candidate_id, claims in claims_by_candidate.items():
            claims.sort(key=_claim_sort_key)
            if _can_consolidate(
                claims,
                inputs_by_cluster,
                config,
                window_id,
                window_ids,
            ):
                initial_bundles.append(
                    _bundle_for_claims(candidate_id, claims, inputs_by_cluster)
                )
                handled_cluster_ids.update(
                    claim.cluster_id for claim in claims
                )
            else:
                for claim in claims:
                    initial_bundles.append(
                        _bundle_for_claims(
                            candidate_id,
                            [claim],
                            inputs_by_cluster,
                        )
                    )
                    handled_cluster_ids.add(claim.cluster_id)

        for proposal in sorted_proposals:
            if proposal.cluster_id in handled_cluster_ids:
                continue
            initial_bundles.append(
                _bundle_for_claims(
                    proposal.candidate_id,
                    [proposal],
                    inputs_by_cluster,
                )
            )

        assignable = [
            bundle
            for bundle in initial_bundles
            if bundle.hypotheses
            and bundle.hypotheses[0].candidate_id is not None
            and bundle.hypotheses[0].decision == "same"
        ]
        assignable_ids = {id(bundle) for bundle in assignable}
        non_assignable = [
            bundle
            for bundle in initial_bundles
            if id(bundle) not in assignable_ids
        ]
        assignable.sort(key=_bundle_priority)

        assigned_slots: set[str] = set()
        final_bundles: dict[str, _Bundle] = {}
        final_assignable: list[_Bundle] = []
        unresolved_review_count = 0
        genuine_conflict_count = 0
        for bundle in assignable:
            assigned = False
            for rank, hypothesis in enumerate(bundle.hypotheses, start=1):
                if hypothesis.candidate_id is None or hypothesis.decision != "same":
                    continue
                if hypothesis.candidate_id in assigned_slots:
                    continue
                assigned_slots.add(hypothesis.candidate_id)
                final_bundle = replace(
                    bundle,
                    candidate_id=hypothesis.candidate_id,
                    decision="same",
                    assigned_rank=rank,
                    assignment_reason=(
                        "top1_assigned"
                        if rank == 1
                        else f"reassigned_to_top{rank}"
                    ),
                )
                _register_bundle(final_bundles, final_bundle)
                final_assignable.append(final_bundle)
                assigned = True
                break
            if not assigned:
                top1_occupied = bundle.hypotheses[0].candidate_id in assigned_slots
                final_bundle = replace(
                    bundle,
                    candidate_id=SyncCandidateLifecycle._candidate_id(
                        window_id,
                        bundle.primary_cluster_key,
                    ),
                    decision="review_required",
                    assigned_rank=None,
                    assignment_reason=(
                        "top1_occupied_no_trusted_alternative"
                        if top1_occupied
                        else "no_automatic_same_hypothesis"
                    ),
                )
                _register_bundle(final_bundles, final_bundle)
                final_assignable.append(final_bundle)
                unresolved_review_count += 1
                if top1_occupied:
                    genuine_conflict_count += 1

        for bundle in non_assignable:
            if bundle.decision == "review_required":
                reason = "abstention_review"
                candidate_id = SyncCandidateLifecycle._candidate_id(
                    window_id,
                    bundle.primary_cluster_key,
                )
            elif bundle.decision == "new":
                reason = "new_identity"
                candidate_id = bundle.candidate_id
            else:
                reason = "top1_not_automatic_same"
                candidate_id = bundle.candidate_id
            final_bundle = replace(
                bundle,
                candidate_id=candidate_id,
                assignment_reason=reason,
            )
            _register_bundle(final_bundles, final_bundle)

        top1_bundle_counts: dict[str, int] = {}
        for bundle in assignable:
            top1 = bundle.hypotheses[0].candidate_id
            if top1 is not None:
                top1_bundle_counts[top1] = (
                    top1_bundle_counts.get(top1, 0) + 1
                )
        diagnostics = _AssignmentDiagnostics(
            coherent_bundle_count=sum(
                1
                for bundle in initial_bundles
                if len(bundle.contributing_cluster_ids) > 1
            ),
            bundled_cluster_count=sum(
                len(bundle.contributing_cluster_ids)
                for bundle in initial_bundles
                if len(bundle.contributing_cluster_ids) > 1
            ),
            top1_conflict_group_count=sum(
                1 for count in top1_bundle_counts.values() if count > 1
            ),
            top1_conflict_loser_count=sum(
                1 for bundle in final_assignable if bundle.assigned_rank != 1
            ),
            reassigned_to_top2_count=sum(
                1 for bundle in final_assignable if bundle.assigned_rank == 2
            ),
            reassigned_to_top3_count=sum(
                1 for bundle in final_assignable if bundle.assigned_rank == 3
            ),
            unresolved_review_count=unresolved_review_count,
            genuine_conflict_count=genuine_conflict_count,
        )
        decisions = {
            cluster_id: bundle.decision
            for cluster_id, bundle in final_bundles.items()
        }
        return _AssignmentResolution(
            decisions=decisions,
            bundles=final_bundles,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _missed_window_count(
        ordered_window_ids: tuple[str, ...], last_seen_window_id: str
    ) -> int:
        """Count chronological windows without interpreting opaque IDs as dates."""
        try:
            last_seen_index = ordered_window_ids.index(last_seen_window_id)
        except ValueError:
            return len(ordered_window_ids)
        return len(ordered_window_ids) - last_seen_index - 1


    @staticmethod
    def _continuity_certificate(
        match: CandidateIdentityMatch,
        *,
        decision: str,
        candidate_id: str,
        run_id: str,
        window_id: str,
        evidence_refs: tuple[str, ...],
        abstention_policy_version: str,
    ) -> FrozenDict:
        components = match.components
        return FrozenDict(
            {
                "schema_version": "candidate-continuity-certificate.v1",
                "decision": decision,
                "candidate_id": candidate_id,
                "closest_candidate_id": match.candidate_id,
                "window_id": window_id,
                "run_id": run_id,
                "components": FrozenDict(
                    {
                        "title_similarity": components.title_similarity,
                        "skill_similarity": components.skill_similarity,
                        "responsibility_similarity": (
                            components.responsibility_similarity
                        ),
                        "membership_overlap": components.membership_overlap,
                        "semantic_similarity": components.semantic_similarity,
                        "sample_overlap": components.sample_overlap,
                        "dedup_cluster_overlap": components.dedup_cluster_overlap,
                        "template_cluster_overlap": (
                            components.template_cluster_overlap
                        ),
                    }
                ),
                "threshold": match.threshold,
                "margin": match.margin,
                "semantic_status": match.semantic_status,
                "evidence_refs": evidence_refs,
                "decision_basis": match.decision_basis,
                "decision_reason": match.decision_reason,
                "algorithm_version": match.decision_version,
                "config_version": match.config_version,
                "abstention_policy_version": abstention_policy_version,
                "abstention_reason": match.abstention_reason,
                "verifier": (
                    freeze(match.verifier.to_dict())
                    if match.verifier is not None
                    else None
                ),
            }
        )

    @staticmethod
    def _match_evidence(
        match: CandidateIdentityMatch,
        decision: str,
        certificate: FrozenDict,
    ) -> FrozenDict:
        components = match.components
        return FrozenDict(
            {
                "matched": match.matched,
                "identity_decision": decision,
                "closest_candidate_id": match.candidate_id,
                "identity_similarity": match.identity_similarity,
                "threshold": match.threshold,
                "margin": match.margin,
                "abstention_reason": match.abstention_reason,
                "components": FrozenDict(
                    {
                        "title_similarity": components.title_similarity,
                        "skill_similarity": components.skill_similarity,
                        "responsibility_similarity": components.responsibility_similarity,
                        "membership_overlap": components.membership_overlap,
                        "semantic_similarity": components.semantic_similarity,
                        "sample_overlap": components.sample_overlap,
                        "dedup_cluster_overlap": components.dedup_cluster_overlap,
                        "template_cluster_overlap": components.template_cluster_overlap,
                    }
                ),
                "decision_reason": match.decision_reason,
                "decision_version": match.decision_version,
                "config_version": match.config_version,
                "semantic_status": match.semantic_status,
                "decision_basis": match.decision_basis,
                "verifier": (
                    freeze(match.verifier.to_dict())
                    if match.verifier is not None
                    else None
                ),
                "continuity_certificate": certificate,
            }
        )

    @staticmethod
    def _candidate_id(window_id: str, cluster_key: str) -> str:
        raw_key = str(cluster_key)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw_key).strip("-")
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        readable = safe[:20] if safe else "occupation"
        return f"cand-{window_id}-{readable}-{digest}"[:64]


def _claim_sort_key(proposal: _IdentityProposal) -> tuple[float, float, str, str]:
    return (
        -(proposal.identity_score or 0.0),
        -(proposal.margin if proposal.margin is not None else -1e9),
        proposal.cluster_key,
        proposal.cluster_id,
    )


def _can_consolidate(
    claims: list[_IdentityProposal],
    inputs_by_cluster: dict[str, _ClusterInputs],
    config: dict[str, object],
    window_id: str,
    window_ids: tuple[str, ...],
) -> bool:
    if len(claims) < 2:
        return False
    if any(item.decision != "same" or not item.candidate_id for item in claims):
        return False
    for left, right in combinations(claims, 2):
        left_inputs = inputs_by_cluster[left.cluster_id]
        right_inputs = inputs_by_cluster[right.cluster_id]
        match = select_candidate_identity(
            left_inputs.spec,
            (right_inputs.spec,),
            config,
            current_window_id=window_id,
            window_order=window_ids,
        )
        if not match.matched or match.abstain:
            return False
    return True


def _bundle_priority(bundle: _Bundle) -> tuple[float, float, str, str]:
    top = bundle.hypotheses[0]
    return (
        -(top.identity_score or 0.0),
        -(top.margin if top.margin is not None else -1e9),
        bundle.primary_cluster_key,
        bundle.primary_cluster_id,
    )


def _final_bundle_priority(bundle: _Bundle) -> tuple[int, float, float, str, str]:
    hypothesis = (
        bundle.hypotheses[bundle.assigned_rank - 1]
        if bundle.assigned_rank is not None
        and 1 <= bundle.assigned_rank <= len(bundle.hypotheses)
        else (bundle.hypotheses[0] if bundle.hypotheses else None)
    )
    return (
        0 if bundle.decision == "same" else 1,
        -(hypothesis.identity_score or 0.0) if hypothesis is not None else 0.0,
        -(hypothesis.margin or 0.0) if hypothesis is not None else 0.0,
        bundle.primary_cluster_key,
        bundle.primary_cluster_id,
    )


def _enforce_unique_window_candidate_claims(
    bundles: tuple[_Bundle, ...],
    window_id: str,
) -> tuple[_Bundle, ...]:
    grouped: dict[str, list[_Bundle]] = {}
    for bundle in bundles:
        grouped.setdefault(bundle.candidate_id, []).append(bundle)

    resolved: list[_Bundle] = []
    for candidate_id in sorted(grouped):
        claims = sorted(grouped[candidate_id], key=_final_bundle_priority)
        resolved.append(claims[0])
        for claim in claims[1:]:
            resolved.append(
                replace(
                    claim,
                    candidate_id=SyncCandidateLifecycle._candidate_id(
                        window_id,
                        claim.primary_cluster_key,
                    ),
                    decision="review_required",
                    assigned_rank=None,
                    assignment_reason="final_window_candidate_claim_conflict",
                )
            )
    return tuple(
        sorted(
            resolved,
            key=lambda item: (item.primary_cluster_id, item.candidate_id),
        )
    )


def _register_bundle(
    bundles_by_cluster: dict[str, _Bundle],
    bundle: _Bundle,
) -> None:
    for cluster_id in bundle.contributing_cluster_ids:
        bundles_by_cluster[cluster_id] = bundle


def _bundle_for_claims(
    candidate_id: str,
    claims: list[_IdentityProposal],
    inputs_by_cluster: dict[str, _ClusterInputs],
    *,
    decision: str | None = None,
) -> _Bundle:
    ordered = sorted(claims, key=_claim_sort_key)
    primary = ordered[0]
    selected_decision = decision or primary.decision
    contributing = tuple(
        item.cluster_id
        for item in sorted(claims, key=lambda item: (item.cluster_key, item.cluster_id))
    )
    inputs = _union_cluster_inputs(
        tuple(inputs_by_cluster[item.cluster_id] for item in ordered)
    )
    support_count = (
        len(inputs.member_jd_ids) if len(ordered) > 1 else primary.sample_count
    )
    company_count = len(inputs.companies)
    bundle_compatibility = FrozenDict(
        {
            "schema_version": "candidate-window-bundle.v1",
            "primary_cluster_id": primary.cluster_id,
            "contributing_cluster_ids": contributing,
            "cluster_count": len(ordered),
            "verdict": "coherent" if len(ordered) > 1 else "single",
            "support_count": support_count,
            "company_count": company_count,
            "member_jd_ids": inputs.member_jd_ids,
            "companies": inputs.companies,
            "evidence_refs": tuple(sorted(inputs.member_evidence_ids)),
            "dedup_cluster_ids": tuple(sorted(inputs.member_dedup_cluster_ids)),
            "template_cluster_ids": tuple(sorted(inputs.member_template_cluster_ids)),
        }
    )
    return _Bundle(
        candidate_id=candidate_id,
        primary_cluster_id=primary.cluster_id,
        primary_cluster_key=primary.cluster_key,
        contributing_cluster_ids=contributing,
        decision=selected_decision,
        support_count=support_count,
        company_count=company_count,
        inputs=inputs,
        hypotheses=primary.hypotheses,
        assigned_rank=None,
        assignment_reason="pending",
        bundle_compatibility=bundle_compatibility,
    )


def _historical_lineage_profiles(
    registry: dict[str, CandidateRecord],
) -> list[CandidateLineageProfile]:
    profiles: list[CandidateLineageProfile] = []
    for record in sorted(registry.values(), key=lambda item: item.id):
        evidence = record.evidence or {}
        explainability = evidence.get("cluster_explainability") or {}
        distribution = (
            explainability.get("enterprise_distribution")
            or evidence.get("enterprise_distribution")
            or {}
        )
        company_ids = (
            frozenset(str(key) for key in distribution)
            if isinstance(distribution, Mapping)
            else frozenset()
        )
        profiles.append(
            CandidateLineageProfile(
                candidate_id=record.id,
                window_id=record.last_seen_window_id,
                titles=frozenset(record.titles),
                skills=frozenset(str(item).casefold() for item in record.skills),
                responsibilities=frozenset(
                    str(item).casefold() for item in record.responsibilities
                ),
                member_jd_ids=frozenset(record.member_jd_ids),
                company_ids=company_ids,
                source_evidence_refs=frozenset(record.member_evidence_ids),
                observed_window_ids=tuple(record.observed_window_ids),
                support_count=record.support_count,
            )
        )
    return profiles


def _current_lineage_clusters(
    clusters: list[ClusterAggregate],
    inputs_by_cluster: dict[str, _ClusterInputs],
    window_id: str,
) -> list[CurrentClusterProfile]:
    profiles: list[CurrentClusterProfile] = []
    for cluster in sorted(clusters, key=lambda item: (item.cluster_key, item.id)):
        inputs = inputs_by_cluster[cluster.id]
        merge_basis = dict(cluster.merge_basis or {})
        split_record = merge_basis.get("split_refinement")
        profiles.append(
            CurrentClusterProfile(
                cluster_id=cluster.id,
                window_id=window_id,
                titles=frozenset(cluster.representative_titles),
                skills=frozenset(
                    str(item).casefold() for item in cluster.core_skills
                ),
                responsibilities=frozenset(
                    str(item).casefold() for item in inputs.responsibilities
                ),
                member_jd_ids=frozenset(inputs.member_jd_ids),
                company_ids=frozenset(inputs.companies),
                source_evidence_refs=frozenset(inputs.member_evidence_ids),
                coherent=bool(merge_basis.get("lineage_cluster_coherent", True)),
                bundle_safe=not bool(split_record),
                support_count=cluster.sample_count,
            )
        )
    return profiles


def _ordinary_lineage_proposals(
    proposals: list[_IdentityProposal],
    registry: dict[str, CandidateRecord],
) -> list[OrdinaryIdentityProposal]:
    result: list[OrdinaryIdentityProposal] = []
    seen: set[tuple[str, str, str]] = set()
    resolved_new_ids = {
        candidate_id
        for candidate_id, candidate in registry.items()
        if candidate.identity_resolution_state == "resolved_new"
    }
    for item in sorted(proposals, key=lambda value: (value.cluster_id, value.candidate_id)):
        candidates = [
            hypothesis
            for hypothesis in item.hypotheses
            if hypothesis.decision == "same"
            and hypothesis.candidate_id
            and hypothesis.candidate_id not in resolved_new_ids
        ]
        if not candidates:
            if item.candidate_id in resolved_new_ids:
                continue
            candidates = [
                SimpleNamespace(
                    candidate_id=item.candidate_id,
                    identity_score=item.identity_score,
                    decision=item.decision,
                    decision_basis=item.decision_basis,
                )
            ]
        for hypothesis in candidates:
            key = (
                item.cluster_id,
                str(hypothesis.candidate_id),
                "same",
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(
                OrdinaryIdentityProposal(
                    cluster_id=item.cluster_id,
                    candidate_id=str(hypothesis.candidate_id),
                    decision="same",
                    identity_score=0.99,
                    decision_basis=tuple(hypothesis.decision_basis),
                )
            )
    return result


def _apply_lineage_bundles(
    resolution: LineageResolution,
    bundles: dict[str, _Bundle],
    proposals: list[_IdentityProposal],
    inputs_by_cluster: dict[str, _ClusterInputs],
) -> dict[str, _Bundle]:
    target_by_cluster: dict[str, tuple[str, str]] = {}
    for relation in resolution.relations:
        if relation.review_required:
            continue
        if relation.relation_type not in {"SPLIT", "MERGE"}:
            continue
        for cluster_id, candidate_id in zip(
            relation.target_cluster_ids,
            relation.proposed_target_candidate_ids,
            strict=True,
        ):
            target_by_cluster[cluster_id] = (candidate_id, relation.relation_type)
    if not target_by_cluster:
        return dict(bundles)

    covered_cluster_ids = set(target_by_cluster)
    remaining: dict[str, _Bundle] = {}
    for cluster_id, bundle in bundles.items():
        if set(bundle.contributing_cluster_ids) & covered_cluster_ids:
            continue
        remaining[cluster_id] = bundle

    for cluster_id in sorted(target_by_cluster):
        candidate_id, relation_type = target_by_cluster[cluster_id]
        proposal = next(
            (item for item in proposals if item.cluster_id == cluster_id),
            None,
        )
        if proposal is None:
            continue
        if relation_type == "SPLIT":
            base = _bundle_for_claims(
                proposal.candidate_id,
                [proposal],
                inputs_by_cluster,
                decision="new",
            )
            replacement = replace(
                base,
                candidate_id=candidate_id,
                primary_cluster_id=cluster_id,
                primary_cluster_key=proposal.cluster_key,
                contributing_cluster_ids=(cluster_id,),
                decision="new",
                assignment_reason="lineage_split_child",
            )
        else:
            base = bundles.get(cluster_id) or _bundle_for_claims(
                proposal.candidate_id,
                [proposal],
                inputs_by_cluster,
            )
            replacement = replace(
                base,
                candidate_id=candidate_id,
                primary_cluster_id=cluster_id,
                primary_cluster_key=proposal.cluster_key,
                contributing_cluster_ids=(cluster_id,),
                decision="new",
                assignment_reason="lineage_merge_candidate",
            )
        _register_bundle(remaining, replacement)
    return remaining


def _lineage_review_records(
    resolution: LineageResolution,
    *,
    source_window_id: str,
    target_window_id: str,
) -> list[CandidateLineageReviewRecord]:
    records: list[CandidateLineageReviewRecord] = []
    for decision in resolution.decisions:
        if not decision.review_required:
            continue
        digest = hashlib.sha256(
            json.dumps(
                decision.to_dict(),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        review_id = (
            f"review-{target_window_id}-"
            f"{'-'.join(sorted(decision.cluster_ids))}-{digest}"
        )
        records.append(
            CandidateLineageReviewRecord(
                review_id=review_id,
                source_window_id=source_window_id,
                target_window_id=target_window_id,
                cluster_ids=decision.cluster_ids,
                candidate_ids=decision.candidate_ids,
                decision_basis=decision.decision_basis,
                hypotheses=tuple(
                    hypothesis.to_dict() for hypothesis in decision.hypotheses
                ),
                confidence=decision.confidence,
                algorithm_version=resolution.config_version,
            )
        )
    return records


@dataclass(frozen=True)
class _ClusterObservation:
    from_status: str | None
    to_status: str
    reason: str
    match_evidence: FrozenDict
    semantic_similarity: float | None
    components: CandidateIdentityComponents
    details: FrozenDict = field(default_factory=FrozenDict)
    candidate_id: str | None = None
    decision: str = "same"
    identity_similarity: float | None = None
    margin: float | None = None
    closest_candidate_id: str | None = None
    review_required: bool = False
    decision_basis: tuple[str, ...] = ()
    hypotheses: tuple["_IdentityHypothesis", ...] = ()


@dataclass(frozen=True)
class _IdentityHypothesis:
    candidate_id: str | None
    identity_score: float | None
    margin: float | None
    decision: str
    review_required: bool
    decision_basis: tuple[str, ...]
    components: CandidateIdentityComponents

    @classmethod
    def from_match(
        cls,
        match: CandidateIdentityMatch,
    ) -> "_IdentityHypothesis":
        decision = identity_decision(match)
        return cls(
            candidate_id=match.candidate_id,
            identity_score=match.identity_similarity,
            margin=match.margin,
            decision=decision,
            review_required=decision == "review_required",
            decision_basis=match.decision_basis,
            components=match.components,
        )

    def to_dict(self) -> FrozenDict:
        return FrozenDict(
            {
                "candidate_id": self.candidate_id,
                "identity_score": self.identity_score,
                "margin": self.margin,
                "decision": self.decision,
                "review_required": self.review_required,
                "decision_basis": self.decision_basis,
                "components": FrozenDict(
                    {
                        "title_similarity": self.components.title_similarity,
                        "skill_similarity": self.components.skill_similarity,
                        "responsibility_similarity": (
                            self.components.responsibility_similarity
                        ),
                        "membership_overlap": self.components.membership_overlap,
                        "semantic_similarity": (
                            self.components.semantic_similarity
                        ),
                        "sample_overlap": self.components.sample_overlap,
                        "dedup_cluster_overlap": (
                            self.components.dedup_cluster_overlap
                        ),
                        "template_cluster_overlap": (
                            self.components.template_cluster_overlap
                        ),
                    }
                ),
            }
        )


@dataclass(frozen=True)
class _IdentityProposal:
    cluster_id: str
    cluster_key: str
    candidate_id: str
    decision: str
    identity_score: float | None
    margin: float | None
    review_required: bool
    decision_basis: tuple[str, ...]
    components: CandidateIdentityComponents
    closest_candidate_id: str | None
    sample_count: int = 0
    hypotheses: tuple[_IdentityHypothesis, ...] = ()

    @classmethod
    def from_cluster(
        cls,
        cluster: ClusterAggregate,
        observation: _ClusterObservation,
    ) -> "_IdentityProposal":
        return cls(
            cluster_id=cluster.id,
            cluster_key=cluster.cluster_key,
            candidate_id=observation.candidate_id or "",
            decision=observation.decision,
            identity_score=observation.identity_similarity,
            margin=observation.margin,
            review_required=observation.review_required,
            decision_basis=observation.decision_basis,
            components=observation.components,
            closest_candidate_id=observation.closest_candidate_id,
            sample_count=cluster.sample_count,
            hypotheses=observation.hypotheses,
        )


class NoopCandidateLifecycle:
    def execute(self, **kwargs) -> None:
        return None


def _record_to_spec(record: CandidateRecord) -> CandidateIdentitySpec:
    return CandidateIdentitySpec(
        titles=frozenset(record.titles),
        skills=frozenset(record.skills),
        responsibilities=frozenset(record.responsibilities),
        member_jd_ids=frozenset(record.member_jd_ids),
        semantic_centroid=record.semantic_centroid,
        candidate_id=record.id,
        evidence_titles=frozenset(record.evidence_titles),
        evidence_skills=frozenset(record.evidence_skills),
        evidence_responsibilities=frozenset(record.evidence_responsibilities),
        member_evidence_ids=frozenset(record.member_evidence_ids),
        member_dedup_cluster_ids=frozenset(record.member_dedup_cluster_ids),
        member_template_cluster_ids=frozenset(record.member_template_cluster_ids),
        last_seen_window_id=record.last_seen_window_id,
    )
