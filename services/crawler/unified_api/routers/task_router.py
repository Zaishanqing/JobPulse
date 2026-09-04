"""任务查询路由。"""
from fastapi import APIRouter, Depends, HTTPException
from ..auth import get_current_user
from ..schemas.task import TaskStatus
from ..services.task_manager import get_task_status, get_user_tasks

router = APIRouter(prefix="/api", tags=["任务管理"])


@router.get("/task/{task_id}", response_model=TaskStatus)
def query_task(task_id: str, user: dict = Depends(get_current_user)):
    task = get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskStatus(**task)


@router.get("/tasks")
def list_tasks(user: dict = Depends(get_current_user)):
    tasks = get_user_tasks(user['id'])
    return {"tasks": [TaskStatus(**t) for t in tasks], "total": len(tasks)}
