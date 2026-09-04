from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status

from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.use_cases import get_jd_use_cases
from app.contexts.jd_lifecycle import JDApplicationError, JDFileCreateCommand, JDUseCases
from app.core.response import success_response
from app.api.jd_mapping import map_jd_output
from app.api.jd_error_mapping import jd_http_exception
from app.contexts.access import AccountRecord
from app.contexts.jd_lifecycle import Actor, FileUpload
from app.schemas.task import TaskRecordEnvelope
from app.schemas.api_requests import JDIdBatchRequest, JDSkillAbnormalRequest
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings

router = APIRouter(tags=["reserved-interfaces"])


def _jd_actor(current_user: AccountRecord) -> Actor:
    return Actor(id=current_user.account_id, role=current_user.role)


def _raise_jd_http(exc: JDApplicationError) -> None:
    raise jd_http_exception(exc)


@router.post("/jds/image")
async def create_jd_image(
    file: UploadFile | None = File(default=None),
    title: str = "图片 JD",
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required"
        )
    try:
        return success_response(
            data=map_jd_output(
                use_cases.create_file(
                    _jd_actor(current_user),
                    JDFileCreateCommand(
                        upload=FileUpload(
                            filename=file.filename or "",
                            content_type=file.content_type,
                            content=await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES),
                        ),
                        title=title,
                        source_type="image_upload",
                        source_name=file.filename,
                        enterprise_id=None,
                        use_ocr=True,
                    ),
                )
            )
        )
    except UploadSizeLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except JDApplicationError as exc:
        _raise_jd_http(exc)


@router.get("/jds/parse-tasks/{task_id}", response_model=TaskRecordEnvelope)
def get_jd_parse_task(
    task_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return success_response(
            data=map_jd_output(use_cases.get_parse_task(_jd_actor(current_user), task_id))
        )
    except JDApplicationError as exc:
        _raise_jd_http(exc)


@router.post("/jds/duplicate-check-batch")
def duplicate_check_batch(
    payload: JDIdBatchRequest = Body(default_factory=lambda: JDIdBatchRequest([])),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return success_response(
            data=map_jd_output(
                use_cases.duplicate_check_batch(_jd_actor(current_user), payload.root)
            )
        )
    except JDApplicationError as exc:
        _raise_jd_http(exc)


@router.post("/jds/inflation-check-batch")
def inflation_check_batch(
    payload: JDIdBatchRequest = Body(default_factory=lambda: JDIdBatchRequest([])),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return success_response(
            data=map_jd_output(
                use_cases.inflation_check_batch(_jd_actor(current_user), payload.root)
            )
        )
    except JDApplicationError as exc:
        _raise_jd_http(exc)


@router.put("/jds/{jd_id}/skills/{skill_id}/mark-abnormal")
def mark_jd_skill_abnormal(
    jd_id: str,
    skill_id: str,
    payload: JDSkillAbnormalRequest = Body(default_factory=JDSkillAbnormalRequest),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    abnormal = payload.abnormal
    reason = payload.reason
    try:
        return success_response(
            data=map_jd_output(
                use_cases.mark_parse_skill_abnormal(
                    _jd_actor(current_user),
                    jd_id,
                    skill_id,
                    abnormal=abnormal,
                    reason=reason,
                )
            )
        )
    except JDApplicationError as exc:
        _raise_jd_http(exc)
