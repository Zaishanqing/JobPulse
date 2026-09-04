"""多公司爬虫路由。"""
from fastapi import APIRouter, Depends, Query
from ..auth import get_current_user
from ..schemas.job import (
    CompanyCrawlRequest, CompanyJobItem, CompanyJobListResponse,
    CompanyInfo, CompanyStats, CrawlResponse, EnvelopeExportRequest,
)
from ..services.company_service import (
    run_company_crawl, get_company_jobs, get_company_stats, get_company_list,
    get_company_job_by_id, export_company_envelopes,
)
from ..services.task_manager import start_task

router = APIRouter(prefix="/api/company", tags=["多公司爬虫"])


@router.post("/crawl", response_model=CrawlResponse)
def start_company_crawl(body: CompanyCrawlRequest, user: dict = Depends(get_current_user)):
    task_id = start_task(
        user_id=user['id'],
        task_type="company",
        params={"company_name": body.company_name, "platform": body.platform},
        run_func=run_company_crawl,
    )
    msg = f"多公司爬虫已启动: {body.company_name}"
    if body.platform:
        msg += f" (平台: {body.platform})"
    return CrawlResponse(task_id=task_id, message=msg)


@router.get("/jobs", response_model=CompanyJobListResponse)
def list_company_jobs(
    company_name: str = Query(default=None),
    platform: str = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    rows, total = get_company_jobs(
        company_name=company_name, platform=platform, page=page, page_size=page_size
    )
    jobs = [CompanyJobItem(
        id=r['id'], company_name=r['company_name'], platform=r['platform'] or '',
        job_title=r['job_title'] or '', salary_min=r['salary_min'] or 0,
        salary_max=r['salary_max'] or 0, experience=r.get('experience'),
        education=r.get('education'), skill_tags=r.get('skill_tags'),
        location=r.get('location'), source_platform=r.get('source_platform', 'company'),
        source_url=r.get('source_url'),
        created_at=str(r['created_at']) if r.get('created_at') else None,
    ) for r in rows]
    return CompanyJobListResponse(jobs=jobs, total=total, page=page, page_size=page_size)


@router.get("/companies", response_model=list[CompanyInfo])
def list_companies(user: dict = Depends(get_current_user)):
    return [CompanyInfo(**c) for c in get_company_list()]


@router.get("/stats", response_model=CompanyStats)
def company_stats(user: dict = Depends(get_current_user)):
    s = get_company_stats()
    return CompanyStats(**s)


# ---------------------------------------------------------------------------
# Envelope / raw export (task 02)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/raw")
def get_company_job_raw(job_id: int, user: dict = Depends(get_current_user)):
    """返回单条岗位的 CrawlerJDEnvelopeV1。

    只有 raw_text_status=completed 的记录才能导出。
    """
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

    row = get_company_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    if row.get("raw_text_status") != "completed":
        raise HTTPException(status_code=422, detail={
            "error_code": "raw_text_unavailable",
            "error_message": row.get("raw_text_error") or "full JD not available",
        })

    from ..services.company_service import _row_to_job_data
    job = _row_to_job_data(row)
    try:
        envelope = job_data_to_envelope(job)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return JSONResponse(content=envelope.model_dump(mode="json"))


@router.post("/jobs/export-envelopes")
def export_company_envelopes_endpoint(
    body: EnvelopeExportRequest,
    user: dict = Depends(get_current_user),
):
    """批量导出 Envelope（仅 completed，返回成功/失败明细）。"""
    from fastapi.responses import JSONResponse

    result = export_company_envelopes(
        company_name=body.company_name,
        platform=body.platform,
        limit=body.limit,
    )
    return JSONResponse(content={"code": 0, "message": "success", "data": result})
