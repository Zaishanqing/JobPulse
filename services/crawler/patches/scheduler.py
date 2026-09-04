"""定时调度模块 — 为项目添加定时自动启动爬虫任务的能力。

使用 APScheduler 的 BackgroundScheduler 执行定时任务。
读取 ``schedule_config.yaml`` 中定义的调度计划，
在后台线程中按 cron 表达式自动触发爬虫。

集成方式：
    方式一（推荐）：在 ``unified_api/main.py`` 的 startup 事件中调用:
        from patches.scheduler import start_scheduler
        start_scheduler()

    方式二：独立运行:
        python patches/scheduler.py

依赖：
    pip install apscheduler pyyaml
"""

from __future__ import annotations

import logging
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("patches.scheduler")

# ---------------------------------------------------------------------------
# APScheduler 别名包装 — 即使未安装也能清晰报错
# ---------------------------------------------------------------------------
_scheduler: Any = None              # type: ignore[annotation-unchecked]
_job_ids: list[str] = []

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    BackgroundScheduler = None     # type: ignore[assignment]
    CronTrigger = None             # type: ignore[assignment]
    _HAS_APSCHEDULER = False


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = CONFIG_DIR / "schedule_config.yaml"


def _load_schedules(config_path: str | Path | None) -> list[dict]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        logger.warning("调度配置文件不存在: %s，使用空配置", path)
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("schedules", [])


# ---------------------------------------------------------------------------
# 任务执行函数映射 — 委托给现有 service 模块
# ---------------------------------------------------------------------------

def _execute_boss(task_id: str, user_id: int, params: dict) -> int:
    from unified_api.services.boss_service import run_boss_crawl
    return run_boss_crawl(
        keyword=params["keyword"],
        city=params["city"],
        pages=params.get("pages", 5),
        task_id=task_id,
        user_id=user_id,
    )


def _execute_company(task_id: str, user_id: int, params: dict) -> int:
    from unified_api.services.company_service import run_company_crawl
    return run_company_crawl(
        company_name=params.get("company_name", "all"),
        platform=params.get("platform"),
        task_id=task_id,
        user_id=user_id,
    )


def _execute_liepin(task_id: str, user_id: int, params: dict) -> int:
    from unified_api.services.liepin_service import run_liepin_crawl
    return run_liepin_crawl(
        keywords=params.get("keywords"),
        cities=params.get("cities"),
        task_id=task_id,
        user_id=user_id,
    )


_EXECUTORS = {
    "boss": _execute_boss,
    "company": _execute_company,
    "liepin": _execute_liepin,
}


# ---------------------------------------------------------------------------
# 单个定时任务的包装器
# ---------------------------------------------------------------------------

def _create_job_func(schedule: dict):
    """为每条调度记录创建闭包，供 APScheduler 回调。"""

    name = schedule.get("name", "未命名")
    task_type = schedule["task_type"]
    user_id = schedule.get("user_id", 1)
    params = schedule.get("params", {})
    executor = _EXECUTORS.get(task_type)

    if executor is None:
        raise ValueError(
            f"不支持的 task_type: {task_type!r}（任务: {name}）"
        )

    def _run():
        task_id = f"sched_{uuid.uuid4().hex[:8]}"
        logger.info(
            "[定时调度] 触发: %s (type=%s, task_id=%s)", name, task_type, task_id
        )
        try:
            count = executor(task_id=task_id, user_id=user_id, params=params)
            logger.info(
                "[定时调度] 完成: %s, 获取 %s 条数据", name, count
            )
        except Exception:
            logger.exception("[定时调度] 失败: %s", name)

    _run.__name__ = f"scheduled_{task_type}_{name.replace(' ', '_')}"
    return _run


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def start_scheduler(
    config_path: str | Path | None = None,
    *,
    blocking: bool = False,
) -> Any:  # BackgroundScheduler | None
    """启动定时调度器。

    读取配置文件，注册所有启用的定时任务，在后台线程中运行。

    Args:
        config_path: 配置文件路径，默认使用 ``schedule_config.yaml``。
        blocking: True 时阻塞当前线程直到 Ctrl-C（独立运行模式）。

    Returns:
        BackgroundScheduler 实例，如果 APScheduler 未安装则返回 None。
    """
    if not _HAS_APSCHEDULER:
        raise ImportError(
            "APScheduler 未安装，请执行: pip install apscheduler"
        )

    global _scheduler, _job_ids

    schedules = _load_schedules(config_path)
    enabled = [s for s in schedules if s.get("enabled", True)]

    if not enabled:
        logger.info("[定时调度] 没有启用的调度任务，跳过启动")
        return None

    _scheduler = BackgroundScheduler(daemon=True)
    _job_ids = []

    for schedule in enabled:
        name = schedule.get("name", "未命名")
        cron_expr = schedule["cron"]
        try:
            parts = cron_expr.strip().split()
            if len(parts) != 5:
                raise ValueError(f"cron 表达式必须为 5 字段: {cron_expr!r}")
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
        except Exception as exc:
            logger.error(
                "[定时调度] 跳过 %s — cron 表达式无效 (%s): %s",
                name, cron_expr, exc,
            )
            continue

        job_func = _create_job_func(schedule)
        job = _scheduler.add_job(
            job_func,
            trigger=trigger,
            id=name,
            name=name,
            replace_existing=True,
        )
        _job_ids.append(job.id)
        logger.info(
            "[定时调度] 已注册: %s (type=%s, cron=%s)",
            name, schedule["task_type"], cron_expr,
        )

    _scheduler.start()
    logger.info(
        "[定时调度] 调度器已启动，共 %s 个定时任务（daemon=%s）",
        len(_job_ids), getattr(_scheduler, "_daemon", None),
    )

    if blocking:
        try:
            import signal
            import sys

            def _shutdown(sig, frame):
                logger.info("[定时调度] 收到信号 %s，正在关闭...", sig)
                _scheduler.shutdown(wait=False)
                sys.exit(0)

            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)
            logging.getLogger("apscheduler").setLevel(logging.WARNING)
            _scheduler.print_jobs()
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[定时调度] Ctrl-C，正在关闭...")
            _scheduler.shutdown(wait=False)

    return _scheduler


def stop_scheduler() -> None:
    """停止定时调度器。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[定时调度] 调度器已停止")


def list_jobs():
    """列出当前注册的定时任务（返回列表，用于 API）。"""
    if _scheduler is None:
        return []
    result = []
    for job in _scheduler.get_jobs():
        result.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": (
                job.next_run_time.isoformat() if job.next_run_time else None
            ),
            "trigger": str(job.trigger),
        })
    return result


def get_status() -> dict:
    """返回调度器运行状态。"""
    return {
        "running": _scheduler is not None and _scheduler.running,
        "job_count": len(_job_ids) if _scheduler else 0,
        "jobs": list_jobs(),
        "has_apscheduler": _HAS_APSCHEDULER,
    }


# ---------------------------------------------------------------------------
# 独立运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    start_scheduler(blocking=True)
