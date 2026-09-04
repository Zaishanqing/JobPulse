from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.evaluation import get_evaluation_use_cases
from app.api.task_mapping import task_data
from app.api.evaluation_mapping import evaluation_report_data
from app.contexts.evaluation import (
    EvaluationDatasetNotFound,
    EvaluationReportNotFound,
    ManageEvaluation,
)
from app.contexts.tasks import TaskNotFound
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.evaluation import EvaluationRuleViolation
from app.contexts.evaluation import EvaluationDatasetRecord
from app.schemas.evaluation import EvaluationDatasetCreate, EvaluationRunRequest
from app.schemas.api_requests import ClusterEvaluationRequest
from app.domain.errors import PermissionDenied


router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, (EvaluationDatasetNotFound, EvaluationReportNotFound, TaskNotFound)):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, EvaluationRuleViolation):
        code = 400
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _dataset(item: EvaluationDatasetRecord) -> dict[str, object]:
    return {
        "dataset_id": item.dataset_id,
        "dataset_type": item.dataset_type,
        "name": item.name,
        "description": item.description,
        "payload": dict(item.payload),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _create_dataset(
    dataset_type: str,
    payload: EvaluationDatasetCreate,
    actor: AccountActor,
    use_cases: ManageEvaluation,
):
    try:
        item = use_cases.create_dataset(
            actor, dataset_type, payload.name, payload.description, payload.payload
        )
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=_dataset(item))


@router.post("/datasets/jd")
def create_jd_dataset(
    payload: EvaluationDatasetCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _create_dataset("jd", payload, actor, use_cases)


@router.post("/datasets/resume")
def create_resume_dataset(
    payload: EvaluationDatasetCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _create_dataset("resume", payload, actor, use_cases)


@router.post("/datasets/match")
def create_match_dataset(
    payload: EvaluationDatasetCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _create_dataset("match", payload, actor, use_cases)


@router.get("/datasets")
def get_evaluation_datasets(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        items = use_cases.list_datasets(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=[_dataset(item) for item in items])


@router.get("/datasets/{dataset_id}")
def get_evaluation_dataset_detail(
    dataset_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        item = use_cases.get_dataset(actor, dataset_id)
    except (PermissionDenied, EvaluationDatasetNotFound) as exc:
        _raise(exc)
    return success_response(data=_dataset(item))


@router.delete("/datasets/{dataset_id}")
def delete_evaluation_dataset(
    dataset_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        use_cases.delete_dataset(actor, dataset_id)
    except (PermissionDenied, EvaluationDatasetNotFound) as exc:
        _raise(exc)
    return success_response(data={"dataset_id": dataset_id, "deleted": True})


def _run(
    report_type: str,
    payload: EvaluationRunRequest | None,
    actor: AccountActor,
    use_cases: ManageEvaluation,
):
    try:
        report = use_cases.run(actor, report_type, payload.dataset_id if payload else None)
    except (PermissionDenied, EvaluationDatasetNotFound, EvaluationRuleViolation) as exc:
        _raise(exc)
    return success_response(data=evaluation_report_data(report))


@router.post("/jd-parse/run")
def run_jd_parse_evaluation(
    payload: EvaluationRunRequest | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _run("jd_parse", payload, actor, use_cases)


@router.post("/resume-parse/run")
def run_resume_parse_evaluation(
    payload: EvaluationRunRequest | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _run("resume_parse", payload, actor, use_cases)


@router.post("/match/run")
def run_match_evaluation(
    payload: EvaluationRunRequest | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _run("match", payload, actor, use_cases)


@router.post("/skill-normalization/run")
def run_skill_normalization_evaluation(
    payload: EvaluationRunRequest | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    return _run("skill_normalization", payload, actor, use_cases)


@router.post("/cluster/run")
def run_cluster_evaluation(
    payload: ClusterEvaluationRequest = Body(default_factory=lambda: ClusterEvaluationRequest({})),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        task = use_cases.run_cluster(actor, payload.root)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=task_data(task))


@router.get("/tasks/{task_id}")
def get_evaluation_task(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        task = use_cases.task(actor, task_id)
    except (PermissionDenied, TaskNotFound) as exc:
        _raise(exc)
    return success_response(data=task_data(task))


@router.get("/reports/{report_id}")
def get_evaluation_report_detail(
    report_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        report = use_cases.get_report(actor, report_id)
    except (PermissionDenied, EvaluationReportNotFound) as exc:
        _raise(exc)
    return success_response(data=evaluation_report_data(report))


@router.get("/reports/{report_id}/export")
def export_evaluation_report(
    report_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEvaluation = Depends(get_evaluation_use_cases),
):
    try:
        report = use_cases.get_report(actor, report_id)
    except (PermissionDenied, EvaluationReportNotFound) as exc:
        _raise(exc)
    return success_response(
        data={
            "format": "json",
            "report": evaluation_report_data(report),
            "implementation_status": "database_report_json_export",
        }
    )
