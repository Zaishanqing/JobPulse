from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4

from app.contexts.discovery import (
    Actor,
    PositionClusterNotFound,
    PositionDiscoveryHandlers,
    RunDiscoveryCommand,
)
from app.core.response import success_response
from app.api.discovery_mapping import discovery_data
from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.use_cases import get_position_discovery_handlers
from app.contexts.access import AccountRecord
from app.domain.values import thaw
from app.schemas.cluster import ClusterTaskRequest


router = APIRouter(prefix="/position-clusters", tags=["position-clusters"])


def _actor(user: AccountRecord) -> Actor:
    return Actor(actor_id=user.account_id, role=user.role)


def _not_found(exc: PositionClusterNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post("/tasks")
def create_position_cluster_task(
    payload: ClusterTaskRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    task = handlers.start.execute(
        RunDiscoveryCommand(
            request_id=f"run-{uuid4()}",
            algorithm=payload.algorithm,
            time_window_start=payload.time_window_start,
            time_window_end=payload.time_window_end,
            dataset_id=payload.dataset_id,
            jd_ids=tuple(dict.fromkeys(payload.jd_ids)),
            max_samples=payload.max_samples,
        ),
        _actor(current_user),
    )
    return success_response(data=discovery_data(task))


@router.get("/tasks/{task_id}")
def get_position_cluster_task(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        data = handlers.query.task(task_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(data=discovery_data(data))


@router.get("")
def get_position_clusters(
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    return success_response(data=discovery_data(handlers.query.list(_actor(current_user))))


@router.get("/{cluster_id}")
def get_position_cluster_detail(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        data = handlers.query.get(cluster_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(data=discovery_data(data))


@router.get("/{cluster_id}/jds")
def get_position_cluster_jds(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        data = handlers.query.jds(cluster_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(data=discovery_data(data))


@router.get("/{cluster_id}/core-skills")
def get_position_cluster_core_skills(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        cluster = handlers.query.get(cluster_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(
        data={"cluster_id": cluster_id, "core_skills": thaw(cluster.core_skills)}
    )


@router.get("/{cluster_id}/representatives")
def get_position_cluster_representatives(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        cluster = handlers.query.get(cluster_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(
        data={
            "cluster_id": cluster_id,
            "representative_titles": list(cluster.representative_titles),
            "representative_jd_ids": list(cluster.representative_jd_ids),
        }
    )


@router.delete("/{cluster_id}")
def delete_position_cluster(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    try:
        handlers.delete.execute(cluster_id, _actor(current_user))
    except PositionClusterNotFound as exc:
        raise _not_found(exc) from exc
    return success_response(data={"cluster_id": cluster_id, "deleted": True})
