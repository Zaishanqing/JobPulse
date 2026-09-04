from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.resumes import get_resume_use_cases
from app.api.resume_mapping import resume_data, resume_parse_data, resume_skill_data
from app.api.task_mapping import task_data
from app.contexts.talent_acquisition import (
    ManageResumes,
    ParseResultNotFound,
    ResumeNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.files import FileRuleViolation
from app.domain.resumes import ResumeRuleViolation
from app.contexts.talent_acquisition import ParseResultChanges
from app.schemas.resume import ResumeParseResultUpdate, ResumeTextCreate, ResumeUpdate
from app.domain.errors import PermissionDenied
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings


router = APIRouter(prefix="/resumes", tags=["resumes"])
SECTION_FIELDS = frozenset(
    {"education", "projects", "internships", "skills", "certificates", "competitions"}
)


def _raise(exc: Exception) -> None:
    if isinstance(exc, (ResumeNotFound, ParseResultNotFound)):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, UploadSizeLimitExceeded):
        code = 413
    elif isinstance(exc, ResumeRuleViolation):
        code = 403 if "personal users" in str(exc) else 409
    elif isinstance(exc, FileRuleViolation) and "type" in str(exc):
        code = 415
    elif isinstance(exc, FileRuleViolation) and "size" in str(exc):
        code = 413
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _changes(payload: ResumeParseResultUpdate) -> ParseResultChanges:
    raw = payload.model_dump(exclude_unset=True)
    values = {
        name: tuple(value) if name in SECTION_FIELDS and value is not None else value
        for name, value in raw.items()
    }
    return ParseResultChanges(frozenset(raw), **values)


@router.post("/text")
def create_text_resume(
    payload: ResumeTextCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        resume = use_cases.create_text(actor, payload.raw_text)
    except ResumeRuleViolation as exc:
        _raise(exc)
    return success_response(data=resume_data(resume))


async def _create_upload(
    file: UploadFile,
    actor: AccountActor,
    use_cases: ManageResumes,
    *,
    use_ocr: bool,
):
    try:
        resume = use_cases.create_upload(
            actor,
            filename=file.filename or "",
            content_type=file.content_type,
            content=await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES),
            use_ocr=use_ocr,
        )
    except (ResumeRuleViolation, FileRuleViolation, UploadSizeLimitExceeded) as exc:
        _raise(exc)
    return success_response(data=resume_data(resume))


@router.post("/file")
async def create_file_resume(
    file: UploadFile = File(...),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    return await _create_upload(file, actor, use_cases, use_ocr=False)


@router.post("/image")
async def create_image_resume(
    file: UploadFile = File(...),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    return await _create_upload(file, actor, use_cases, use_ocr=True)


@router.get("/me")
def get_my_resumes(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        records = use_cases.list_mine(actor)
    except ResumeRuleViolation as exc:
        _raise(exc)
    return success_response(data=[resume_data(item) for item in records])


@router.get("/{resume_id}")
def get_resume_detail(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        record = use_cases.get(actor, resume_id)
    except (ResumeNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=resume_data(record))


@router.patch("/{resume_id}")
def rename_resume_detail(
    resume_id: str,
    payload: ResumeUpdate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        record = use_cases.rename(actor, resume_id, payload.display_name)
    except (ResumeNotFound, ResumeRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=resume_data(record))


@router.delete("/{resume_id}")
def delete_resume_detail(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        use_cases.delete(actor, resume_id)
    except (ResumeNotFound, ResumeRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data={"resume_id": resume_id, "deleted": True})


@router.post("/{resume_id}/parse")
def parse_resume(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        task = use_cases.parse(actor, resume_id)
    except (ResumeNotFound, ResumeRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=task_data(task))


@router.get("/{resume_id}/parse-result")
def get_resume_parse_result(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        result = use_cases.get_parse_result(actor, resume_id)
    except (ResumeNotFound, ParseResultNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=resume_parse_data(result))


@router.put("/{resume_id}/parse-result")
def edit_resume_parse_result(
    resume_id: str,
    payload: ResumeParseResultUpdate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        result = use_cases.update_parse_result(actor, resume_id, _changes(payload))
    except (ResumeNotFound, ResumeRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=resume_parse_data(result))


@router.post("/{resume_id}/parse-result/confirm")
def confirm_resume_parse_result(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        result = use_cases.confirm(actor, resume_id)
    except (ResumeNotFound, ParseResultNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=resume_parse_data(result))


def _skill_profile_data(resume_id: str, skills: list) -> dict[str, object]:
    return {
        "resume_id": resume_id,
        "skills": [resume_skill_data(skill) for skill in skills],
    }


@router.post("/{resume_id}/skill-profile")
def generate_resume_skill_profile(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        skills = use_cases.generate_skill_profile(actor, resume_id)
    except (ResumeNotFound, ResumeRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_skill_profile_data(resume_id, skills))

@router.get("/{resume_id}/skill-profile")
def get_resume_skill_profile(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageResumes = Depends(get_resume_use_cases),
):
    try:
        skills = use_cases.get_skill_profile(actor, resume_id)
    except (ResumeNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_skill_profile_data(resume_id, skills))
