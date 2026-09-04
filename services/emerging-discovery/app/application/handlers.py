from dataclasses import dataclass
from collections.abc import Callable

from app.application.ambiguous_identity_review import (
    ExportAmbiguousIdentityEvidence,
    ResolveAmbiguousIdentity,
    ResolveAmbiguousIdentityCommand,
)
from app.application.candidate_diffusion import QueryCandidateDiffusion
from app.application.discovery import RunDiscovery, get_discovery_result
from app.application.comparison import CompareAlgorithms
from app.application.contracts import AlgorithmComparisonResult
from app.application.lifecycle_survival import EvaluateLifecycleSurvival, LifecycleSurvivalResult
from app.application.offline_evaluation import EvaluateAlgorithmsOffline
from app.application.promotion_distance import (
    EvaluatePromotionDistance,
    PromotionDistanceCertificate,
)
from app.application.contracts import DiscoveryResult, RunDiscoveryCommand
from app.application.maintenance import PurgeDiscoveryRun
from app.ports.maintenance import MaintenanceAuditRecord
from app.ports.providers import DiscoveryUnitOfWork
from app.domain.values import JsonObject, freeze
from app.application.recompute import (
    EmergingRecomputeRequest,
    EmergingRecomputeResult,
    RecomputeEmergingConclusion,
)


@dataclass(frozen=True)
class InternalServiceAuthenticator:
    expected_bearer: str

    def verify(self, authorization: str | None) -> bool:
        import hmac

        return authorization is not None and hmac.compare_digest(
            authorization, self.expected_bearer
        )


@dataclass(frozen=True)
class QueryDiscovery:
    uow: DiscoveryUnitOfWork

    def by_run_id(self, run_id: str) -> DiscoveryResult:
        return get_discovery_result(self.uow, run_id=run_id)

    def by_request_id(self, request_id: str) -> DiscoveryResult:
        return get_discovery_result(self.uow, request_id=request_id)

    def lineage_graph(self, run_id: str) -> JsonObject:
        with self.uow:
            return self.uow.clusters.lineage_graph(run_id)

    def trajectory(self, cluster_id: str) -> JsonObject:
        with self.uow:
            return self.uow.clusters.trajectory(cluster_id)

    def memberships(self, cluster_id: str) -> JsonObject:
        with self.uow:
            return self.uow.clusters.memberships(cluster_id)

    def candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> JsonObject:
        with self.uow:
            return self.uow.candidates.list_candidates(
                status=status,
                candidate_id=candidate_id,
                window_id=window_id,
            )

    def candidate_detail(self, candidate_id: str) -> JsonObject:
        with self.uow:
            return self.uow.candidates.detail(candidate_id)

    def candidate_trajectory(self, candidate_id: str) -> JsonObject:
        with self.uow:
            return self.uow.candidates.trajectory(candidate_id)

    def lifecycle_survival(
        self,
        *,
        candidate_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[LifecycleSurvivalResult, ...]:
        return EvaluateLifecycleSurvival(self.uow).execute(
            candidate_id=candidate_id,
            event_type=event_type,
        )

    def promotion_distance(
        self,
        *,
        candidate_id: str | None = None,
    ) -> tuple[PromotionDistanceCertificate, ...]:
        return EvaluatePromotionDistance(self.uow).execute(candidate_id=candidate_id)

    def ambiguous_identity_evidence(
        self, *, observation_id: str | None = None
    ) -> JsonObject:
        return ExportAmbiguousIdentityEvidence(self.uow).execute(
            observation_id=observation_id
        )

    def resolve_ambiguous_identity(
        self, command: ResolveAmbiguousIdentityCommand
    ) -> JsonObject:
        audit = ResolveAmbiguousIdentity(self.uow).execute(command)
        return freeze(
            {
                "resolution_id": audit.resolution_id,
                "provisional_candidate_id": audit.provisional_candidate_id,
                "target_candidate_id": audit.target_candidate_id,
                "decision": audit.decision,
                "reviewer": audit.reviewer,
                "reason": audit.reason,
                "window_id": audit.window_id,
                "timestamp": audit.timestamp,
                "algorithm_version": audit.algorithm_version,
                "idempotency_key": audit.idempotency_key,
            }
        )

    def candidate_diffusion(self, candidate_id: str) -> JsonObject:
        return QueryCandidateDiffusion(self.uow).execute(candidate_id)


@dataclass(frozen=True)
class DiscoveryHandlers:
    run: RunDiscovery
    query: QueryDiscovery
    purge: PurgeDiscoveryRun
    authenticator: InternalServiceAuthenticator
    readiness: Callable[[], None]
    comparison: CompareAlgorithms
    offline_evaluation: EvaluateAlgorithmsOffline
    recompute: RecomputeEmergingConclusion

    def create(self, command: RunDiscoveryCommand) -> DiscoveryResult:
        return self.run.execute(command)

    def check_readiness(self) -> None:
        self.readiness()

    def compare(
        self,
        command: RunDiscoveryCommand,
        algorithms: tuple[str, ...],
        algorithm_configs,
    ) -> AlgorithmComparisonResult:
        return self.comparison.execute(command, algorithms, algorithm_configs)

    def purge_run(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        supplied_token: str,
    ) -> MaintenanceAuditRecord:
        return self.purge.execute(
            run_id,
            actor=actor,
            reason=reason,
            supplied_token=supplied_token,
        )

    def recompute_conclusion(
        self, request: EmergingRecomputeRequest
    ) -> EmergingRecomputeResult:
        return self.recompute.execute(request)

    def resolve_ambiguous_identity(
        self, command: ResolveAmbiguousIdentityCommand
    ) -> JsonObject:
        return self.query.resolve_ambiguous_identity(command)
