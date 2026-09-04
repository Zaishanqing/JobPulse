from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.ocr import get_ocr_use_cases
from app.api.task_mapping import task_data
from app.api.ocr_mapping import ocr_result_data
from app.contexts.platform import ManageOCR, OCRResultNotFound
from app.contexts.tasks import TaskNotFound
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.schemas.api_requests import OCRResultUpdateRequest
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings


router = APIRouter(prefix="/ocr", tags=["ocr"])


async def _run(
    source_type: str,
    file: UploadFile | None,
    actor: AccountActor,
    use_cases: ManageOCR,
    default_media_type: str,
):
    try:
        content = await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES) if file else b""
        result, task = use_cases.run(
            actor,
            source_type,
            file.filename if file else None,
            content,
            file.content_type if file and file.content_type else default_media_type,
        )
    except UploadSizeLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return success_response(data=ocr_result_data(result, task.task_id))


@router.post("/image")
async def ocr_image(
    file: UploadFile | None = File(default=None),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageOCR = Depends(get_ocr_use_cases),
):
    return await _run("image", file, actor, use_cases, "application/octet-stream")


@router.post("/pdf")
async def ocr_pdf(
    file: UploadFile | None = File(default=None),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageOCR = Depends(get_ocr_use_cases),
):
    return await _run("pdf", file, actor, use_cases, "application/pdf")


@router.get("/tasks/{task_id}")
def get_ocr_task(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageOCR = Depends(get_ocr_use_cases),
):
    try:
        task = use_cases.task(actor, task_id)
    except (TaskNotFound, PermissionDenied) as exc:
        raise HTTPException(
            status_code=404 if isinstance(exc, TaskNotFound) else 403, detail=str(exc)
        ) from exc
    return success_response(data=task_data(task))


@router.put("/results/{result_id}")
def update_ocr_result(
    result_id: str,
    payload: OCRResultUpdateRequest = Body(default_factory=lambda: OCRResultUpdateRequest(text="")),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageOCR = Depends(get_ocr_use_cases),
):
    try:
        result = use_cases.update(actor, result_id, payload.text)
    except (OCRResultNotFound, PermissionDenied) as exc:
        raise HTTPException(
            status_code=404 if isinstance(exc, OCRResultNotFound) else 403, detail=str(exc)
        ) from exc
    return success_response(data=ocr_result_data(result))
