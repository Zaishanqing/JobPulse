from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.contexts.jd_lifecycle import (
    JDApplicationError,
    JDFileCreateCommand,
    JDParseCommand,
    JDTextCreateCommand,
    JDUseCases,
)
from app.core.response import success_response
from app.api.jd_mapping import map_jd_output
from app.api.jd_error_mapping import jd_http_exception
from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.use_cases import get_jd_use_cases
from app.contexts.jd_lifecycle import Actor, FileUpload
from app.contexts.access import AccountRecord
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings
from app.schemas.jd import (
    JDBatchCreateRequest,
    JDParseBatchRequest,
    JDParseRequest,
    JDParseResultUpdate,
    JDRawTextUpdate,
    JDTextCreate,
)

router = APIRouter(prefix="/jds", tags=["jds"])


def _success(value):
    return success_response(data=map_jd_output(value))


def _actor(current_user: AccountRecord) -> Actor:
    return Actor(id=current_user.account_id, role=current_user.role)


def _text_command(payload: JDTextCreate) -> JDTextCreateCommand:
    values = payload.model_dump()
    return JDTextCreateCommand(**values)


def _parse_command(payload: JDParseRequest) -> JDParseCommand:
    values = payload.model_dump(mode="json")
    return JDParseCommand(**values)


def _raise_http(exc: JDApplicationError) -> None:
    raise jd_http_exception(exc)


@router.post("/text")
def create_text_jd(
    payload: JDTextCreate,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.create_text(_actor(current_user), _text_command(payload)))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/file")
async def create_file_jd(
    file: UploadFile = File(...),
    title: str = Form(...),
    source_type: str = Form("enterprise_upload"),
    source_name: str = Form("file_upload"),
    enterprise_id: str | None = Form(None),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        upload = FileUpload(
            filename=file.filename or "",
            content_type=file.content_type,
            content=await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES),
        )
        return _success(
            use_cases.create_file(
                _actor(current_user),
                JDFileCreateCommand(
                    upload=upload,
                    title=title,
                    source_type=source_type,
                    source_name=source_name or file.filename,
                    enterprise_id=enterprise_id,
                    use_ocr=False,
                ),
            )
        )
    except UploadSizeLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/batch")
def create_jd_batch(
    payload: JDBatchCreateRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(
            use_cases.create_batch(
                _actor(current_user), [_text_command(item) for item in payload.root]
            )
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/parse-batch")
def parse_jd_batch(
    payload: JDParseBatchRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(
            use_cases.parse_batch(
                _actor(current_user), payload.jd_ids, payload.extraction_mode
            )
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("")
def get_jds(
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.list_jds(_actor(current_user)))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/summary")
def get_jd_summary(
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return success_response(data=use_cases.summarize_jds(_actor(current_user)))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/page")
def get_jd_page(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None, max_length=200),
    sort: str = Query(
        default="created_desc",
        pattern="^(created_desc|created_asc|title_asc)$",
    ),
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        items, total = use_cases.list_jds_page(
            _actor(current_user),
            offset=offset,
            limit=limit,
            query=query,
            sort=sort,
        )
        return success_response(
            data={
                "items": map_jd_output(items),
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}")
def get_jd_detail(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.get_jd(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.put("/{jd_id}/raw")
def edit_jd_raw_text(
    jd_id: str,
    payload: JDRawTextUpdate,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(
            use_cases.update_raw_text(_actor(current_user), jd_id, payload.raw_text)
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.delete("/{jd_id}")
def delete_jd_detail(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        use_cases.delete_jd(_actor(current_user), jd_id)
        return _success({"jd_id": jd_id, "deleted": True})
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/deprecate")
def deprecate_jd_detail(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        use_cases.deprecate_jd(_actor(current_user), jd_id)
        return success_response(data={"jd_id": jd_id, "deprecated": True})
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/parse")
def parse_jd(
    jd_id: str,
    payload: JDParseRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(
            use_cases.parse(_actor(current_user), jd_id, _parse_command(payload))
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}/parse-result")
def get_jd_parse_result(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.get_parse_result(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.put("/{jd_id}/parse-result")
def edit_jd_parse_result(
    jd_id: str,
    payload: JDParseResultUpdate,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(
            use_cases.update_parse_result(
                _actor(current_user), jd_id, payload.to_command()
            )
        )
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/parse-result/confirm")
def confirm_jd_parse_result(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.confirm_parse_result(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/parse-result/publish")
def publish_jd_parse_result(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.publish_parse_result(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}/parse-result/export")
def export_jd_parse_result(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.export_parse_result(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/duplicate-check")
def check_jd_duplicate(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.duplicate_check(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}/similar")
def get_jd_similar(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.similar_jds(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}/copy-risk")
def get_jd_copy_risk(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.copy_risk_report(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.put("/{jd_id}/downweight")
def downweight_jd_detail(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.downweight_jd(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.post("/{jd_id}/inflation-check")
def check_jd_inflation(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.inflation_check(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)


@router.get("/{jd_id}/inflation-report")
def get_jd_inflation_report(
    jd_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        return _success(use_cases.inflation_report(_actor(current_user), jd_id))
    except JDApplicationError as exc:
        _raise_http(exc)
