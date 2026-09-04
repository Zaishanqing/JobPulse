"""Boss直聘路由。"""
from fastapi import APIRouter, Depends, Query
from ..auth import get_current_user
from ..schemas.job import (
    BossCrawlRequest, BossJobItem, BossJobListResponse,
    BossStats, CrawlResponse, EnvelopeExportRequest, KeywordItem, CityItem,
)
from ..config import COMMON_KEYWORDS, CITY_CODES
from ..services.boss_service import (
    run_boss_crawl, get_boss_jobs, get_boss_stats,
    boss_login_status,
    get_boss_job_by_id, export_boss_envelopes,
)
from ..services.task_manager import start_task

router = APIRouter(prefix="/api/boss", tags=["Boss直聘"])


@router.get("/login/status")
def check_login_status(user: dict = Depends(get_current_user)):
    return boss_login_status()


@router.post("/crawl", response_model=CrawlResponse)
def start_boss_crawl(body: BossCrawlRequest, user: dict = Depends(get_current_user)):
    task_id = start_task(
        user_id=user['id'],
        task_type="boss",
        params={"keyword": body.keyword, "city": body.city, "pages": body.pages},
        run_func=run_boss_crawl,
    )
    return CrawlResponse(task_id=task_id, message=f"Boss直聘爬虫已启动: {body.keyword} - {body.city}")


@router.get("/jobs", response_model=BossJobListResponse)
def list_boss_jobs(
    keyword: str = Query(default=None),
    city: str = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    rows, total = get_boss_jobs(keyword=keyword, city=city, page=page, page_size=page_size)
    jobs = [BossJobItem(
        id=r['id'], job_title=r['job_title'] or '', job_salary=r['job_salary'] or '',
        job_company=r['job_company'] or '', company_city=r['company_city'] or '',
        keyword=r['keyword'] or '', job_skill=r.get('job_skill'),
        job_lable=r.get('job_lable'),
        create_time=str(r['create_time']) if r.get('create_time') else None,
    ) for r in rows]
    return BossJobListResponse(jobs=jobs, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=BossStats)
def boss_stats(user: dict = Depends(get_current_user)):
    s = get_boss_stats()
    return BossStats(**s)


@router.get("/keywords", response_model=list[KeywordItem])
def boss_keywords(user: dict = Depends(get_current_user)):
    return [KeywordItem(keyword=k) for k in COMMON_KEYWORDS]


@router.get("/cities", response_model=list[CityItem])
def boss_cities(user: dict = Depends(get_current_user)):
    return [CityItem(city=k, city_code=v) for k, v in CITY_CODES.items()]


# ---------------------------------------------------------------------------
# Envelope / raw export (task 02 remediation)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/raw")
def get_boss_job_raw(job_id: int, user: dict = Depends(get_current_user)):
    """返回单条 Boss 岗位的 CrawlerJDEnvelopeV1。

    只有 raw_text_status=completed 的记录才能导出。
    福利、经验、学历、技能不会作为 JD 文本。
    """
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope
    from ..services.boss_service import _row_to_job_data

    row = get_boss_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    raw_status = row.get("raw_text_status") or ""
    if raw_status != "completed":
        raise HTTPException(status_code=422, detail={
            "error_code": "raw_text_unavailable",
            "error_message": row.get("raw_text_error")
                or f"raw_text_status={raw_status}: full JD detail not fetched",
        })

    job = _row_to_job_data(row, "boss_zhipin")
    try:
        envelope = job_data_to_envelope(job)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return JSONResponse(content=envelope.model_dump(mode="json"))


@router.post("/jobs/export-envelopes")
def export_boss_envelopes_endpoint(
    body: EnvelopeExportRequest,
    user: dict = Depends(get_current_user),
):
    """批量导出 Boss Envelope（仅 completed，返回成功/失败明细）。"""
    from fastapi.responses import JSONResponse

    result = export_boss_envelopes(
        keyword=body.keyword,
        city=body.city,
        limit=body.limit,
    )
    return JSONResponse(content={"code": 0, "message": "success", "data": result})
