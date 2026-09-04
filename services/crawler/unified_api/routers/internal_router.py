"""Internal service-to-service API for Main Backend.

These endpoints are not for browsers.  They use a simple Bearer token and are
kept deliberately thin: they reuse the existing task manager, crawler services
and Offline Bundle exporter.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from unified_api.auth import require_internal_token
from unified_api.offline_export.exporter import BundleExporter
from unified_api.offline_export.repository import MySQLExportRepository
from unified_api.services import boss_service
from unified_api.services import liepin_service
from unified_api.services import company_service
from unified_api.services.task_manager import get_task_status, start_task
from jobgraph_contracts.offline_bundle import BundleMode

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class InternalCrawlRequest(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    keyword: str = Field(default="", max_length=255)
    city: str = Field(default="", max_length=64)
    pages: int = Field(default=5, ge=1, le=100)


class InternalExportRequest(BaseModel):
    task_id: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=32)


class InternalCookiesRequest(BaseModel):
    cookies: list[dict]


@router.get("/sources")
def list_sources(_: None = Depends(require_internal_token)):
    boss_status = boss_service.boss_login_status()
    boss_logged_in = bool(boss_status.get("logged_in", False))
    liepin_status = liepin_service.liepin_login_status()
    liepin_logged_in = bool(liepin_status.get("logged_in", False))
    sources = [
        {
            "source": "boss",
            "available": True,
            "ready": boss_logged_in,
            "login_required": not boss_logged_in,
            "reason": None if boss_logged_in else "Boss login is required on the crawler service",
        },
       {
           "source": "liepin",
           "available": True,
           "ready": liepin_logged_in,
           "login_required": not liepin_logged_in,
           "reason": None if liepin_logged_in else "Liepin login is required on the crawler service",
       },
        {
            "source": "feishu",
            "available": True,
            "ready": True,
            "login_required": False,
            "reason": None,
        },
    ]
    return {"data": {"sources": sources}}


@router.get("/boss/login/status")
def get_boss_login_status(_: None = Depends(require_internal_token)):
    return {"data": boss_service.boss_login_status()}


@router.get("/liepin/login/status")
def get_liepin_login_status(_: None = Depends(require_internal_token)):
    return {"data": liepin_service.liepin_login_status()}


@router.post("/boss/cookies")
def save_boss_cookies(
    body: InternalCookiesRequest,
    _: None = Depends(require_internal_token),
):
    result = boss_service.set_cookies_and_verify(body.cookies)
    return {"data": result}


@router.post("/liepin/cookies")
def save_liepin_cookies(
    body: InternalCookiesRequest,
    _: None = Depends(require_internal_token),
):
    result = liepin_service.set_cookies_and_verify(body.cookies)
    return {"data": result}


@router.post("/crawl")
def create_crawl_task(
    body: InternalCrawlRequest,
    _: None = Depends(require_internal_token),
):
    source = body.source.strip().lower()
    if source == "boss":
        task_id = start_task(
            user_id=0,
            task_type="boss",
            params={
                "keyword": body.keyword,
                "city": body.city,
                "pages": body.pages,
            },
            run_func=boss_service.run_boss_crawl,
        )
    elif source == "liepin":
        # Liepin's existing service consumes lists; keep the Main request shape
        # and adapt to the existing crawler service contract.
        task_id = start_task(
            user_id=0,
            task_type="liepin",
            params={
                "keywords": [body.keyword],
                "cities": [body.city],
                "pages": body.pages,
            },
            run_func=liepin_service.run_liepin_crawl,
        )
    elif source == "feishu":
        task_id = start_task(
            user_id=0,
            task_type="feishu",
            params={"company_name": "all", "platform": "feishu"},
            run_func=company_service.run_company_crawl,
        )
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "source_unavailable",
                "error_message": f"Unsupported crawler source: {source}",
            },
        )
    return {"data": {"task_id": task_id, "source": source}}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, _: None = Depends(require_internal_token)):
    task = get_task_status(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawler task not found")
    return {"data": task}


@router.post("/export")
def export_bundle(
    body: InternalExportRequest,
    _: None = Depends(require_internal_token),
):
    task = get_task_status(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawler task not found")
    if task.get("status") not in {"completed", "succeeded"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "crawl_not_completed",
                "error_message": "Crawler task has not completed",
            },
        )
    source = body.source.strip().lower()
    task_type = str(task.get("task_type") or "").strip().lower()
    if task_type != source:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "task_source_mismatch",
                "error_message": (
                    f"Crawler task {body.task_id} belongs to source "
                    f"{task_type or 'unknown'}, not {source}"
                ),
            },
        )
    output_dir = os.getenv("OFFLINE_BUNDLE_DIR", "bundles").strip() or "bundles"
    exporter = BundleExporter(MySQLExportRepository())
    try:
        summary = exporter.export(
            output=Path(output_dir), mode=BundleMode.FULL, task_id=body.task_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "export_failed",
                "error_message": str(exc),
            },
        ) from exc
    return {
        "data": {
            "bundle_id": summary.bundle_id,
            "file_name": summary.output_path.name,
            "record_count": summary.record_count,
            "hash": None,
        }
    }
