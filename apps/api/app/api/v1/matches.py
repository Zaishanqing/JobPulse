from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.matching import get_matching_use_cases
from app.api.evaluation_data import evaluation_reference_data, evaluation_report_data
from app.api.matching_bff_mapping import (
    canonical_status,
    evidence_deletion_data,
    match_versions_response,
    what_if_data,
)
from app.contexts.matching_learning import (
    ManageMatching,
    MatchingEvaluationNotFound,
)
from app.contexts.matching_learning.contracts_service import (
    StandardPositionProfileInsufficient,
)
from app.contexts.tasks import TaskNotFound, TaskRecord
from app.core.response import success_response
from app.domain.values import thaw
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.matching import MatchingRuleViolation
from app.contexts.matching_learning.matching_service import (
    MatchingServiceError,
    RemoteEvaluation,
    RemoteTask,
)
from app.schemas.match import MatchRankingCreate, MatchTaskCreate
from app.schemas.matching_bff import (
    EligibleResumeListEnvelope,
    EvidenceDeletionCreate,
    EvidenceDeletionEnvelope,
    MatchReportEnvelope,
    MatchReportExportEnvelope,
    MatchReportListEnvelope,
    WhatIfCreate,
    WhatIfEnvelope,
    MatchTaskEnvelope,
    MatchPreflightEnvelope,
    MatchPositionListEnvelope,
    MatchRankingEnvelope,
)

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("/positions", response_model=MatchPositionListEnvelope)
def match_positions(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    return success_response(
        data=[
            {
                "position_id": item.position_id,
                "position_name": item.position_name,
                "taxonomy_family_name": item.taxonomy_family_name,
                "status": item.status,
                "lifecycle_status": item.lifecycle_status,
                "matchable": item.matchable,
                "reason": item.reason,
                "blockers": list(item.blockers),
                "position_graph_version": item.position_graph_version,
                "position_profile_version": item.position_profile_version,
            }
            for item in use_cases.matchable_positions(actor)
        ]
    )


@router.get("/preflight", response_model=MatchPreflightEnvelope)
def match_preflight(
    resume_id: str,
    position_id: str,
    target_type: str = "standard_position",
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        result = use_cases.preflight(
            actor,
            resume_id=resume_id,
            position_id=position_id,
            target_type=target_type,
        )
    except (MatchingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=result)


@router.get("/eligible-resumes", response_model=EligibleResumeListEnvelope)
def eligible_match_resumes(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    return success_response(
        data=[
            {
                "resume_id": item.resume_id,
                "validated_cv_snapshot_id": item.validated_cv_snapshot_id,
                "skill_count": item.skill_count,
                "project_count": item.project_count,
            }
            for item in use_cases.eligible_resumes(actor)
        ]
    )


@router.get("/rankings", response_model=MatchRankingEnvelope)
def get_match_ranking(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        result = use_cases.ranking(actor, resume_id=resume_id)
    except (MatchingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=result)


@router.post("/rankings", response_model=MatchRankingEnvelope)
def start_match_ranking(
    payload: MatchRankingCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        result = use_cases.ranking(actor, resume_id=payload.resume_id)
    except (MatchingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    # The explicit POST is the only action that resumes a cancelled or idle
    # ranking. GET remains read-only so revisiting the page never starts work.
    # Failed rows are terminal for progress display but must stay retryable:
    # an explicit start resubmits only the failed candidates.
    has_failures = any(
        item.get("calculation_status") == "failed" for item in result["items"]
    )
    if result["status"] != "completed" or has_failures:
        try:
            use_cases.prepare_ranking(actor, resume_id=payload.resume_id)
        except (MatchingRuleViolation, PermissionDenied) as exc:
            _raise(exc)
        background_tasks.add_task(
            use_cases.run_ranking,
            actor,
            resume_id=payload.resume_id,
            correlation_id=request.state.trace_id,
            concurrency=4,
        )
        result = {**result, "status": "running"}
    return success_response(data=result)


@router.post("/rankings/cancel", response_model=MatchRankingEnvelope)
def cancel_match_ranking(
    payload: MatchRankingCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        use_cases.cancel_ranking(actor, resume_id=payload.resume_id)
        result = use_cases.ranking(actor, resume_id=payload.resume_id)
    except (MatchingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=result)


def _remote_task(item: RemoteTask) -> dict[str, object]:
    result = {"evaluation_id": item.evaluation_id}
    raw_versions = item.raw.get("versions")
    raw_versions = raw_versions if isinstance(raw_versions, dict) else None
    status = canonical_status(item.status)
    return {
        "task_id": item.task_id,
        "task_type": "match",
        "status": status,
        "canonical_status": status,
        "progress": 100 if status == "succeeded" else (50 if status == "running" else 0),
        "result_payload": result,
        "result_reference": f"matching_evaluation:{item.evaluation_id}" if item.evaluation_id else None,
        "evaluation_id": item.evaluation_id,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "attempt_count": item.attempt,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "execution_mode": item.raw.get("execution_mode"),
        "rule_based": item.raw.get("rule_based"),
        "provider": item.raw.get("provider"),
        "target_type": item.target_type,
        "use_enterprise_weights": item.raw.get("use_enterprise_weights"),
        "generate_learning_path": item.raw.get("generate_learning_path"),
        "versions": match_versions_response(raw_versions),
    }


def _task_record_data(task: TaskRecord) -> dict[str, object]:
    result = thaw(task.result_payload.values)
    result = result if isinstance(result, dict) else {}
    input_payload = thaw(task.input_payload.values)
    input_payload = input_payload if isinstance(input_payload, dict) else {}
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "canonical_status": task.status,
        "progress": task.progress,
        "result_payload": {"evaluation_id": result.get("evaluation_id")},
        "result_reference": task.result_reference,
        "evaluation_id": result.get("evaluation_id"),
        "error_code": task.error_code,
        "error_message": task.error_message,
        "attempt_count": task.attempt_count,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "execution_mode": "synchronous_local",
        "implementation_status": "database_persisted_sync_executor",
        "created_by": task.created_by,
        "logs": [
            {"status": log.status, "at": log.at, "message": log.message}
            for log in task.logs
        ]
        if task.logs
        else None,
        "input_payload": {
            "resume_id": input_payload.get("resume_id"),
            "target_type": input_payload.get("target_type"),
            "target_id": (
                input_payload.get("target_id")
                or input_payload.get("position_id")
            ),
            "use_enterprise_weights": input_payload.get("use_enterprise_weights"),
            "generate_learning_path": input_payload.get("generate_learning_path"),
        },
        "versions": match_versions_response(result.get("versions")),
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, StandardPositionProfileInsufficient):
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": exc.code,
                "message": str(exc),
                "reason_code": exc.reason_code,
            },
        ) from exc
    if isinstance(exc, MatchingServiceError):
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    if isinstance(exc, (MatchingEvaluationNotFound, TaskNotFound)):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/tasks", response_model=MatchTaskEnvelope)
def create_match_task(
    payload: MatchTaskCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        task = use_cases.run(
            actor,
            **payload.model_dump(),
            idempotency_key=idempotency_key or "",
            correlation_id=request.state.trace_id,
        )
    except (MatchingRuleViolation, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(
        data=(
            _remote_task(task)
            if isinstance(task, RemoteTask)
            else _task_record_data(task)
        )
    )


@router.get("/tasks/{task_id}", response_model=MatchTaskEnvelope)
def get_match_task_detail(
    task_id: str,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        task = use_cases.task(actor, task_id, correlation_id=request.state.trace_id)
    except (TaskNotFound, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(
        data=(
            _remote_task(task)
            if isinstance(task, RemoteTask)
            else _task_record_data(task)
        )
    )


@router.post("/tasks/{task_id}/restart", response_model=MatchTaskEnvelope)
def restart_match_task(
    task_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        task = use_cases.abandon_and_restart(
            actor,
            task_id,
            idempotency_key=idempotency_key or "",
            correlation_id=request.state.trace_id,
        )
    except (MatchingRuleViolation, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(data=_remote_task(task))


@router.post("/tasks/{task_id}/abandon", response_model=MatchTaskEnvelope)
def abandon_match_task(
    task_id: str,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        task = use_cases.abandon(
            actor, task_id, correlation_id=request.state.trace_id
        )
    except (PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)
    return success_response(data=_remote_task(task))


def _report(
    evaluation_id: str,
    actor: AccountActor,
    use_cases: ManageMatching,
    correlation_id: str = "",
) -> RemoteEvaluation:
    try:
        return use_cases.get(actor, evaluation_id, correlation_id=correlation_id)
    except (MatchingEvaluationNotFound, PermissionDenied, MatchingServiceError) as exc:
        _raise(exc)


@router.get("/reports/{evaluation_id}", response_model=MatchReportEnvelope)
def get_match_evaluation_detail(
    evaluation_id: str,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    item = _report(evaluation_id, actor, use_cases, request.state.trace_id)
    return success_response(
        data=evaluation_report_data(
            item,
            position_name=use_cases.position_name(item.position_id),
        )
    )


@router.post("/reports/{evaluation_id}/what-if", response_model=WhatIfEnvelope)
def evaluate_match_what_if(
    evaluation_id: str,
    payload: WhatIfCreate,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        result = use_cases.what_if(
            actor,
            evaluation_id,
            actions=tuple(item.model_dump(mode="python") for item in payload.actions),
            correlation_id=request.state.trace_id,
        )
    except (
        MatchingEvaluationNotFound,
        MatchingRuleViolation,
        PermissionDenied,
        MatchingServiceError,
    ) as exc:
        _raise(exc)
    current = _report(evaluation_id, actor, use_cases, request.state.trace_id)
    return success_response(data=what_if_data(result, current))


@router.post(
    "/reports/{evaluation_id}/evidence-deletions",
    response_model=EvidenceDeletionEnvelope,
)
def evaluate_explanation_deletion(
    evaluation_id: str,
    payload: EvidenceDeletionCreate,
    request: Request,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    try:
        current = _report(evaluation_id, actor, use_cases, request.state.trace_id)
        result = use_cases.explanation_deletion(
            actor,
            evaluation_id,
            deletion_kind=payload.deletion_kind,
            evidence_source_ids=tuple(payload.evidence_source_ids),
            correlation_id=request.state.trace_id,
        )
    except (
        MatchingEvaluationNotFound,
        MatchingRuleViolation,
        PermissionDenied,
        MatchingServiceError,
    ) as exc:
        _raise(exc)
    return success_response(data=evidence_deletion_data(result, current))


@router.get("/reports/{evaluation_id}/export", response_model=MatchReportExportEnvelope)
def export_match_evaluation(
    evaluation_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    item = _report(evaluation_id, actor, use_cases)
    return success_response(
        data={"format": "json", "report": evaluation_report_data(item)}
    )


@router.get("/reports", response_model=MatchReportListEnvelope)
def list_match_reports_api(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
):
    return success_response(
        data=[evaluation_reference_data(item) for item in use_cases.list(actor)]
    )
