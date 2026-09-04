from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.matching import get_learning_path_use_cases
from app.api.matching_bff_mapping import (
    algorithm_versions_response,
    data_versions_response,
    enrich_learning_path_gap,
    graph_version_from_versions,
)
from app.contexts.matching_learning import (
    LearningPathNotFound,
    ManageLearningPaths,
    MatchingEvaluationNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.matching import MatchingRuleViolation
from app.schemas.learning_path import LearningPathCreate
from app.schemas.matching_bff import (
    LearningPathEnvelope,
    LearningPathExportEnvelope,
    LearningPathListEnvelope,
)
from app.contexts.matching_learning.matching_service import (
    MatchingServiceError,
    RemoteLearningPath,
)

router = APIRouter(prefix="/learning-paths", tags=["learning-paths"])


def _remote_data(item: RemoteLearningPath) -> dict[str, object]:
    versions = item.versions if isinstance(item.versions, dict) else {}
    gap = enrich_learning_path_gap(
        item.gap_analysis,
        evaluation_id=item.evaluation_id,
        resume_id=item.resume_id or "",
        snapshot_id=item.validated_cv_snapshot_id or "",
        position_id=item.position_id or item.target_position_id or "",
        graph_version=graph_version_from_versions(versions),
        cv_source_version=versions.get("cv_source_version") or "",
        position_source_version=versions.get("position_source_version") or "",
    )
    steps = gap.get("learning_path")
    return {
        "path_id": item.path_id,
        "evaluation_id": item.evaluation_id,
        "target_position_id": item.target_position_id,
        "time_budget_hours": item.time_budget_hours,
        # Display-only goal for the current planner; the planner itself does
        # not receive or optimize this string.
        "learning_goal": "在时间预算内优先解除阻塞并最大化匹配提升",
        "stages": steps if isinstance(steps, list) else [],
        "gap_analysis": gap,
        "status": item.status,
        "provider": item.provider,
        "algorithm_versions": algorithm_versions_response(item.algorithm_versions),
        "data_versions": data_versions_response(item.data_versions),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, MatchingServiceError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc
    status = (
        404
        if isinstance(exc, (LearningPathNotFound, MatchingEvaluationNotFound))
        else (409 if isinstance(exc, MatchingRuleViolation) else 403)
    )
    detail = str(exc)
    raise HTTPException(
        status_code=status,
        detail={
            "error_code": getattr(exc, "code", None) or detail,
            "message": detail,
        },
    ) from exc


@router.post("", response_model=LearningPathEnvelope)
def create_learning_path_api(
    payload: LearningPathCreate,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageLearningPaths = Depends(get_learning_path_use_cases),
):
    try:
        item = use_cases.create(
            actor,
            evaluation_id=payload.evaluation_id,
            target_position_id=payload.target_position_id,
            time_budget_hours=payload.time_budget_hours,
            correlation_id=request.state.trace_id,
        )
    except (
        MatchingEvaluationNotFound,
        MatchingRuleViolation,
        PermissionDenied,
        MatchingServiceError,
    ) as exc:
        _raise(exc)
    return success_response(data=_remote_data(item))


@router.get("", response_model=LearningPathListEnvelope)
def list_learning_paths_api(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageLearningPaths = Depends(get_learning_path_use_cases),
):
    return success_response(data=[_remote_data(item) for item in use_cases.list(actor)])


@router.get("/{path_id}", response_model=LearningPathEnvelope)
def get_learning_path_api(
    path_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageLearningPaths = Depends(get_learning_path_use_cases),
):
    try:
        item = use_cases.get(actor, path_id)
    except (LearningPathNotFound, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(data=_remote_data(item))


@router.get("/{path_id}/export", response_model=LearningPathExportEnvelope)
def export_learning_path(
    path_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageLearningPaths = Depends(get_learning_path_use_cases),
):
    try:
        item = use_cases.get(actor, path_id)
    except (LearningPathNotFound, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(
        data={"format": "json", "learning_path": _remote_data(item)}
    )
