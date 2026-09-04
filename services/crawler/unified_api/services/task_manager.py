"""后台任务管理器。"""
import uuid
import threading
import json
import traceback
from typing import Callable
from loguru import logger
from ..database import get_conn

# 内存状态字典
_TASK_STORE: dict[str, dict] = {}


def _init_task(task_id: str, user_id: int, task_type: str, params: dict):
    """在 MySQL 和内存中同时初始化任务记录。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO crawl_tasks (id, user_id, task_type, params, status) VALUES (%s,%s,%s,%s,'running')",
        (task_id, user_id, task_type, json.dumps(params)),
    )
    conn.commit()
    cur.close()
    conn.close()
    _TASK_STORE[task_id] = {
        "task_type": task_type,
        "status": "running",
        "progress": "正在初始化...",
        "result_count": 0,
        "error_message": None,
    }


def _finish_task(task_id: str, result_count: int, error: str | None = None):
    """完成或失败时更新 MySQL 和内存。"""
    status = "failed" if error else "completed"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE crawl_tasks SET status=%s, result_count=%s, error_message=%s, completed_at=NOW() WHERE id=%s",
        (status, result_count, error, task_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    if task_id in _TASK_STORE:
        _TASK_STORE[task_id]["status"] = status
        _TASK_STORE[task_id]["result_count"] = result_count
        if error:
            _TASK_STORE[task_id]["error_message"] = error


def update_progress(task_id: str, message: str):
    if task_id in _TASK_STORE:
        _TASK_STORE[task_id]["progress"] = message


def get_task_status(task_id: str) -> dict | None:
    """先从内存查，再回退到 MySQL。"""
    if task_id in _TASK_STORE:
        return {"task_id": task_id, **_TASK_STORE[task_id]}

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM crawl_tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {
            "task_id": row["id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "progress": row.get("progress"),
            "result_count": row["result_count"],
            "error_message": row.get("error_message"),
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        }
    return None


def get_user_tasks(user_id: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM crawl_tasks WHERE user_id = %s ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    results = []
    for row in rows:
        # 优先使用内存中的最新状态
        mem = _TASK_STORE.get(row["id"])
        results.append({
            "task_id": row["id"],
            "task_type": row["task_type"],
            "status": mem["status"] if mem else row["status"],
            "progress": mem["progress"] if mem else row.get("progress"),
            "result_count": mem["result_count"] if mem else row["result_count"],
            "error_message": mem["error_message"] if mem else row.get("error_message"),
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        })
    return results


def start_task(
    user_id: int,
    task_type: str,
    params: dict,
    run_func: Callable,
) -> str:
    """启动后台爬虫任务。"""
    task_id = str(uuid.uuid4())[:8]
    _init_task(task_id, user_id, task_type, params)

    def _runner():
        try:
            update_progress(task_id, "爬虫启动中...")
            count = run_func(task_id=task_id, user_id=user_id, **params)
            _finish_task(task_id, count or 0)
            logger.info(f"Task {task_id} completed, {count} results")
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}\n{traceback.format_exc()}")
            _finish_task(task_id, 0, str(e))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return task_id
