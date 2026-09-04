from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.acquisition.api.acquisition_schemas import (
    CreateBundleRequest,
    CreateCrawlJobRequest,
    CreateSourceRequest,
    UpdateSourceRequest,
)
from app.api.router import envelope, require_token

acquisition_router = APIRouter(prefix="/internal/v1/acquisition", dependencies=[Depends(require_token)])

_SENSITIVE_KEY_PARTS = (
    "api_key", "apikey", "authorization", "bearer", "cookie", "credential",
    "password", "secret", "token",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_auth_values(value):
    if isinstance(value, dict):
        return {
            key: _redact_auth_values(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [_redact_auth_values(item) for item in value]
    return value


def _public_source(value: dict[str, object]) -> dict[str, object]:
    public = {
        key: _redact_auth_values(item) if key == "endpoint_config" else item
        for key, item in value.items()
        if key != "auth_config"
    }
    public["auth_configured"] = bool(value.get("auth_config"))
    return public


def acquisition_store(request: Request):
    return request.app.state.acquisition_store


def crawl_service(request: Request):
    return request.app.state.crawl_service


# -- Sources --

@acquisition_router.post("/sources")
def create_source(payload: CreateSourceRequest, store=Depends(acquisition_store)):
    return envelope(_public_source(store.create_source(payload.model_dump())))


@acquisition_router.get("/sources")
def list_sources(source_type: str | None = None, status: str | None = None, store=Depends(acquisition_store)):
    return envelope([_public_source(item) for item in store.list_sources(source_type, status)])


@acquisition_router.get("/sources/{source_id}")
def get_source(source_id: str, store=Depends(acquisition_store)):
    result = store.get_source(source_id)
    if result is None:
        raise HTTPException(status_code=404, detail="source not found")
    return envelope(_public_source(result))


@acquisition_router.put("/sources/{source_id}")
def update_source(source_id: str, payload: UpdateSourceRequest, store=Depends(acquisition_store)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    result = store.update_source(source_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="source not found")
    return envelope(_public_source(result))


@acquisition_router.delete("/sources/{source_id}")
def delete_source(source_id: str, store=Depends(acquisition_store)):
    result = store.delete_source(source_id)
    if result is None:
        raise HTTPException(status_code=404, detail="source not found")
    return envelope(_public_source(result))


# -- Crawl Jobs --

@acquisition_router.post("/crawl-jobs", status_code=202)
def create_crawl_job(payload: CreateCrawlJobRequest, store=Depends(acquisition_store)):
    return envelope(store.create_crawl_job(payload.model_dump(mode="json")))


@acquisition_router.get("/crawl-jobs")
def list_crawl_jobs(source_id: str | None = None, job_status: str | None = None, store=Depends(acquisition_store)):
    return envelope(store.list_crawl_jobs(source_id, job_status))


@acquisition_router.get("/crawl-jobs/{job_id}")
def get_crawl_job(job_id: str, store=Depends(acquisition_store)):
    result = store.get_crawl_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="crawl job not found")
    return envelope(result)


@acquisition_router.post("/crawl-jobs/{job_id}/retry")
def retry_crawl_job(job_id: str, store=Depends(acquisition_store)):
    try:
        result = store.retry_crawl_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(result)


@acquisition_router.post("/crawl-jobs/{job_id}/cancel")
def cancel_crawl_job(job_id: str, store=Depends(acquisition_store)):
    try:
        result = store.cancel_crawl_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(result)


# -- Snapshots --

@acquisition_router.get("/sources/{source_id}/snapshots")
def list_snapshots(source_id: str, offset: int = 0, limit: int = 50, store=Depends(acquisition_store)):
    return envelope(store.list_snapshots(source_id, offset, limit))


# -- Bundles --

@acquisition_router.post("/sources/{source_id}/bundles", status_code=202)
def create_bundle(source_id: str, payload: CreateBundleRequest, svc=Depends(crawl_service)):
    try:
        result = svc.generate_bundle(
            job_id=payload.job_id,
            source_id=source_id,
            bundle_type=payload.bundle_type,
            snapshot_ids=payload.snapshot_ids,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(result)


@acquisition_router.get("/bundles/{bundle_id}")
def get_bundle(bundle_id: str, store=Depends(acquisition_store)):
    result = store.get_bundle(bundle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="bundle not found")
    return envelope(result)
