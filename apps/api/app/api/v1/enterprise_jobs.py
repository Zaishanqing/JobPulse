from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.recruitment import get_recruitment_handlers
from app.contexts.talent_acquisition import JobNotFound, RecruitmentHandlers
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.recruitment import RecruitmentRuleViolation
from app.contexts.talent_acquisition import JobRecord, SkillWeightInput, SkillWeightRecord
from app.schemas.enterprise_job import (
    EnterpriseJobCreateRequest,
    EnterpriseJobSkillWeightsRequest,
    EnterpriseJobUpdateRequest,
    HeadcountUpdateRequest,
)
from app.domain.errors import PermissionDenied
from app.schemas.api_requests import EnterpriseSkillClassificationRequest


router = APIRouter(prefix="/enterprise-jobs", tags=["enterprise-jobs"])


def _job_data(job: JobRecord) -> dict:
    return {
        "enterprise_job_id": job.job_id,
        "enterprise_id": job.enterprise_id,
        "title": job.title,
        "standard_position_id": job.standard_position_id,
        "jd_text": job.jd_text,
        "requirement_graph": job.requirement_graph,
        "headcount": job.headcount,
        "location": job.location,
        "employment_type": job.employment_type,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_unit": job.salary_unit,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _weight_data(weight: SkillWeightRecord) -> dict:
    return {
        "id": weight.weight_id,
        "enterprise_job_id": weight.job_id,
        "skill_id": weight.skill_id,
        "weight": weight.weight,
        "is_required": weight.is_required,
        "is_bonus": weight.is_bonus,
    }


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, JobNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, RecruitmentRuleViolation):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")


@router.post("")
def create_enterprise_job(
    payload: EnterpriseJobCreateRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        job = handlers.jobs.create(actor, payload.model_dump())
    except (JobNotFound, PermissionDenied, RecruitmentRuleViolation) as exc:
        raise _http_error(exc) from exc
    return success_response(data=_job_data(job))


@router.get("")
def get_enterprise_jobs(
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        jobs = handlers.jobs.list(actor)
    except PermissionDenied as exc:
        raise _http_error(exc) from exc
    return success_response(data=[_job_data(job) for job in jobs])


@router.get("/{job_id}")
def get_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        job = handlers.jobs.get(actor, job_id)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(data=_job_data(job))


@router.put("/{job_id}")
def update_enterprise_job(
    job_id: str,
    payload: EnterpriseJobUpdateRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        job = handlers.jobs.update(actor, job_id, payload.model_dump(exclude_unset=True))
    except (JobNotFound, PermissionDenied, RecruitmentRuleViolation) as exc:
        raise _http_error(exc) from exc
    return success_response(data=_job_data(job))


@router.delete("/{job_id}")
def delete_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        handlers.jobs.delete(actor, job_id)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(data={"enterprise_job_id": job_id, "deleted": True})


def _change_status(job_id: str, status: str, actor: AccountActor, handlers: RecruitmentHandlers):
    try:
        return handlers.jobs.change_status(actor, job_id, status)
    except (JobNotFound, PermissionDenied, RecruitmentRuleViolation) as exc:
        raise _http_error(exc) from exc


@router.put("/{job_id}/publish")
def publish_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return success_response(data=_job_data(_change_status(job_id, "published", actor, handlers)))


@router.put("/{job_id}/pause")
def pause_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return success_response(data=_job_data(_change_status(job_id, "paused", actor, handlers)))


@router.put("/{job_id}/resume")
def resume_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return success_response(data=_job_data(_change_status(job_id, "published", actor, handlers)))


@router.put("/{job_id}/cancel")
def cancel_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return success_response(data=_job_data(_change_status(job_id, "cancelled", actor, handlers)))


@router.put("/{job_id}/headcount")
def change_enterprise_job_headcount(
    job_id: str,
    payload: HeadcountUpdateRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        old_headcount, job = handlers.jobs.change_headcount(actor, job_id, payload.headcount)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(
        data={
            "enterprise_job_id": job.job_id,
            "old_headcount": old_headcount,
            "new_headcount": job.headcount,
            "status": job.status,
        }
    )


@router.get("/{job_id}/skill-weights")
def get_enterprise_job_skill_weights(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        weights = handlers.jobs.weights(actor, job_id)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(data=[_weight_data(weight) for weight in weights])


@router.put("/{job_id}/skill-weights")
def set_enterprise_job_skill_weights(
    job_id: str,
    payload: EnterpriseJobSkillWeightsRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    inputs = [SkillWeightInput(**item.model_dump()) for item in payload.weights]
    try:
        weights = handlers.jobs.replace_weights(actor, job_id, inputs)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(
        data={
            "enterprise_job_id": job_id,
            "updated_count": len(weights),
            "weights": [_weight_data(weight) for weight in weights],
        }
    )


@router.post("/{job_id}/skill-weights/reset")
def reset_enterprise_job_skill_weights(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        count = handlers.jobs.reset_weights(actor, job_id)
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(data={"enterprise_job_id": job_id, "deleted_count": count})


def _skill_ids(payload: dict, field_name: str) -> list[str]:
    values = payload.get(field_name, payload.get("skills"))
    if not isinstance(values, list):
        raise HTTPException(status_code=422, detail=f"{field_name} must be a list")
    result = []
    for value in values:
        skill_id = value.get("skill_id") if isinstance(value, dict) else value
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise HTTPException(
                status_code=422, detail="Each skill must provide a non-empty skill_id"
            )
        result.append(skill_id.strip())
    return result


def _classify(
    job_id: str,
    payload: dict,
    field_name: str,
    classification: str,
    actor: AccountActor,
    handlers: RecruitmentHandlers,
):
    try:
        weights = handlers.jobs.classify_skills(
            actor, job_id, _skill_ids(payload, field_name), classification
        )
    except (JobNotFound, PermissionDenied) as exc:
        raise _http_error(exc) from exc
    return success_response(
        data={
            "enterprise_job_id": job_id,
            field_name: [_weight_data(item) for item in weights],
            "updated_count": len(weights),
            "implementation_status": "database_persisted_skill_classification",
        }
    )


@router.put("/{job_id}/required-skills")
def set_required_skills(
    job_id: str,
    payload: EnterpriseSkillClassificationRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return _classify(job_id, payload.root, "required_skills", "required", actor, handlers)


@router.put("/{job_id}/bonus-skills")
def set_bonus_skills(
    job_id: str,
    payload: EnterpriseSkillClassificationRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    return _classify(job_id, payload.root, "bonus_skills", "bonus", actor, handlers)
