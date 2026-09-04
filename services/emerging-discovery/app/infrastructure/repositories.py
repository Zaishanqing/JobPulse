from __future__ import annotations

import hmac
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.application.candidate_lineage_trajectory import (
    reconstruct_lineage_trajectory,
)
from app.application.contracts import (
    DiscoveryAssessmentResult as DiscoveryAssessment,
    DiscoveryClusterResult,
    DiscoveryLineageResult,
    DiscoveryResult,
)
from app.application.discovery_mapping import algorithm_metadata_contract
from app.application.payload_fingerprint import _persisted_payload_fingerprint
from app.domain.candidate_lineage import (
    CandidateLineageRelation as DomainCandidateLineageRelation,
    LineageEvidence,
)
from app.domain.discovery import (
    ClusterFeatureSummary,
    AlgorithmMetadata,
    GeneratedDefinition,
    GeneratedSkill,
)
from app.domain.lineage import ClusterLineageSpec, LineageRelation, LineageScore
from app.domain.values import FrozenDict, freeze, thaw
from app.infrastructure.models import (
    AlgorithmConfigSnapshot,
    Candidate,
    CandidateClusterObservation,
    CandidateLineageRelation as CandidateLineageRelationModel,
    CandidateLineageReview as CandidateLineageReviewModel,
    CandidateStatusTransition,
    Cluster,
    ClusterLineage,
    ClusterMembership,
    DiscoveryMaintenanceAudit,
    DiscoveryRun,
    GerminationAssessment,
    InputSnapshot,
    IdentityResolutionAudit,
)
from app.ports.maintenance import MaintenanceAuditRecord
from app.ports.records import (
    AlgorithmConfigRecord,
    AmbiguousIdentityPairRecord,
    CandidateIdentityEvidenceSnapshotRecord,
    CandidateLifecycleTrajectoryRecord,
    CandidateDiffusionEvidenceRecord,
    CandidateDiffusionObservationRecord,
    CandidateDiffusionRecord,
    CandidateLineageReviewRecord,
    CandidateObservationRecord,
    CandidatePromotionContextRecord,
    CandidateRecord,
    CandidateTransitionRecord,
    ClusterAggregate,
    IdentityResolutionAuditRecord,
    LifecycleWindowRecord,
    LineageRecord,
    RunRecord,
    SnapshotRecord,
)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=timezone.utc)


def _candidate_resolution_state(model: Candidate) -> str | None:
    return (model.identity_profile or {}).get("identity_resolution_state")


def _generated_definition(value: dict[str, Any]) -> GeneratedDefinition:
    def skills(name: str) -> tuple[GeneratedSkill, ...]:
        return tuple(
            GeneratedSkill(
                raw_skill=str(item["raw_skill"]),
                normalized_skill_id=str(item["normalized_skill_id"]),
                confidence=float(item["confidence"]),
            )
            for item in value.get(name, ())
        )

    return GeneratedDefinition(
        position_name=str(value["position_name"]),
        core_responsibilities=tuple(value.get("core_responsibilities", ())),
        required_skills=skills("required_skills"),
        bonus_skills=skills("bonus_skills"),
        industry_scenarios=tuple(value.get("industry_scenarios", ())),
        generation_mode=str(value["generation_mode"]),
        field_evidence=freeze(value.get("field_evidence", {})),
        position_summary=str(value.get("position_summary", "")),
        distinguishing_features=tuple(value.get("distinguishing_features", ())),
        representative_enterprises=freeze(value.get("representative_enterprises", {})),
        growth_trajectory=tuple(freeze(item) for item in value.get("growth_trajectory", ())),
    )


def _generated_definition_payload(value: GeneratedDefinition) -> dict[str, Any]:
    return {
        "position_name": value.position_name,
        "core_responsibilities": list(value.core_responsibilities),
        "required_skills": [asdict(item) for item in value.required_skills],
        "bonus_skills": [asdict(item) for item in value.bonus_skills],
        "industry_scenarios": list(value.industry_scenarios),
        "generation_mode": value.generation_mode,
        "field_evidence": thaw(value.field_evidence),
        "position_summary": value.position_summary,
        "distinguishing_features": list(value.distinguishing_features),
        "representative_enterprises": thaw(value.representative_enterprises),
        "growth_trajectory": [thaw(item) for item in value.growth_trajectory],
    }


def _feature_summary(value: dict[str, Any]) -> ClusterFeatureSummary:
    parameters = value.get("parameters") or {}
    return ClusterFeatureSummary(
        metadata=AlgorithmMetadata(
            algorithm_name=str(value.get("algorithm_name", "unknown")),
            requested_algorithm=str(value.get("requested_algorithm", "unknown")),
            algorithm_version=str(value.get("algorithm_version", "unknown")),
            feature_version=str(value.get("feature_version", "unknown")),
            similarity_threshold=float(parameters.get("similarity_threshold", 0.0)),
            random_seed=int(value.get("random_seed", 0)),
        ),
        centroid=tuple(float(item) for item in value.get("centroid", ())),
    )


def _candidate_record(model: Candidate) -> CandidateRecord:
    profile = model.identity_profile or {}
    return CandidateRecord(
        id=model.id,
        status=model.status,
        first_seen_window_id=model.first_seen_window_id,
        last_seen_window_id=model.last_seen_window_id,
        age=model.age,
        current_cluster_id=model.current_cluster_id,
        previous_cluster_ids=tuple(model.previous_cluster_ids or ()),
        canonical_title=model.canonical_title,
        display_title=model.display_title,
        definition=freeze(model.definition or {}),
        support_count=model.support_count,
        company_coverage=model.company_coverage,
        skill_similarity=model.skill_similarity,
        responsibility_similarity=model.responsibility_similarity,
        title_similarity=model.title_similarity,
        membership_overlap=model.membership_overlap,
        identity_similarity=model.identity_similarity,
        novelty_score=model.novelty_score,
        emergence_score=model.emergence_score,
        evidence=freeze(model.evidence or {}),
        identity_stability=model.identity_stability,
        titles=tuple(profile.get("titles", ())),
        skills=tuple(profile.get("skills", ())),
        responsibilities=tuple(profile.get("responsibilities", ())),
        member_jd_ids=tuple(profile.get("member_jd_ids", ())),
        observed_window_ids=tuple(profile.get("observed_window_ids", ())),
        semantic_centroid=tuple(float(item) for item in profile.get("semantic_centroid", ())),
        created_at=model.created_at,
        updated_at=model.updated_at,
        evidence_titles=tuple(profile.get("evidence_titles", ())),
        evidence_skills=tuple(profile.get("evidence_skills", ())),
        evidence_responsibilities=tuple(profile.get("evidence_responsibilities", ())),
        member_evidence_ids=tuple(profile.get("member_evidence_ids", ())),
        member_dedup_cluster_ids=tuple(
            profile.get("member_dedup_cluster_ids", ())
        ),
        member_template_cluster_ids=tuple(
            profile.get("member_template_cluster_ids", ())
        ),
        identity_certificate=freeze(profile.get("identity_certificate", {})),
        lifecycle_state_v2=freeze(profile.get("lifecycle_state_v2", {})),
        identity_resolution_state=profile.get("identity_resolution_state"),
        identity_resolution=freeze(profile.get("identity_resolution", {})),
        canonical_candidate_id=profile.get("canonical_candidate_id"),
    )


def _candidate_data(model: Candidate) -> FrozenDict:
    return FrozenDict(
        {
            "candidate_id": model.id,
            "status": model.status,
            "first_seen_window_id": model.first_seen_window_id,
            "last_seen_window_id": model.last_seen_window_id,
            "age": model.age,
            "current_cluster_id": model.current_cluster_id,
            "previous_cluster_ids": tuple(model.previous_cluster_ids or ()),
            "canonical_title": model.canonical_title,
            "display_title": model.display_title,
            "definition": freeze(model.definition or {}),
            "support_count": model.support_count,
            "company_coverage": model.company_coverage,
            "skill_similarity": model.skill_similarity,
            "responsibility_similarity": model.responsibility_similarity,
            "title_similarity": model.title_similarity,
            "membership_overlap": model.membership_overlap,
            "identity_similarity": model.identity_similarity,
            "novelty_score": model.novelty_score,
            "emergence_score": model.emergence_score,
            "evidence": freeze(model.evidence or {}),
            "identity_stability": model.identity_stability,
            "identity_profile": freeze(model.identity_profile or {}),
            "identity_certificate": freeze(
                (model.identity_profile or {}).get("identity_certificate", {})
            ),
            "lifecycle_state_v2": freeze(
                (model.identity_profile or {}).get("lifecycle_state_v2", {})
            ),
            "identity_resolution_state": (model.identity_profile or {}).get(
                "identity_resolution_state"
            ),
            "identity_resolution": freeze(
                (model.identity_profile or {}).get("identity_resolution", {})
            ),
            "canonical_candidate_id": (model.identity_profile or {}).get(
                "canonical_candidate_id"
            ),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
    )


def _candidate_observation_record(
    model: CandidateClusterObservation,
) -> CandidateObservationRecord:
    return CandidateObservationRecord(
        id=model.id,
        candidate_id=model.candidate_id,
        run_id=model.run_id,
        cluster_id=model.cluster_id,
        window_id=model.window_id,
        title=model.title,
        status=model.status,
        emergence_score=model.emergence_score,
        support_count=model.support_count,
        company_count=model.company_count,
        identity_similarity=model.identity_similarity,
        skill_similarity=model.skill_similarity,
        responsibility_similarity=model.responsibility_similarity,
        title_similarity=model.title_similarity,
        membership_overlap=model.membership_overlap,
        semantic_similarity=model.semantic_similarity,
        evidence=freeze(model.evidence or {}),
        match_evidence=freeze(model.match_evidence or {}),
    )


def _candidate_lineage_relation(
    model: CandidateLineageRelationModel,
) -> DomainCandidateLineageRelation:
    return DomainCandidateLineageRelation(
        relation_id=model.relation_id,
        relation_type=model.relation_type,
        source_candidate_ids=tuple(str(item) for item in model.source_candidate_ids or ()),
        target_candidate_ids=tuple(str(item) for item in model.target_candidate_ids or ()),
        source_window_id=model.source_window_id,
        target_window_id=model.target_window_id,
        confidence=float(model.confidence),
        evidence=tuple(
            LineageEvidence(
                name=str(item["name"]),
                value=float(item["value"]),
                kind=str(item["kind"]),
                detail=tuple(str(detail) for detail in item.get("detail", ())),
            )
            for item in model.evidence or ()
        ),
        decision_basis=tuple(str(item) for item in model.decision_basis or ()),
        review_required=bool(model.review_required),
        algorithm_version=model.algorithm_version,
        model_version=model.model_version,
        source_cluster_ids=tuple(str(item) for item in model.source_cluster_ids or ()),
        target_cluster_ids=tuple(str(item) for item in model.target_cluster_ids or ()),
        proposed_target_candidate_ids=tuple(
            str(item) for item in model.proposed_target_candidate_ids or ()
        ),
        support_inflation=int(model.support_inflation),
        observation_delta=int(model.observation_delta),
    )


def _candidate_lineage_review_record(
    model: CandidateLineageReviewModel,
) -> CandidateLineageReviewRecord:
    return CandidateLineageReviewRecord(
        review_id=model.review_id,
        source_window_id=model.source_window_id,
        target_window_id=model.target_window_id,
        cluster_ids=tuple(str(item) for item in model.cluster_ids or ()),
        candidate_ids=tuple(str(item) for item in model.candidate_ids or ()),
        decision_basis=tuple(str(item) for item in model.decision_basis or ()),
        hypotheses=tuple(dict(item) for item in model.hypotheses or ()),
        confidence=float(model.confidence) if model.confidence is not None else None,
        algorithm_version=model.algorithm_version,
    )


def _lifecycle_window_record(
    run: DiscoveryRun,
    config: AlgorithmConfigSnapshot,
) -> LifecycleWindowRecord:
    time_window = ((config.config or {}).get("run_context") or {}).get(
        "time_window"
    ) or {}
    declared = time_window.get("windows") or []
    window_id = str(
        time_window.get("current_observation_window_id")
        or (declared[-1].get("window_id") if declared else "unavailable")
    )
    return LifecycleWindowRecord(
        window_id=window_id,
        run_id=run.id,
        request_id=run.request_id,
        algorithm_version=run.algorithm_version,
        formula_version=run.formula_version,
        completed_at=run.completed_at,
        coverage=freeze(
            {
                "source_count": _quality_count(config, "source_count"),
                "company_count": _quality_count(config, "enterprise_count"),
                "jd_count": _quality_count(config, "jd_count"),
            }
        ),
    )


def _quality_count(config: AlgorithmConfigSnapshot, key: str) -> int | None:
    report = (config.config or {}).get("input_quality_report") or {}
    effective = report.get("effective") or {}
    value = effective.get(key)
    if value is None:
        value = report.get(key)
    if value is None or str(value).strip().casefold() == "unavailable":
        return None
    return int(value)


def _observation_data(model: CandidateClusterObservation, cluster_name: str | None) -> FrozenDict:
    return FrozenDict(
        {
            "observation_id": model.id,
            "candidate_id": model.candidate_id,
            "run_id": model.run_id,
            "cluster_id": model.cluster_id,
            "cluster_name": cluster_name,
            "window_id": model.window_id,
            "title": model.title,
            "status": model.status,
            "emergence_score": model.emergence_score,
            "support_count": model.support_count,
            "company_count": model.company_count,
            "identity_similarity": model.identity_similarity,
            "skill_similarity": model.skill_similarity,
            "responsibility_similarity": model.responsibility_similarity,
            "title_similarity": model.title_similarity,
            "membership_overlap": model.membership_overlap,
            "semantic_similarity": model.semantic_similarity,
            "evidence": freeze(model.evidence or {}),
            "match_evidence": freeze(model.match_evidence or {}),
            "created_at": model.created_at,
        }
    )


class ImmutableHistoryRepository:
    """Append-only write policy for discovery history aggregates."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, entity: Any) -> None:
        state = inspect(entity)
        if state.persistent or state.detached or state.deleted:
            raise ValueError("discovery history repository accepts new records only")
        self.session.add(entity)

    def flush(self) -> None:
        self.session.flush()


def _run_record(model: DiscoveryRun) -> RunRecord:
    return RunRecord(
        id=model.id,
        request_id=model.request_id,
        status=model.status,
        algorithm_version=model.algorithm_version,
        formula_version=model.formula_version,
        time_window_start=model.time_window_start,
        time_window_end=model.time_window_end,
        completed_at=model.completed_at,
    )


class SqlAlchemyRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_request_id(self, request_id: str) -> RunRecord | None:
        model = (
            self.session.query(DiscoveryRun)
            .filter_by(request_id=request_id)
            .order_by(DiscoveryRun.created_at.desc())
            .first()
        )
        return _run_record(model) if model else None

    def by_id(self, run_id: str) -> RunRecord | None:
        model = self.session.get(DiscoveryRun, run_id)
        return _run_record(model) if model else None

    def latest_succeeded(self) -> RunRecord | None:
        model = (
            self.session.query(DiscoveryRun)
            .filter_by(status="succeeded")
            .order_by(
                DiscoveryRun.time_window_end.desc(),
                DiscoveryRun.created_at.desc(),
            )
            .first()
        )
        return _run_record(model) if model else None

    def fingerprint_by_run_id(self, run_id: str) -> str | None:
        config_model = (
            self.session.query(AlgorithmConfigSnapshot)
            .filter_by(run_id=run_id)
            .one_or_none()
        )
        if config_model is None:
            return None
        snapshots = (
            self.session.query(InputSnapshot)
            .filter_by(run_id=run_id)
            .order_by(InputSnapshot.source_jd_id)
            .all()
        )
        return _persisted_payload_fingerprint(
            config_model.config or {},
            [item.payload or {} for item in snapshots],
        )

    def add(self, run: RunRecord, algorithm_config: AlgorithmConfigRecord) -> None:
        if isinstance(algorithm_config, dict):
            frozen = freeze(algorithm_config["config"])
            if not isinstance(frozen, FrozenDict):
                raise TypeError("algorithm config must be a JSON object")
            algorithm_config = AlgorithmConfigRecord(
                id=str(algorithm_config["id"]),
                config=frozen,
            )
        self.session.add(
            DiscoveryRun(
                id=run.id,
                request_id=run.request_id,
                status=run.status,
                algorithm_version=run.algorithm_version,
                formula_version=run.formula_version,
                time_window_start=run.time_window_start,
                time_window_end=run.time_window_end,
                completed_at=run.completed_at,
            )
        )
        self.session.flush()
        self.session.add(
            AlgorithmConfigSnapshot(
                id=algorithm_config.id,
                run_id=run.id,
                algorithm_version=run.algorithm_version,
                formula_version=run.formula_version,
                config=thaw(algorithm_config.config),
            )
        )
        self.session.flush()


class SqlAlchemySnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, snapshots: list[SnapshotRecord]) -> None:
        self.session.add_all(
            [
                InputSnapshot(
                    id=item.id,
                    run_id=item.run_id,
                    source_jd_id=item.source_jd_id,
                    window_id=item.window_id,
                    input_version=item.input_version,
                    schema_version=item.schema_version,
                    payload=thaw(item.payload),
                )
                for item in snapshots
            ]
        )
        self.session.flush()


class SqlAlchemyClusterRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_specs_before(
        self,
        window_start,
        window_end,
        compatibility,
    ) -> list[ClusterLineageSpec]:
        if window_start is None or window_end is None or window_start > window_end:
            return []
        candidates = (
            self.session.query(DiscoveryRun)
            .filter(
                DiscoveryRun.status == "succeeded",
                DiscoveryRun.time_window_start.is_not(None),
                DiscoveryRun.time_window_end.is_not(None),
                DiscoveryRun.time_window_start <= DiscoveryRun.time_window_end,
                DiscoveryRun.time_window_end < window_start,
            )
            .order_by(
                DiscoveryRun.time_window_end.desc(),
                DiscoveryRun.created_at.desc(),
            )
            .all()
        )
        run = next(
            (
                item
                for item in candidates
                if (
                    self.session.query(AlgorithmConfigSnapshot)
                    .filter_by(run_id=item.id)
                    .one()
                    .config
                    or {}
                ).get("lineage_compatibility")
                == compatibility
            ),
            None,
        )
        if run is None:
            return []
        run_config = (
            self.session.query(AlgorithmConfigSnapshot).filter_by(run_id=run.id).one().config or {}
        )
        windows = ((run_config.get("run_context") or {}).get("time_window") or {}).get(
            "windows", []
        )
        time_window_context = (run_config.get("run_context") or {}).get(
            "time_window"
        ) or {}
        window_id = str(
            time_window_context.get("current_observation_window_id")
            or (windows[-1].get("window_id") if windows else "unavailable")
        )
        result = []
        for cluster in self.session.query(Cluster).filter_by(run_id=run.id).all():
            memberships = (
                self.session.query(ClusterMembership).filter_by(cluster_id=cluster.id).all()
            )
            ids = [item.input_snapshot_id for item in memberships]
            snapshots = self.session.query(InputSnapshot).filter(InputSnapshot.id.in_(ids)).all()
            result.append(
                ClusterLineageSpec(
                    cluster.id,
                    frozenset(
                        "\x1f".join(
                            (
                                str((item.payload or {}).get("source_fact_id", "")),
                                str((item.payload or {}).get("source_fact_version", "")),
                            )
                        )
                        for item in snapshots
                    ),
                    tuple((cluster.feature_summary or {}).get("centroid", [])),
                    frozenset(str(skill).casefold() for skill in (cluster.core_skills or [])),
                    window_id,
                    cluster.cluster_key,
                )
            )
        return result

    def _lineage_payload(self, rows: list[ClusterLineage]) -> FrozenDict:
        cluster_ids = {
            value
            for row in rows
            for value in (row.predecessor_cluster_id, row.successor_cluster_id)
            if value
        }
        clusters = {
            row.id: row
            for row in self.session.query(Cluster).filter(Cluster.id.in_(cluster_ids)).all()
        }
        window_by_cluster: dict[str, str] = {}
        edges = []
        for row in rows:
            evidence = row.evidence or {}
            if row.predecessor_cluster_id and evidence.get("predecessor_window_id"):
                window_by_cluster[row.predecessor_cluster_id] = str(
                    evidence["predecessor_window_id"]
                )
            if row.successor_cluster_id and evidence.get("successor_window_id"):
                window_by_cluster[row.successor_cluster_id] = str(evidence["successor_window_id"])
            edges.append(
                FrozenDict(
                    {
                        "lineage_id": row.id,
                        "source": row.predecessor_cluster_id,
                        "target": row.successor_cluster_id,
                        "event": row.relation_type,
                        "weight": row.similarity_score,
                        "source_window": evidence.get("predecessor_window_id"),
                        "target_window": evidence.get("successor_window_id"),
                        "metrics": freeze(evidence.get("score_components", {})),
                        "threshold": evidence.get("threshold"),
                        "matching_basis": evidence.get("decision_reason"),
                        "evidence_summary": FrozenDict(
                            {
                                "cluster_ids": tuple(evidence.get("evidence_cluster_ids", ())),
                                "decision_version": row.decision_version,
                            }
                        ),
                    }
                )
            )
        nodes = tuple(
            FrozenDict(
                {
                    "cluster_id": cluster_id,
                    "run_id": cluster.run_id,
                    "window_id": window_by_cluster.get(cluster_id, "unavailable"),
                    "label": cluster.cluster_name,
                    "member_count": cluster.sample_count,
                }
            )
            for cluster_id, cluster in sorted(clusters.items())
        )
        return FrozenDict({"nodes": nodes, "edges": tuple(edges)})

    def lineage_graph(self, run_id: str) -> FrozenDict:
        if self.session.get(DiscoveryRun, run_id) is None:
            raise LookupError("Discovery run not found")
        rows = (
            self.session.query(ClusterLineage)
            .filter_by(run_id=run_id)
            .order_by(ClusterLineage.id)
            .all()
        )
        payload = self._lineage_payload(rows)
        return FrozenDict({"run_id": run_id, **dict(payload)})

    def trajectory(self, cluster_id: str) -> FrozenDict:
        if self.session.get(Cluster, cluster_id) is None:
            raise LookupError("Cluster not found")
        rows = self.session.query(ClusterLineage).order_by(ClusterLineage.created_at).all()
        selected: list[ClusterLineage] = []
        reachable = {cluster_id}
        changed = True
        while changed:
            changed = False
            for row in rows:
                endpoints = {row.predecessor_cluster_id, row.successor_cluster_id} - {None}
                if endpoints & reachable and row not in selected:
                    selected.append(row)
                    before = len(reachable)
                    reachable.update(endpoints)
                    changed = changed or len(reachable) != before
        payload = self._lineage_payload(selected)
        return FrozenDict({"cluster_id": cluster_id, **dict(payload)})

    def memberships(self, cluster_id: str) -> FrozenDict:
        if self.session.get(Cluster, cluster_id) is None:
            raise LookupError("Cluster not found")
        rows = (
            self.session.query(ClusterMembership, InputSnapshot)
            .join(InputSnapshot, InputSnapshot.id == ClusterMembership.input_snapshot_id)
            .filter(ClusterMembership.cluster_id == cluster_id)
            .order_by(InputSnapshot.window_id, InputSnapshot.source_jd_id)
            .all()
        )
        return FrozenDict(
            {
                "cluster_id": cluster_id,
                "memberships": tuple(
                    FrozenDict(
                        {
                            "membership_id": membership.id,
                            "membership_score": membership.membership_score,
                            "input_snapshot_id": snapshot.id,
                            "source_jd_id": snapshot.source_jd_id,
                            "window_id": snapshot.window_id,
                            "input_version": snapshot.input_version,
                            "source_fact_id": (snapshot.payload or {}).get("source_fact_id"),
                            "title": (snapshot.payload or {}).get("title"),
                            "source_name": (snapshot.payload or {}).get("source_name"),
                        }
                    )
                    for membership, snapshot in rows
                ),
            }
        )

    def add_many(self, clusters: list[ClusterAggregate], lineages: list[LineageRecord]) -> None:
        for item in clusters:
            self.session.add(
                Cluster(
                    id=item.id,
                    run_id=item.run_id,
                    cluster_key=item.cluster_key,
                    cluster_name=item.cluster_name,
                    sample_count=item.sample_count,
                    core_skills=thaw(item.core_skills),
                    representative_titles=thaw(item.representative_titles),
                    representative_members=thaw(item.representative_members),
                    core_responsibilities=thaw(item.core_responsibilities),
                    semantic_centroid=thaw(item.semantic_centroid),
                    algorithm_sources=thaw(item.algorithm_sources),
                    merge_basis=thaw(item.merge_basis),
                    stability_score=item.stability_score,
                    growth_score=item.growth_score,
                    distance_from_existing_positions=item.distance_from_existing_positions,
                    feature_summary={
                        **thaw(algorithm_metadata_contract(item.feature_summary.metadata)),
                        "centroid": list(item.feature_summary.centroid),
                    },
                )
            )
            self.session.add_all(
                [
                    ClusterMembership(
                        id=membership.id,
                        cluster_id=item.id,
                        input_snapshot_id=membership.input_snapshot_id,
                        membership_score=membership.membership_score,
                    )
                    for membership in item.memberships
                ]
            )
            assessment = item.assessment
            self.session.add(
                GerminationAssessment(
                    id=assessment.id,
                    cluster_id=item.id,
                    score=assessment.result.germination_score,
                    level=assessment.result.level,
                    qualified_as_emerging=assessment.result.qualified_as_emerging,
                    dimensions=asdict(assessment.result.dimensions),
                    evidence_package=thaw(assessment.evidence_package),
                    generated_definition=_generated_definition_payload(
                        assessment.generated_definition
                    ),
                    decision_reason=assessment.result.decision_reason,
                )
            )
            self.session.flush()
        self.session.add_all(
            [
                ClusterLineage(
                    id=item.id,
                    run_id=item.run_id,
                    relation_type=item.relation.relation_type,
                    predecessor_cluster_id=item.relation.predecessor_cluster_id,
                    successor_cluster_id=item.relation.successor_cluster_id,
                    similarity_score=item.relation.similarity_score,
                    evidence={
                        "evidence_cluster_ids": list(item.relation.evidence_cluster_ids),
                        "score_components": vars(item.relation.score)
                        if item.relation.score
                        else {},
                        "threshold": item.relation.threshold,
                        "decision_reason": item.relation.decision_reason,
                        "predecessor_window_id": item.relation.predecessor_window_id,
                        "successor_window_id": item.relation.successor_window_id,
                    },
                    decision_version=item.relation.decision_version,
                )
                for item in lineages
            ]
        )
        self.session.flush()

    def result(self, run_id: str, contract_version: str) -> DiscoveryResult:
        run = self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise LookupError("Discovery run not found")
        clusters = self.session.query(Cluster).filter_by(run_id=run.id).order_by(Cluster.id).all()
        items = []
        for cluster in clusters:
            assessment = (
                self.session.query(GerminationAssessment).filter_by(cluster_id=cluster.id).one()
            )
            memberships = (
                self.session.query(ClusterMembership).filter_by(cluster_id=cluster.id).all()
            )
            snapshot_ids = [item.input_snapshot_id for item in memberships]
            snapshots = (
                self.session.query(InputSnapshot)
                .filter(InputSnapshot.id.in_(snapshot_ids))
                .order_by(InputSnapshot.source_jd_id)
                .all()
            )
            definition = _generated_definition(assessment.generated_definition)
            items.append(
                DiscoveryClusterResult(
                    cluster_id=cluster.id,
                    cluster_name=cluster.cluster_name,
                    sample_count=cluster.sample_count,
                    core_skills=definition.required_skills,
                    representative_titles=tuple(cluster.representative_titles),
                    representative_jd_ids=tuple(item.source_jd_id for item in snapshots),
                    representative_members=tuple(
                        freeze(item) for item in cluster.representative_members
                    ),
                    core_responsibilities=tuple(cluster.core_responsibilities),
                    semantic_centroid=tuple(cluster.semantic_centroid),
                    algorithm_sources=tuple(cluster.algorithm_sources),
                    merge_basis=freeze(cluster.merge_basis),
                    stability_score=cluster.stability_score,
                    growth_score=cluster.growth_score,
                    distance_from_existing_positions=cluster.distance_from_existing_positions,
                    feature_summary=_feature_summary(cluster.feature_summary),
                    germination_assessment=DiscoveryAssessment(
                        germination_score=assessment.score,
                        score_dimensions=assessment.dimensions,
                        level=assessment.level,
                        qualified_as_emerging=assessment.qualified_as_emerging,
                        decision_reason=assessment.decision_reason,
                        evidence_package=assessment.evidence_package,
                    ),
                    generated_definition=definition,
                )
            )
        lineage_items = []
        for item in (
            self.session.query(ClusterLineage).filter_by(run_id=run.id).order_by(ClusterLineage.id)
        ):
            score_data = (item.evidence or {}).get("score_components") or {}
            score = LineageScore(**score_data) if score_data else None
            lineage_items.append(
                DiscoveryLineageResult(
                    LineageRelation(
                        relation_type=item.relation_type,
                        predecessor_cluster_id=item.predecessor_cluster_id,
                        successor_cluster_id=item.successor_cluster_id,
                        similarity_score=item.similarity_score,
                        evidence_cluster_ids=tuple(
                            (item.evidence or {}).get("evidence_cluster_ids", ())
                        ),
                        score=score,
                        predecessor_window_id=(item.evidence or {}).get("predecessor_window_id"),
                        successor_window_id=(item.evidence or {}).get("successor_window_id"),
                        decision_version=item.decision_version,
                        threshold=float((item.evidence or {}).get("threshold", 0.35)),
                        decision_reason=str(
                            (item.evidence or {}).get(
                                "decision_reason",
                                "historical lineage decision",
                            )
                        ),
                    )
                )
            )
        config_model = self.session.query(AlgorithmConfigSnapshot).filter_by(run_id=run.id).one()
        quality_report = freeze((config_model.config or {}).get("input_quality_report", {}))
        if not isinstance(quality_report, FrozenDict):
            quality_report = FrozenDict()
        run_context = freeze((config_model.config or {}).get("run_context", {}))
        if not isinstance(run_context, FrozenDict):
            run_context = FrozenDict()
        persisted_snapshots = (
            self.session.query(InputSnapshot)
            .filter_by(run_id=run.id)
            .order_by(InputSnapshot.source_jd_id)
            .all()
        )
        payload_fingerprint = _persisted_payload_fingerprint(
            config_model.config or {},
            [item.payload or {} for item in persisted_snapshots],
        )
        return DiscoveryResult(
            contract_version=contract_version,
            run_id=run.id,
            request_id=run.request_id,
            status=run.status,
            algorithm_version=run.algorithm_version,
            formula_version=run.formula_version,
            created_at=_utc(run.created_at),
            completed_at=_utc(run.completed_at),
            clusters=tuple(items),
            lineages=tuple(lineage_items),
            input_quality_report=quality_report,
            run_context=run_context,
            payload_fingerprint=payload_fingerprint or "",
        )


class SqlAlchemyCandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def active_candidates(self) -> list[CandidateRecord]:
        rows = (
            self.session.query(Candidate)
            .filter(Candidate.status.notin_(("dead", "noise")))
            .order_by(Candidate.id)
            .all()
        )
        return [
            _candidate_record(item)
            for item in rows
            if _candidate_resolution_state(item)
            not in ("pending_review", "resolved_same")
        ]

    def candidate(self, candidate_id: str) -> CandidateRecord | None:
        model = self.session.get(Candidate, candidate_id)
        return _candidate_record(model) if model is not None else None

    def _canonical_model(self, model: Candidate) -> Candidate:
        canonical_id = (model.identity_profile or {}).get("canonical_candidate_id")
        if canonical_id is None:
            return model
        canonical = self.session.get(Candidate, str(canonical_id))
        return canonical if canonical is not None else model

    def _candidate_group_ids(self, model: Candidate) -> tuple[str, ...]:
        canonical = self._canonical_model(model)
        ids = {canonical.id, model.id}
        for item in self.session.query(Candidate).all():
            if (
                (item.identity_profile or {}).get("canonical_candidate_id")
                == canonical.id
            ):
                ids.add(item.id)
        return tuple(sorted(ids))

    def save(self, candidate: CandidateRecord) -> None:
        model = self.session.get(Candidate, candidate.id)
        values = {
            "status": candidate.status,
            "first_seen_window_id": candidate.first_seen_window_id,
            "last_seen_window_id": candidate.last_seen_window_id,
            "age": candidate.age,
            "current_cluster_id": candidate.current_cluster_id,
            "previous_cluster_ids": list(candidate.previous_cluster_ids),
            "canonical_title": candidate.canonical_title,
            "display_title": candidate.display_title,
            "definition": thaw(candidate.definition),
            "support_count": candidate.support_count,
            "company_coverage": candidate.company_coverage,
            "skill_similarity": candidate.skill_similarity,
            "responsibility_similarity": candidate.responsibility_similarity,
            "title_similarity": candidate.title_similarity,
            "membership_overlap": candidate.membership_overlap,
            "identity_similarity": candidate.identity_similarity,
            "novelty_score": candidate.novelty_score,
            "emergence_score": candidate.emergence_score,
            "evidence": thaw(candidate.evidence),
            "identity_stability": candidate.identity_stability,
            "identity_profile": {
                "titles": list(candidate.titles),
                "skills": list(candidate.skills),
                "responsibilities": list(candidate.responsibilities),
                "member_jd_ids": list(candidate.member_jd_ids),
                "observed_window_ids": list(candidate.observed_window_ids),
                "semantic_centroid": list(candidate.semantic_centroid),
                "evidence_titles": list(candidate.evidence_titles),
                "evidence_skills": list(candidate.evidence_skills),
                "evidence_responsibilities": list(
                    candidate.evidence_responsibilities
                ),
                "member_evidence_ids": list(candidate.member_evidence_ids),
                "member_dedup_cluster_ids": list(
                    candidate.member_dedup_cluster_ids
                ),
                "member_template_cluster_ids": list(
                    candidate.member_template_cluster_ids
                ),
                "identity_certificate": thaw(candidate.identity_certificate),
                "lifecycle_state_v2": thaw(candidate.lifecycle_state_v2),
                "identity_resolution_state": candidate.identity_resolution_state,
                "identity_resolution": thaw(candidate.identity_resolution),
                "canonical_candidate_id": candidate.canonical_candidate_id,
            },
            "updated_at": candidate.updated_at,
        }
        if model is None:
            self.session.add(
                Candidate(
                    id=candidate.id,
                    created_at=candidate.created_at,
                    **values,
                )
            )
        else:
            for name, value in values.items():
                setattr(model, name, value)
        self.session.flush()

    def add_observation(self, observation: CandidateObservationRecord) -> None:
        existing = (
            self.session.query(CandidateClusterObservation.id)
            .filter(
                CandidateClusterObservation.candidate_id
                == observation.candidate_id,
                CandidateClusterObservation.window_id
                == observation.window_id,
            )
            .first()
        )
        if existing is not None:
            raise ValueError(
                f"candidate {observation.candidate_id} already observed "
                f"in window {observation.window_id}"
            )
        self.session.add(
            CandidateClusterObservation(
                id=observation.id,
                candidate_id=observation.candidate_id,
                run_id=observation.run_id,
                cluster_id=observation.cluster_id,
                window_id=observation.window_id,
                title=observation.title,
                status=observation.status,
                emergence_score=observation.emergence_score,
                support_count=observation.support_count,
                company_count=observation.company_count,
                identity_similarity=observation.identity_similarity,
                skill_similarity=observation.skill_similarity,
                responsibility_similarity=observation.responsibility_similarity,
                title_similarity=observation.title_similarity,
                membership_overlap=observation.membership_overlap,
                semantic_similarity=observation.semantic_similarity,
                evidence=thaw(observation.evidence),
                match_evidence=thaw(observation.match_evidence),
            )
        )
        self.session.flush()

    def add_transition(self, transition: CandidateTransitionRecord) -> None:
        self.session.add(
            CandidateStatusTransition(
                id=transition.id,
                candidate_id=transition.candidate_id,
                from_status=transition.from_status,
                to_status=transition.to_status,
                reason=transition.reason,
                run_id=transition.run_id,
                window_id=transition.window_id,
                transition_version=transition.transition_version,
                timestamp=transition.timestamp,
                details=thaw(transition.details),
            )
        )
        self.session.flush()

    def save_lineage(
        self,
        run_id: str,
        relations: list[DomainCandidateLineageRelation],
        reviews: list[CandidateLineageReviewRecord],
    ) -> None:
        for relation in relations:
            existing = (
                self.session.query(CandidateLineageRelationModel.id)
                .filter(
                    CandidateLineageRelationModel.relation_id == relation.relation_id
                )
                .first()
            )
            if existing is not None:
                raise ValueError(
                    f"duplicate candidate lineage relation: {relation.relation_id}"
                )
            self.session.add(
                CandidateLineageRelationModel(
                    relation_id=relation.relation_id,
                    run_id=run_id,
                    relation_type=relation.relation_type,
                    source_candidate_ids=list(relation.source_candidate_ids),
                    target_candidate_ids=list(relation.target_candidate_ids),
                    source_window_id=relation.source_window_id,
                    target_window_id=relation.target_window_id,
                    confidence=float(relation.confidence),
                    evidence=[item.to_dict() for item in relation.evidence],
                    decision_basis=list(relation.decision_basis),
                    review_required=relation.review_required,
                    algorithm_version=relation.algorithm_version,
                    model_version=relation.model_version,
                    source_cluster_ids=list(relation.source_cluster_ids),
                    target_cluster_ids=list(relation.target_cluster_ids),
                    proposed_target_candidate_ids=list(
                        relation.proposed_target_candidate_ids
                    ),
                    support_inflation=relation.support_inflation,
                    observation_delta=relation.observation_delta,
                )
            )
        for review in reviews:
            existing = (
                self.session.query(CandidateLineageReviewModel.id)
                .filter(CandidateLineageReviewModel.review_id == review.review_id)
                .first()
            )
            if existing is not None:
                raise ValueError(
                    f"duplicate candidate lineage review: {review.review_id}"
                )
            self.session.add(
                CandidateLineageReviewModel(
                    review_id=review.review_id,
                    run_id=run_id,
                    source_window_id=review.source_window_id,
                    target_window_id=review.target_window_id,
                    cluster_ids=list(review.cluster_ids),
                    candidate_ids=list(review.candidate_ids),
                    decision_basis=list(review.decision_basis),
                    hypotheses=[dict(item) for item in review.hypotheses],
                    confidence=review.confidence,
                    algorithm_version=review.algorithm_version,
                )
            )
        self.session.flush()

    def candidate_lineage_relations(
        self,
    ) -> tuple[DomainCandidateLineageRelation, ...]:
        rows = (
            self.session.query(CandidateLineageRelationModel)
            .order_by(CandidateLineageRelationModel.created_at, CandidateLineageRelationModel.id)
            .all()
        )
        return tuple(_candidate_lineage_relation(row) for row in rows)

    def candidate_lineage_reviews(
        self,
    ) -> tuple[CandidateLineageReviewRecord, ...]:
        rows = (
            self.session.query(CandidateLineageReviewModel)
            .order_by(CandidateLineageReviewModel.created_at, CandidateLineageReviewModel.id)
            .all()
        )
        return tuple(_candidate_lineage_review_record(row) for row in rows)

    def lineage_relations_by_source(
        self,
        candidate_id: str,
    ) -> tuple[DomainCandidateLineageRelation, ...]:
        rows = (
            self.session.query(CandidateLineageRelationModel)
            .filter(
                CandidateLineageRelationModel.source_candidate_ids.contains(
                    [candidate_id]
                )
            )
            .order_by(CandidateLineageRelationModel.created_at, CandidateLineageRelationModel.id)
            .all()
        )
        return tuple(_candidate_lineage_relation(row) for row in rows)

    def lineage_relations_by_target(
        self,
        candidate_id: str,
    ) -> tuple[DomainCandidateLineageRelation, ...]:
        rows = (
            self.session.query(CandidateLineageRelationModel)
            .filter(
                CandidateLineageRelationModel.target_candidate_ids.contains(
                    [candidate_id]
                )
            )
            .order_by(CandidateLineageRelationModel.created_at, CandidateLineageRelationModel.id)
            .all()
        )
        return tuple(_candidate_lineage_relation(row) for row in rows)

    def lineage_relations_by_transition(
        self,
        source_window_id: str,
        target_window_id: str,
    ) -> tuple[DomainCandidateLineageRelation, ...]:
        rows = (
            self.session.query(CandidateLineageRelationModel)
            .filter(
                CandidateLineageRelationModel.source_window_id == source_window_id,
                CandidateLineageRelationModel.target_window_id == target_window_id,
            )
            .order_by(CandidateLineageRelationModel.relation_id)
            .all()
        )
        return tuple(_candidate_lineage_relation(row) for row in rows)

    @staticmethod
    def _identity_evidence_snapshot(
        model: InputSnapshot,
    ) -> CandidateIdentityEvidenceSnapshotRecord:
        payload = model.payload or {}
        structured = payload.get("structured_data") or {}
        company = next(
            (
                str(structured[key]).strip()
                for key in ("enterprise_id", "company_id", "company_name")
                if structured.get(key) is not None and str(structured[key]).strip()
            ),
            None,
        )
        return CandidateIdentityEvidenceSnapshotRecord(
            source_jd_id=model.source_jd_id,
            source_name=(
                str(payload["source_name"]).strip()
                if payload.get("source_name") is not None
                and str(payload["source_name"]).strip()
                else None
            ),
            company=company,
            evidence_ref=(
                f"{payload.get('source_fact_id') or model.source_jd_id}:"
                f"{model.input_version}"
            ),
        )

    def ambiguous_identity_pairs(
        self, observation_id: str | None = None
    ) -> tuple[AmbiguousIdentityPairRecord, ...]:
        query = self.session.query(CandidateClusterObservation)
        if observation_id is not None:
            query = query.filter(CandidateClusterObservation.id == observation_id)
        observations = query.order_by(
            CandidateClusterObservation.run_id,
            CandidateClusterObservation.window_id,
            CandidateClusterObservation.id,
        ).all()
        result: list[AmbiguousIdentityPairRecord] = []
        for model in observations:
            match = model.match_evidence or {}
            certificate = match.get("continuity_certificate") or {}
            decision = certificate.get("decision") or match.get("identity_decision")
            closest_id = certificate.get("closest_candidate_id") or match.get(
                "closest_candidate_id"
            )
            if decision != "review_required" or not closest_id:
                continue
            candidate_a_model = self.session.get(Candidate, model.candidate_id)
            candidate_b_model = self.session.get(Candidate, str(closest_id))
            if candidate_a_model is None or candidate_b_model is None:
                continue
            candidate_a_ids = set(
                (candidate_a_model.identity_profile or {}).get("member_jd_ids", ())
            )
            candidate_b_ids = set(
                (candidate_b_model.identity_profile or {}).get("member_jd_ids", ())
            )
            snapshots = (
                self.session.query(InputSnapshot)
                .filter(InputSnapshot.source_jd_id.in_(candidate_a_ids | candidate_b_ids))
                .order_by(InputSnapshot.source_jd_id, InputSnapshot.input_version)
                .all()
                if candidate_a_ids or candidate_b_ids
                else []
            )
            candidate_a_evidence = set(
                (candidate_a_model.identity_profile or {}).get(
                    "member_evidence_ids", ()
                )
            )
            candidate_b_evidence = set(
                (candidate_b_model.identity_profile or {}).get(
                    "member_evidence_ids", ()
                )
            )

            def belongs_to(
                item: InputSnapshot, source_ids: set[str], evidence_ids: set[str]
            ) -> bool:
                if item.source_jd_id not in source_ids:
                    return False
                evidence_ref = (
                    f"{(item.payload or {}).get('source_fact_id') or item.source_jd_id}:"
                    f"{item.input_version}"
                )
                return not evidence_ids or evidence_ref in evidence_ids

            result.append(
                AmbiguousIdentityPairRecord(
                    observation=_candidate_observation_record(model),
                    candidate_a=_candidate_record(candidate_a_model),
                    candidate_b=_candidate_record(candidate_b_model),
                    candidate_a_snapshots=tuple(
                        self._identity_evidence_snapshot(item)
                        for item in snapshots
                        if belongs_to(
                            item, candidate_a_ids, candidate_a_evidence
                        )
                    ),
                    candidate_b_snapshots=tuple(
                        self._identity_evidence_snapshot(item)
                        for item in snapshots
                        if belongs_to(
                            item, candidate_b_ids, candidate_b_evidence
                        )
                    ),
                )
            )
        return tuple(result)

    def candidate_diffusion(self, candidate_id: str) -> CandidateDiffusionRecord:
        model = self.session.get(Candidate, candidate_id)
        if model is None:
            raise LookupError("Candidate not found")
        canonical = self._canonical_model(model)
        candidate_ids = self._candidate_group_ids(model)
        rows = (
            self.session.query(
                CandidateClusterObservation,
                DiscoveryRun,
                AlgorithmConfigSnapshot,
            )
            .join(DiscoveryRun, DiscoveryRun.id == CandidateClusterObservation.run_id)
            .join(
                AlgorithmConfigSnapshot,
                AlgorithmConfigSnapshot.run_id == CandidateClusterObservation.run_id,
            )
            .filter(CandidateClusterObservation.candidate_id.in_(candidate_ids))
            .order_by(
                CandidateClusterObservation.created_at,
                CandidateClusterObservation.window_id,
                CandidateClusterObservation.id,
            )
            .all()
        )
        result = []
        for observation, run, config in rows:
            memberships = (
                self.session.query(ClusterMembership, InputSnapshot)
                .join(
                    InputSnapshot,
                    InputSnapshot.id == ClusterMembership.input_snapshot_id,
                )
                .filter(ClusterMembership.cluster_id == observation.cluster_id)
                .order_by(InputSnapshot.source_jd_id, InputSnapshot.input_version)
                .all()
            )
            evidence = []
            for _, snapshot in memberships:
                payload = snapshot.payload or {}
                structured = payload.get("structured_data") or {}
                company = next(
                    (
                        str(structured[key]).strip()
                        for key in ("enterprise_id", "company_id", "company_name")
                        if structured.get(key) is not None
                        and str(structured[key]).strip()
                    ),
                    None,
                )
                content_hash = str(payload.get("content_hash") or "").casefold()
                evidence.append(
                    CandidateDiffusionEvidenceRecord(
                        input_snapshot_id=snapshot.id,
                        source_jd_id=snapshot.source_jd_id,
                        source_fact_id=str(
                            payload.get("source_fact_id") or snapshot.source_jd_id
                        ),
                        input_version=snapshot.input_version,
                        window_id=snapshot.window_id,
                        source_name=(
                            str(payload["source_name"]).strip()
                            if payload.get("source_name") is not None
                            and str(payload["source_name"]).strip()
                            else None
                        ),
                        company=company,
                        content_hash=(
                            content_hash
                            if content_hash.startswith("sha256:")
                            else None
                        ),
                    )
                )
            config_value = config.config or {}
            result.append(
                CandidateDiffusionObservationRecord(
                    observation=_candidate_observation_record(observation),
                    observed_at=observation.created_at,
                    algorithm_version=run.algorithm_version,
                    formula_version=run.formula_version,
                    config_snapshot_id=config.id,
                    config_version=(
                        str((observation.match_evidence or {})["config_version"])
                        if (observation.match_evidence or {}).get("config_version")
                        is not None
                        else str(config_value["candidate_identity_config_version"])
                        if config_value.get("candidate_identity_config_version") is not None
                        else None
                    ),
                    evidence=tuple(evidence),
                )
            )
        return CandidateDiffusionRecord(
            candidate=_candidate_record(canonical), observations=tuple(result)
        )

    def list_candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> FrozenDict:
        query = self.session.query(Candidate)
        if status:
            query = query.filter(Candidate.status == status)
        if candidate_id:
            query = query.filter(Candidate.id == candidate_id)
        if window_id:
            query = (
                query.join(
                    CandidateClusterObservation,
                    CandidateClusterObservation.candidate_id == Candidate.id,
                ).filter(CandidateClusterObservation.window_id == window_id)
            )
        rows = query.order_by(Candidate.updated_at.desc(), Candidate.id).all()
        visible: list[Candidate] = []
        seen: set[str] = set()
        for item in rows:
            canonical_id = (item.identity_profile or {}).get("canonical_candidate_id")
            if canonical_id:
                if candidate_id is not None and item.id == candidate_id:
                    canonical = self.session.get(Candidate, str(canonical_id))
                    if canonical is not None and canonical.id not in seen:
                        visible.append(canonical)
                        seen.add(canonical.id)
                continue
            if item.id not in seen:
                visible.append(item)
                seen.add(item.id)
        return FrozenDict(
            {
                "candidates": tuple(_candidate_data(item) for item in visible),
                "filters": FrozenDict(
                    {
                        "status": status,
                        "candidate_id": candidate_id,
                        "window_id": window_id,
                    }
                ),
            }
        )

    def detail(self, candidate_id: str) -> FrozenDict:
        model = self.session.get(Candidate, candidate_id)
        if model is None:
            raise LookupError("Candidate not found")
        canonical = self._canonical_model(model)
        candidate_ids = self._candidate_group_ids(model)
        observation = (
            self.session.query(CandidateClusterObservation)
            .filter(CandidateClusterObservation.candidate_id.in_(candidate_ids))
            .order_by(
                CandidateClusterObservation.window_id.desc(),
                CandidateClusterObservation.created_at.desc(),
            )
            .first()
        )
        return FrozenDict(
            {
                "candidate": _candidate_data(canonical),
                "latest_observation": (
                    _observation_data(observation, None) if observation else None
                ),
            }
        )

    def trajectory(self, candidate_id: str) -> FrozenDict:
        model = self.session.get(Candidate, candidate_id)
        if model is None:
            raise LookupError("Candidate not found")
        canonical = self._canonical_model(model)
        candidate_ids = self._candidate_group_ids(model)
        rows = (
            self.session.query(CandidateClusterObservation, Cluster)
            .join(Cluster, Cluster.id == CandidateClusterObservation.cluster_id)
            .filter(CandidateClusterObservation.candidate_id.in_(candidate_ids))
            .order_by(
                CandidateClusterObservation.created_at,
            )
            .all()
        )
        transitions = (
            self.session.query(CandidateStatusTransition)
            .filter(CandidateStatusTransition.candidate_id.in_(candidate_ids))
            .order_by(CandidateStatusTransition.timestamp, CandidateStatusTransition.created_at)
            .all()
        )
        relations = self.candidate_lineage_relations()
        observation_records = [
            _candidate_observation_record(observation)
            for observation, _cluster in rows
        ]
        window_order = tuple(
            item.window_id for item in self.lifecycle_windows()
        )
        reconstructed = reconstruct_lineage_trajectory(
            canonical.id,
            observation_records,
            relations,
            window_order=window_order,
        )
        return FrozenDict(
            {
                "candidate_id": canonical.id,
                "trajectory": tuple(
                    _observation_data(observation, cluster.cluster_name)
                    for observation, cluster in rows
                ),
                "transitions": tuple(
                    FrozenDict(
                        {
                            "transition_id": item.id,
                            "run_id": item.run_id,
                            "window_id": item.window_id,
                            "from_status": item.from_status,
                            "to_status": item.to_status,
                            "reason": item.reason,
                            "transition_version": item.transition_version,
                            "details": thaw(freeze(item.details or {})),
                            "timestamp": item.timestamp,
                        }
                    )
                    for item in transitions
                ),
                "legacy_continuity": tuple(
                    FrozenDict(step.to_dict())
                    for step in reconstructed.legacy_continuity
                ),
                "lineage_aware_continuity": tuple(
                    FrozenDict(step.to_dict())
                    for step in reconstructed.lineage_aware_continuity
                ),
                "lineage_reachable_candidate_ids": (
                    reconstructed.reachable_candidate_ids
                ),
                "lineage_relations_used": tuple(
                    FrozenDict(item) for item in reconstructed.relations_used
                ),
                "lineage_paths": tuple(
                    tuple(FrozenDict(step.to_dict()) for step in path)
                    for path in reconstructed.paths
                ),
            }
        )

    def lifecycle_trajectories(
        self, candidate_id: str | None = None
    ) -> tuple[CandidateLifecycleTrajectoryRecord, ...]:
        candidate_query = self.session.query(Candidate)
        if candidate_id is not None:
            candidate_query = candidate_query.filter(Candidate.id == candidate_id)
        candidate_models = candidate_query.order_by(Candidate.id).all()
        if candidate_id is not None and not candidate_models:
            raise LookupError("Candidate not found")
        result = []
        for model in candidate_models:
            canonical = self._canonical_model(model)
            if (
                candidate_id is None
                and _candidate_resolution_state(model) == "resolved_same"
            ):
                continue
            item_ids = self._candidate_group_ids(model)
            observations = (
                self.session.query(CandidateClusterObservation)
                .filter(CandidateClusterObservation.candidate_id.in_(item_ids))
                .order_by(CandidateClusterObservation.created_at, CandidateClusterObservation.id)
                .all()
            )
            transitions = (
                self.session.query(CandidateStatusTransition)
                .filter(CandidateStatusTransition.candidate_id.in_(item_ids))
                .order_by(CandidateStatusTransition.timestamp, CandidateStatusTransition.id)
                .all()
            )
            result.append(
                CandidateLifecycleTrajectoryRecord(
                    candidate_id=canonical.id,
                    observations=tuple(
                        _candidate_observation_record(item)
                        for item in observations
                    ),
                    transitions=tuple(
                        CandidateTransitionRecord(
                            id=item.id,
                            candidate_id=item.candidate_id,
                            from_status=item.from_status,
                            to_status=item.to_status,
                            reason=item.reason,
                            run_id=item.run_id,
                            window_id=item.window_id,
                            timestamp=item.timestamp,
                            transition_version=item.transition_version,
                            details=freeze(item.details or {}),
                        )
                        for item in transitions
                    ),
                )
            )
        return tuple(result)

    def lifecycle_windows(self) -> tuple[LifecycleWindowRecord, ...]:
        rows = (
            self.session.query(DiscoveryRun, AlgorithmConfigSnapshot)
            .join(AlgorithmConfigSnapshot, AlgorithmConfigSnapshot.run_id == DiscoveryRun.id)
            .filter(DiscoveryRun.status == "succeeded")
            .order_by(DiscoveryRun.completed_at, DiscoveryRun.id)
            .all()
        )
        result = []
        for run, config in rows:
            result.append(_lifecycle_window_record(run, config))
        return tuple(result)

    def promotion_contexts(
        self, candidate_id: str | None = None
    ) -> tuple[CandidatePromotionContextRecord, ...]:
        query = self.session.query(Candidate)
        if candidate_id is not None:
            query = query.filter(Candidate.id == candidate_id)
        candidates = query.order_by(Candidate.id).all()
        if candidate_id is not None and not candidates:
            raise LookupError("Candidate not found")

        result = []
        for candidate in candidates:
            canonical = self._canonical_model(candidate)
            if candidate.id != canonical.id and candidate_id is None:
                continue
            state = _candidate_resolution_state(canonical)
            if state in ("pending_review", "resolved_same"):
                continue
            candidate_ids = self._candidate_group_ids(candidate)
            observation = (
                self.session.query(CandidateClusterObservation)
                .filter(CandidateClusterObservation.candidate_id.in_(candidate_ids))
                .order_by(
                    CandidateClusterObservation.created_at.desc(),
                    CandidateClusterObservation.id.desc(),
                )
                .first()
            )
            run = self.session.get(DiscoveryRun, observation.run_id) if observation else None
            config = (
                self.session.query(AlgorithmConfigSnapshot)
                .filter_by(run_id=observation.run_id)
                .one_or_none()
                if observation
                else None
            )
            lifecycle_config = (
                ((config.config or {}).get("run_context") or {}).get("config") or {}
                if config
                else {}
            )
            result.append(
                CandidatePromotionContextRecord(
                    candidate=_candidate_record(canonical),
                    latest_observation=(
                        _candidate_observation_record(observation) if observation else None
                    ),
                    window=(
                        _lifecycle_window_record(run, config) if run and config else None
                    ),
                    config_snapshot_id=config.id if config else None,
                    lifecycle_config=freeze(lifecycle_config),
                )
            )
        return tuple(result)

    def identity_resolution_audits(
        self,
        *,
        provisional_candidate_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[IdentityResolutionAuditRecord, ...]:
        query = self.session.query(IdentityResolutionAudit)
        if provisional_candidate_id is not None:
            query = query.filter(
                IdentityResolutionAudit.provisional_candidate_id
                == provisional_candidate_id
            )
        if idempotency_key is not None:
            query = query.filter(
                IdentityResolutionAudit.idempotency_key == idempotency_key
            )
        rows = query.order_by(
            IdentityResolutionAudit.timestamp,
            IdentityResolutionAudit.created_at,
            IdentityResolutionAudit.id,
        ).all()
        return tuple(
            IdentityResolutionAuditRecord(
                resolution_id=item.id,
                provisional_candidate_id=item.provisional_candidate_id,
                target_candidate_id=item.target_candidate_id,
                decision=item.decision,
                reviewer=item.reviewer,
                reason=item.reason,
                window_id=item.window_id,
                timestamp=item.timestamp,
                algorithm_version=item.algorithm_version,
                idempotency_key=item.idempotency_key,
                created_at=item.created_at,
                details=freeze(item.details or {}),
            )
            for item in rows
        )

    def add_identity_resolution_audit(
        self, audit: IdentityResolutionAuditRecord
    ) -> None:
        self.session.add(
            IdentityResolutionAudit(
                id=audit.resolution_id,
                created_at=(
                    audit.created_at
                    if audit.created_at is not None
                    else datetime.now(timezone.utc)
                ),
                provisional_candidate_id=audit.provisional_candidate_id,
                target_candidate_id=audit.target_candidate_id,
                decision=audit.decision,
                reviewer=audit.reviewer,
                reason=audit.reason,
                window_id=audit.window_id,
                timestamp=audit.timestamp,
                algorithm_version=audit.algorithm_version,
                idempotency_key=audit.idempotency_key,
                details=thaw(audit.details),
            )
        )
        self.session.flush()


class SqlAlchemyDiscoveryUnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.runs = SqlAlchemyRunRepository(session)
        self.snapshots = SqlAlchemySnapshotRepository(session)
        self.clusters = SqlAlchemyClusterRepository(session)
        self.candidates = SqlAlchemyCandidateRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


class DiscoveryMaintenanceRepository:
    """Explicit, audited escape hatch; normal repositories expose no delete operation."""

    def __init__(self, session: Session, expected_token: str) -> None:
        self.session = session
        self.expected_token = expected_token

    def purge_run(
        self, run_id: str, *, actor: str, reason: str, supplied_token: str
    ) -> MaintenanceAuditRecord:
        if not hmac.compare_digest(supplied_token, self.expected_token):
            raise PermissionError("invalid discovery maintenance credential")
        if len(reason.strip()) < 10:
            raise ValueError("maintenance reason must contain at least 10 characters")
        run = self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise LookupError("discovery run not found")
        audit = DiscoveryMaintenanceAudit(
            run_id=run_id,
            action="purge_run",
            actor=actor,
            reason=reason.strip(),
            status="in_progress",
        )
        self.session.add(audit)
        self.session.flush()
        try:
            self.session.execute(text("SET LOCAL jobgraph.allow_discovery_cleanup = 'on'"))
            params = {"run_id": run_id}
            run_cluster_ids = {
                row.id for row in self.session.query(Cluster).filter_by(run_id=run_id).all()
            }
            if run_cluster_ids:
                candidates = (
                    self.session.query(Candidate)
                    .filter(Candidate.current_cluster_id.in_(run_cluster_ids))
                    .all()
                )
                for candidate in candidates:
                    candidate.current_cluster_id = None
                    candidate.previous_cluster_ids = [
                        item
                        for item in candidate.previous_cluster_ids
                        if item not in run_cluster_ids
                    ]
            self.session.query(CandidateStatusTransition).filter_by(run_id=run_id).delete(
                synchronize_session=False
            )
            self.session.query(CandidateClusterObservation).filter_by(run_id=run_id).delete(
                synchronize_session=False
            )
            orphans = (
                self.session.query(Candidate)
                .outerjoin(
                    CandidateClusterObservation,
                    CandidateClusterObservation.candidate_id == Candidate.id,
                )
                .filter(CandidateClusterObservation.id.is_(None))
                .all()
            )
            for orphan in orphans:
                self.session.delete(orphan)
            self.session.flush()
            self.session.execute(
                text(
                    "DELETE FROM germination_assessments WHERE cluster_id IN "
                    "(SELECT id FROM clusters WHERE run_id=:run_id)"
                ),
                params,
            )
            if run_cluster_ids:
                self.session.execute(
                    text(
                        "DELETE FROM cluster_lineages "
                        "WHERE run_id=:run_id "
                        "OR predecessor_cluster_id = any(:cluster_ids) "
                        "OR successor_cluster_id = any(:cluster_ids)"
                    ),
                    {"run_id": run_id, "cluster_ids": sorted(run_cluster_ids)},
                )
            else:
                self.session.execute(
                    text("DELETE FROM cluster_lineages WHERE run_id=:run_id"),
                    params,
                )
            self.session.execute(
                text("DELETE FROM candidate_lineage_reviews WHERE run_id=:run_id"),
                params,
            )
            self.session.execute(
                text("DELETE FROM candidate_lineage_relations WHERE run_id=:run_id"),
                params,
            )
            self.session.execute(
                text(
                    "DELETE FROM cluster_memberships WHERE cluster_id IN "
                    "(SELECT id FROM clusters WHERE run_id=:run_id)"
                ),
                params,
            )
            for table in ("clusters", "algorithm_config_snapshots", "input_snapshots"):
                self.session.execute(text(f"DELETE FROM {table} WHERE run_id=:run_id"), params)
            self.session.execute(text("DELETE FROM discovery_runs WHERE id=:run_id"), params)
            audit.status = "completed"
            audit.completed_at = datetime.now(timezone.utc)
            return MaintenanceAuditRecord(
                audit_id=audit.id,
                run_id=audit.run_id,
                action=audit.action,
                status=audit.status,
                actor=audit.actor,
                reason=audit.reason,
                completed_at=audit.completed_at,
            )
        except Exception:
            raise


class DiscoveryMaintenanceUnitOfWork:
    def __init__(self, session: Session, expected_token: str) -> None:
        self.session = session
        self.maintenance = DiscoveryMaintenanceRepository(session, expected_token)

    def __enter__(self) -> "DiscoveryMaintenanceUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
