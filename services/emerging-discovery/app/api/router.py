from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.contracts import (
    AlgorithmComparisonEnvelope,
    AlgorithmComparisonRequest,
    CleanupRunRequest,
    DiscoveryRunEnvelope,
    DiscoveryRunRequest,
    FixedCompetitionEvaluationRequest,
    EmergingConclusionRecomputeRequest,
    ResolveAmbiguousIdentityRequest,
    success,
)
from app.api.dependencies import get_handlers, require_internal_service
from app.api.mapping import (
    algorithm_comparison_data,
    discovery_command_from_api,
    discovery_result_data,
    lifecycle_survival_data,
    promotion_distance_data,
)
from app.application.discovery import CONTRACT_VERSION
from app.api.competition_evaluation import (
    evaluate_fixed_dataset,
    load_fixed_dataset,
)
from app.application.handlers import DiscoveryHandlers
from app.application.ambiguous_identity_review import ResolveAmbiguousIdentityCommand
from app.application.recompute import EmergingRecomputeRequest, EmergingTargetAnchor
from dataclasses import asdict
from app.domain.values import FrozenDict, freeze, thaw


router = APIRouter()


@router.get("/health")
def health():
    return success({"status": "healthy", "service": "emerging-discovery"})


@router.get("/readiness")
def readiness(handlers: DiscoveryHandlers = Depends(get_handlers)):
    handlers.check_readiness()
    return success({"status": "ready", "service": "emerging-discovery"})


@router.post(
    "/api/v1/discovery-runs",
    status_code=201,
    response_model=DiscoveryRunEnvelope,
    dependencies=[Depends(require_internal_service)],
)
def create_discovery_run(
    payload: DiscoveryRunRequest,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    command = discovery_command_from_api(
        contract_version=CONTRACT_VERSION,
        request_id=payload.request_id,
        algorithm=payload.algorithm,
        time_windows=[item.model_dump(mode="json") for item in payload.time_windows],
        snapshots=[item.model_dump(mode="json") for item in payload.snapshots],
        position_references=[item.model_dump(mode="json") for item in payload.position_references],
        config=payload.config,
        current_observation_window_id=payload.current_observation_window_id,
    )
    data = discovery_result_data(handlers.create(command))
    return success(data)


@router.post(
    "/api/v1/discovery-conclusion-recomputations",
    dependencies=[Depends(require_internal_service)],
)
def recompute_discovery_conclusion(
    payload: EmergingConclusionRecomputeRequest,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    command = discovery_command_from_api(
        contract_version=CONTRACT_VERSION,
        request_id=payload.request_id,
        algorithm=payload.algorithm,
        time_windows=[item.model_dump(mode="json") for item in payload.time_windows],
        snapshots=[item.model_dump(mode="json") for item in payload.snapshots],
        position_references=[item.model_dump(mode="json") for item in payload.position_references],
        config=payload.config,
        current_observation_window_id=payload.current_observation_window_id,
    )
    anchor = (
        EmergingTargetAnchor(
            anchor_id=payload.target_anchor.anchor_id,
            titles=tuple(payload.target_anchor.titles),
            skills=tuple(payload.target_anchor.skills),
            responsibilities=tuple(payload.target_anchor.responsibilities),
            member_jd_ids=tuple(payload.target_anchor.member_jd_ids),
            member_evidence_ids=tuple(payload.target_anchor.member_evidence_ids),
            member_template_cluster_ids=tuple(payload.target_anchor.member_template_cluster_ids),
            semantic_centroid=tuple(payload.target_anchor.semantic_centroid),
        )
        if payload.target_anchor is not None
        else None
    )
    result = handlers.recompute_conclusion(
        EmergingRecomputeRequest(
            dataset_id=payload.dataset_id,
            release_id=payload.release_id,
            subject_ref=payload.subject_ref,
            algorithm_version=payload.algorithm_version,
            config_hash=payload.config_hash,
            command=command,
            target_anchor=anchor,
        )
    )
    return success(asdict(result))


@router.post(
    "/api/v1/discovery-comparisons",
    response_model=AlgorithmComparisonEnvelope,
    dependencies=[Depends(require_internal_service)],
)
def compare_discovery_algorithms(
    payload: AlgorithmComparisonRequest,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    command = discovery_command_from_api(
        contract_version=CONTRACT_VERSION,
        request_id=payload.request_id,
        algorithm=payload.algorithm,
        time_windows=[item.model_dump(mode="json") for item in payload.time_windows],
        snapshots=[item.model_dump(mode="json") for item in payload.snapshots],
        position_references=[item.model_dump(mode="json") for item in payload.position_references],
        config=payload.config,
        current_observation_window_id=payload.current_observation_window_id,
    )
    configs = freeze(payload.algorithm_configs)
    if not isinstance(configs, FrozenDict):
        raise ValueError("algorithm_configs must be an object")
    result = handlers.compare(
        command,
        tuple(payload.comparison_algorithms),
        configs,
    )
    return success(algorithm_comparison_data(result))


@router.post(
    "/api/v1/discovery-evaluations",
    response_model=AlgorithmComparisonEnvelope,
    dependencies=[Depends(require_internal_service)],
)
def evaluate_discovery_algorithms(
    payload: FixedCompetitionEvaluationRequest,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    service_root = Path(__file__).resolve().parents[2]
    dataset = load_fixed_dataset(
        service_root / "evaluation" / f"{payload.dataset_version}.json"
    )
    return success(evaluate_fixed_dataset(dataset, handlers.comparison.registry))


@router.get(
    "/api/v1/discovery-runs/by-request-id/{request_id}",
    response_model=DiscoveryRunEnvelope,
    dependencies=[Depends(require_internal_service)],
)
def get_discovery_run_by_request_id(
    request_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)
):
    try:
        result = handlers.query.by_request_id(request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(discovery_result_data(result))


@router.get(
    "/api/v1/discovery-runs/{run_id}",
    response_model=DiscoveryRunEnvelope,
    dependencies=[Depends(require_internal_service)],
)
def get_discovery_run(run_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)):
    try:
        result = handlers.query.by_run_id(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(discovery_result_data(result))


@router.get(
    "/api/v1/discovery-runs/{run_id}/lineage-graph",
    dependencies=[Depends(require_internal_service)],
)
def get_run_lineage_graph(run_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)):
    try:
        return success(thaw(handlers.query.lineage_graph(run_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/clusters/{cluster_id}/trajectory",
    dependencies=[Depends(require_internal_service)],
)
def get_cluster_trajectory(cluster_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)):
    try:
        return success(thaw(handlers.query.trajectory(cluster_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/clusters/{cluster_id}/memberships",
    dependencies=[Depends(require_internal_service)],
)
def get_cluster_memberships(
    cluster_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)
):
    try:
        return success(thaw(handlers.query.memberships(cluster_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/candidates",
    dependencies=[Depends(require_internal_service)],
)
def list_candidates(
    status: str | None = None,
    candidate_id: str | None = None,
    window_id: str | None = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    return success(
        thaw(
            handlers.query.candidates(
                status=status,
                candidate_id=candidate_id,
                window_id=window_id,
            )
        )
    )


@router.get(
    "/api/v1/candidates/{candidate_id}",
    dependencies=[Depends(require_internal_service)],
)
def get_candidate(
    candidate_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)
):
    try:
        return success(thaw(handlers.query.candidate_detail(candidate_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/candidates/{candidate_id}/trajectory",
    dependencies=[Depends(require_internal_service)],
)
def get_candidate_trajectory(
    candidate_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)
):
    try:
        return success(thaw(handlers.query.candidate_trajectory(candidate_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/candidates/{candidate_id}/diffusion-graph",
    dependencies=[Depends(require_internal_service)],
)
def get_candidate_diffusion_graph(
    candidate_id: str, handlers: DiscoveryHandlers = Depends(get_handlers)
):
    try:
        return success(thaw(handlers.query.candidate_diffusion(candidate_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/review-exports/ambiguous-identity-pairs",
    dependencies=[Depends(require_internal_service)],
)
def export_ambiguous_identity_pairs(
    observation_id: str | None = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    try:
        return success(
            thaw(
                handlers.query.ambiguous_identity_evidence(
                    observation_id=observation_id
                )
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/v1/candidates/{candidate_id}/identity-resolution",
    dependencies=[Depends(require_internal_service)],
)
def resolve_ambiguous_identity(
    candidate_id: str,
    payload: ResolveAmbiguousIdentityRequest,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    command = ResolveAmbiguousIdentityCommand(
        provisional_candidate_id=candidate_id,
        resolution=payload.resolution,
        target_candidate_id=payload.target_candidate_id,
        reviewer=payload.reviewer,
        reason=payload.reason,
        expected_version=payload.expected_version,
        idempotency_key=payload.idempotency_key,
    )
    try:
        result = handlers.resolve_ambiguous_identity(command)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(thaw(result))


@router.get(
    "/api/v1/evaluations/lifecycle-survival",
    dependencies=[Depends(require_internal_service)],
)
def get_lifecycle_survival(
    candidate_id: str | None = None,
    event_type: str | None = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    try:
        results = handlers.query.lifecycle_survival(
            candidate_id=candidate_id,
            event_type=event_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success(
        {
            "results": [lifecycle_survival_data(item) for item in results],
            "filters": {
                "candidate_id": candidate_id,
                "event_type": event_type,
            },
        }
    )


@router.get(
    "/api/v1/evaluations/promotion-distance",
    dependencies=[Depends(require_internal_service)],
)
def get_promotion_distance(
    candidate_id: str | None = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    try:
        certificates = handlers.query.promotion_distance(candidate_id=candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(
        {
            "certificates": [
                promotion_distance_data(item) for item in certificates
            ],
            "filters": {"candidate_id": candidate_id},
        }
    )


@router.delete(
    "/api/v1/admin/discovery-runs/{run_id}",
    dependencies=[Depends(require_internal_service)],
)
def purge_discovery_run(
    run_id: str,
    payload: CleanupRunRequest,
    maintenance_token: Annotated[str | None, Header(alias="X-Maintenance-Token")] = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
):
    try:
        audit = handlers.purge_run(
            run_id,
            actor=payload.actor,
            reason=payload.reason,
            supplied_token=maintenance_token or "",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(
        {
            "audit_id": audit.audit_id,
            "run_id": audit.run_id,
            "action": audit.action,
            "status": audit.status,
            "actor": audit.actor,
            "reason": audit.reason,
            "completed_at": audit.completed_at,
        }
    )
