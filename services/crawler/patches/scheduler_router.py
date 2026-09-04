"""定时调度 API 路由。"""
from fastapi import APIRouter
from patches.scheduler import get_status, list_jobs

router = APIRouter(prefix="/api/scheduler", tags=["定时调度"])


@router.get("/status")
def scheduler_status():
    """返回调度器运行状态和已注册的定时任务。"""
    return get_status()


@router.get("/jobs")
def scheduler_jobs():
    """列出当前注册的定时任务。"""
    return {"jobs": list_jobs()}
