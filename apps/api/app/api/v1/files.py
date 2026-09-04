from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.files import get_file_use_cases
from app.contexts.platform import FileNotFound, ManageFiles
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.files import FileRuleViolation
from app.contexts.platform import FileRecord
from app.domain.errors import PermissionDenied
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings


router = APIRouter(prefix="/files", tags=["files"])


def _data(item: FileRecord) -> dict:
    key = PurePath(item.storage_key).name
    return {
        "file_id": item.file_id, "owner_user_id": item.owner_user_id,
        "filename": item.filename, "content_type": item.content_type,
        "path": key, "storage_key": key, "size": item.size,
        "purpose": item.purpose,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, FileNotFound):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, UploadSizeLimitExceeded):
        code = 413
    elif "type" in str(exc):
        code = 415
    elif "size" in str(exc):
        code = 413
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), purpose: str | None = None, actor: AccountActor = Depends(get_account_actor), use_cases: ManageFiles = Depends(get_file_use_cases)):
    try:
        item = use_cases.upload(
            actor, filename=file.filename or "", content_type=file.content_type,
            content=await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES),
            purpose=purpose,
        )
    except (FileRuleViolation, UploadSizeLimitExceeded) as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.get("/{file_id}")
def get_file_detail(file_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageFiles = Depends(get_file_use_cases)):
    try:
        item = use_cases.get(actor, file_id)
    except (FileNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.get("/{file_id}/preview")
def preview_file(file_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageFiles = Depends(get_file_use_cases)):
    try:
        item, content = use_cases.read(actor, file_id)
    except (FileNotFound, PermissionDenied) as exc:
        _raise(exc)
    # HTTP 头只支持 latin-1，中文文件名需按 RFC 5987 编码并附带 ASCII 回退名。
    filename = PurePath(item.filename).name
    if filename.isascii():
        disposition = f'inline; filename="{filename}"'
    else:
        fallback = filename.encode("ascii", "ignore").decode().replace('"', "_") or "file"
        disposition = f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=item.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.delete("/{file_id}")
def delete_file_detail(file_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageFiles = Depends(get_file_use_cases)):
    try:
        use_cases.delete(actor, file_id)
    except (FileNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data={"file_id": file_id, "deleted": True})
