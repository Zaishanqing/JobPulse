from dataclasses import asdict
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.extraction_tasks import (
    get_extraction_task_use_cases,
    get_extraction_worker_control,
)
from app.contexts.access import AccountRecord
from app.contexts.extraction_tasks import (
    ExtractionDraftNotReady,
    ExtractionDraftRecord,
    ExtractionDraftValidationError,
    ExtractionTaskConflict,
    ExtractionTaskNotFound,
    ExtractionTaskRecord,
    ExtractionTaskRetryRejected,
    ExtractionTaskUseCases,
    ExtractionValidationGateError,
    ExtractionTaskPage,
    RunPendingExtractionTasks,
)
from app.core.response import success_response
from app.domain.json_types import thaw_json_object
from app.domain.permissions import (
    INTEGRATION_JD_RETRY,
    INTEGRATION_STATUS_VIEW,
    INTEGRATION_WORKER_RUN,
    require_permission,
)


router = APIRouter(tags=["extraction-tasks"])


def _data(task: ExtractionTaskRecord) -> dict[str, object]:
    result = asdict(task)
    if task.bundle_payload is not None:
        result["bundle_payload"] = thaw_json_object(task.bundle_payload)
    return result


def _draft_data(draft: ExtractionDraftRecord) -> dict[str, object]:
    return asdict(draft)


def _execute(call: Callable[[], ExtractionTaskRecord]):
    try:
        return success_response(data=_data(call()))
    except ExtractionTaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionTaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionTaskRetryRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _page_data(page: ExtractionTaskPage) -> dict[str, object]:
    return {
        "items": [_data(item) for item in page.items],
        "total": page.total,
        "page": page.page,
        "page_size": page.page_size,
    }


@router.get("/extraction-tasks")
def list_extraction_tasks(
    status: str | None = None,
    source_jd_version_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_STATUS_VIEW)
    try:
        result = use_cases.list_extraction_tasks(
            status=status,
            source_jd_version_id=source_jd_version_id,
            page=page,
            page_size=page_size,
        )
        return success_response(data=_page_data(result))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/extraction-tasks/run-pending")
def run_pending_extraction_tasks(
    limit: int = Query(20, ge=1, le=100),
    current_user: AccountRecord = Depends(get_authenticated_account),
    control: RunPendingExtractionTasks = Depends(get_extraction_worker_control),
):
    require_permission(current_user.role, INTEGRATION_WORKER_RUN)
    return success_response(data={"claimed": control.execute(limit)})


@router.post("/source-jd-versions/{version_id}/extraction-tasks")
def create_extraction_task(
    version_id: str,
    extraction_mode: Literal["llm", "rule"] = Query(...),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_WORKER_RUN)
    return _execute(
        lambda: use_cases.create_extraction_task(version_id, extraction_mode)
    )


@router.post("/extraction-tasks/{task_id}/run")
def run_extraction_task(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_WORKER_RUN)
    return _execute(lambda: use_cases.run_extraction_task(task_id))


@router.post("/extraction-tasks/{task_id}/retry")
def retry_extraction_task(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_JD_RETRY)
    return _execute(lambda: use_cases.retry_extraction_task(task_id))


@router.post("/extraction-tasks/{task_id}/import-draft")
def import_extraction_draft(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_WORKER_RUN)
    try:
        return success_response(
            data=_draft_data(use_cases.import_extraction_bundle(task_id))
        )
    except ExtractionTaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionDraftNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionDraftValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExtractionValidationGateError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc.code}: {exc.safe_message}",
        ) from exc
    except ExtractionTaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/extraction-tasks/{task_id}/draft")
def get_extraction_draft(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_STATUS_VIEW)
    try:
        return success_response(
            data=_draft_data(use_cases.get_imported_draft(task_id))
        )
    except ExtractionTaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/source-jd-versions/{version_id}/drafts")
def list_source_version_drafts(
    version_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_STATUS_VIEW)
    try:
        return success_response(
            data=[
                _draft_data(item)
                for item in use_cases.list_imported_drafts(version_id)
            ]
        )
    except ExtractionTaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/extraction-tasks/{task_id}")
def get_extraction_task(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: ExtractionTaskUseCases = Depends(get_extraction_task_use_cases),
):
    require_permission(current_user.role, INTEGRATION_STATUS_VIEW)
    return _execute(lambda: use_cases.get_extraction_task(task_id))
