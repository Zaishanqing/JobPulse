from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.acquisition import get_acquisition_use_cases
from app.contexts.acquisition import (
    AcquisitionJobNotFound,
    AcquisitionRetryRejected,
    AcquisitionUseCases,
)
from app.domain.accounts import AccountActor
from app.core.response import success_response
from app.schemas.acquisition import AcquisitionJobCreateRequest

router = APIRouter(tags=["acquisition"])


@router.get("/acquisition/sources")
def list_acquisition_sources(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    return success_response(
        data=[asdict(item) for item in use_cases.list_sources(actor)]
    )


@router.post("/acquisition/boss/cookies", status_code=202)
def save_boss_cookies(
    payload: dict,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    return success_response(data=use_cases.save_boss_cookies(actor, payload.get("cookies", [])))


@router.post("/acquisition/liepin/cookies", status_code=202)
def save_liepin_cookies(
    payload: dict,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    return success_response(data=use_cases.save_liepin_cookies(actor, payload.get("cookies", [])))


@router.get("/acquisition/boss/login/status")
def get_boss_login_status(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    return success_response(data=asdict(use_cases.get_boss_login_status(actor)))


@router.get("/acquisition/liepin/login/status")
def get_liepin_login_status(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    return success_response(data=asdict(use_cases.get_liepin_login_status(actor)))


@router.post("/acquisition/jobs", status_code=202)
def create_acquisition_job(
    payload: AcquisitionJobCreateRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    try:
        record = use_cases.create(
            actor,
            source=payload.source,
            keyword=payload.keyword,
            city=payload.city,
            pages=payload.pages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success_response(data=asdict(record))


@router.get("/acquisition/jobs")
def list_acquisition_jobs(
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    try:
        result = use_cases.list(
            actor,
            status=status,
            source=source,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success_response(
        data={
            "items": [asdict(item) for item in result.items],
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }
    )


@router.get("/acquisition/jobs/{job_id}")
def get_acquisition_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    try:
        record = use_cases.get(actor, job_id)
    except AcquisitionJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success_response(data=asdict(record))


@router.post("/acquisition/jobs/{job_id}/retry", status_code=202)
def retry_acquisition_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: AcquisitionUseCases = Depends(get_acquisition_use_cases),
):
    try:
        record = use_cases.retry(actor, job_id)
    except AcquisitionJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AcquisitionRetryRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success_response(data=asdict(record))
