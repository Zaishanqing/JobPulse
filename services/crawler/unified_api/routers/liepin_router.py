"""猎聘爬虫路由。"""
from fastapi import APIRouter, Depends
from ..auth import get_current_user
from ..schemas.job import LiepinCrawlRequest, CrawlResponse
from ..services.liepin_service import run_liepin_crawl
from ..services.task_manager import start_task

router = APIRouter(prefix="/api/liepin", tags=["猎聘"])


@router.post("/crawl", response_model=CrawlResponse)
def start_liepin_crawl(body: LiepinCrawlRequest, user: dict = Depends(get_current_user)):
    task_id = start_task(
        user_id=user['id'],
        task_type="liepin",
        params={"keywords": body.keywords, "cities": body.cities, "pages": body.pages},
        run_func=run_liepin_crawl,
    )
    kw_count = len(body.keywords) if body.keywords else "全部"
    city_count = len(body.cities) if body.cities else "全部"
    return CrawlResponse(
        task_id=task_id,
        message=f"猎聘爬虫已启动: {kw_count} 关键词 x {city_count} 城市",
    )
