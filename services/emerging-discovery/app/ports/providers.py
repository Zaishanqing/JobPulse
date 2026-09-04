from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence

from app.application.contracts import (
    AlgorithmEvaluationResult,
    AlgorithmSelection,
    DiscoveryConfig,
    DiscoveryResult,
)
from app.domain.discovery import (
    AlgorithmCluster,
    AlgorithmOutput,
    GeneratedDefinition,
    JDSnapshot,
    PositionReference,
)
from app.domain.lineage import ClusterLineageSpec, LineageRelation
from app.domain.candidate_lineage import CandidateLineageRelation
from app.domain.germination import GerminationAssessmentResult
from app.domain.values import JsonObject
from app.ports.records import (
    AlgorithmConfigRecord,
    AmbiguousIdentityPairRecord,
    CandidateLifecycleTrajectoryRecord,
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


class RunRepository(Protocol):
    def by_request_id(self, request_id: str) -> RunRecord | None: ...
    def by_id(self, run_id: str) -> RunRecord | None: ...
    def latest_succeeded(self) -> RunRecord | None: ...
    def fingerprint_by_run_id(self, run_id: str) -> str | None: ...
    def add(self, run: RunRecord, algorithm_config: AlgorithmConfigRecord) -> None: ...


class SnapshotRepository(Protocol):
    def add_many(self, snapshots: list[SnapshotRecord]) -> None: ...


class ClusterRepository(Protocol):
    def latest_specs_before(
        self,
        window_start: date | None,
        window_end: date | None,
        compatibility: JsonObject,
    ) -> list[ClusterLineageSpec]: ...
    def add_many(
        self,
        clusters: list[ClusterAggregate],
        lineages: list[LineageRecord],
    ) -> None: ...
    def result(self, run_id: str, contract_version: str) -> DiscoveryResult: ...
    def lineage_graph(self, run_id: str) -> JsonObject: ...
    def trajectory(self, cluster_id: str) -> JsonObject: ...
    def memberships(self, cluster_id: str) -> JsonObject: ...


class CandidateRepository(Protocol):
    def active_candidates(self) -> list[CandidateRecord]: ...
    def candidate(self, candidate_id: str) -> CandidateRecord | None: ...
    def save(self, candidate: CandidateRecord) -> None: ...
    def add_observation(self, observation: CandidateObservationRecord) -> None: ...
    def add_transition(self, transition: CandidateTransitionRecord) -> None: ...
    def list_candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> JsonObject: ...
    def detail(self, candidate_id: str) -> JsonObject: ...
    def trajectory(self, candidate_id: str) -> JsonObject: ...
    def lifecycle_trajectories(
        self, candidate_id: str | None = None
    ) -> tuple[CandidateLifecycleTrajectoryRecord, ...]: ...
    def lifecycle_windows(self) -> tuple[LifecycleWindowRecord, ...]: ...
    def promotion_contexts(
        self, candidate_id: str | None = None
    ) -> tuple[CandidatePromotionContextRecord, ...]: ...
    def ambiguous_identity_pairs(
        self, observation_id: str | None = None
    ) -> tuple[AmbiguousIdentityPairRecord, ...]: ...
    def candidate_diffusion(self, candidate_id: str) -> CandidateDiffusionRecord: ...
    def identity_resolution_audits(
        self,
        *,
        provisional_candidate_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[IdentityResolutionAuditRecord, ...]: ...
    def add_identity_resolution_audit(
        self, audit: IdentityResolutionAuditRecord
    ) -> None: ...
    def save_lineage(
        self,
        run_id: str,
        relations: Sequence[CandidateLineageRelation],
        reviews: Sequence[CandidateLineageReviewRecord],
    ) -> None: ...
    def candidate_lineage_relations(
        self,
    ) -> tuple[CandidateLineageRelation, ...]: ...
    def candidate_lineage_reviews(
        self,
    ) -> tuple[CandidateLineageReviewRecord, ...]: ...
    def lineage_relations_by_source(
        self,
        candidate_id: str,
    ) -> tuple[CandidateLineageRelation, ...]: ...
    def lineage_relations_by_target(
        self,
        candidate_id: str,
    ) -> tuple[CandidateLineageRelation, ...]: ...
    def lineage_relations_by_transition(
        self,
        source_window_id: str,
        target_window_id: str,
    ) -> tuple[CandidateLineageRelation, ...]: ...


class ReferencePort(Protocol):
    def resolve(
        self, references: tuple[PositionReference, ...]
    ) -> tuple[PositionReference, ...]: ...


class EmbeddingPort(Protocol):
    version: str

    def embed(self, snapshots: tuple[JDSnapshot, ...]) -> tuple[tuple[float, ...], ...]: ...


class ClusteringPort(Protocol):
    version: str
    random_seed: int

    def cluster(
        self, snapshots: tuple[JDSnapshot, ...], embeddings: tuple[tuple[float, ...], ...]
    ) -> tuple[AlgorithmCluster, ...]: ...


class DefinitionPort(Protocol):
    version: str

    def generate(
        self, cluster: AlgorithmCluster, reference_skill_sets: list[set[str]]
    ) -> GeneratedDefinition: ...


class GerminationPort(Protocol):
    def assess(
        self,
        *,
        sample_count: int,
        effective_sample_count: int,
        sources: list[str],
        enterprises: list[str | None],
        spread_labels: list[str],
        publish_dates: list[date],
        all_publish_dates: list[date],
        candidate_skills: set[str],
        reference_skill_sets: list[set[str]],
        stability_score: float,
        config: DiscoveryConfig,
        window_ids: list[str],
        all_window_ids: list[str],
        evidence_quality: JsonObject,
        required_window_ids: list[str],
    ) -> GerminationAssessmentResult: ...


class LineagePort(Protocol):
    def match(
        self,
        previous: list[ClusterLineageSpec],
        current: list[ClusterLineageSpec],
    ) -> list[LineageRelation]: ...


class DiscoveryAlgorithm(Protocol):
    def execute(
        self,
        *,
        algorithm: AlgorithmSelection,
        snapshots: tuple[JDSnapshot, ...],
        reference_skill_sets: list[set[str]],
        config: DiscoveryConfig,
        time_window_ids: list[str] | None = None,
    ) -> AlgorithmOutput: ...


class CandidateLifecyclePort(Protocol):
    def execute(
        self,
        *,
        run_id: str,
        window_ids: tuple[str, ...],
        clusters: list[ClusterAggregate],
        snapshot_records: list[SnapshotRecord],
        config: DiscoveryConfig,
        historical_backfill: bool = False,
    ) -> None: ...


class SemanticProviderStatusPort(Protocol):
    version: str
    available: bool


class AlgorithmRegistryPort(Protocol):
    semantic_provider: SemanticProviderStatusPort

    def names(self) -> tuple[str, ...]: ...

    def evaluate(
        self,
        algorithm: str,
        snapshots: tuple[JDSnapshot, ...],
        parameters: JsonObject,
    ) -> AlgorithmEvaluationResult: ...


class DiscoveryUnitOfWork(Protocol):
    runs: RunRepository
    snapshots: SnapshotRepository
    clusters: ClusterRepository
    candidates: CandidateRepository

    def __enter__(self) -> "DiscoveryUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
