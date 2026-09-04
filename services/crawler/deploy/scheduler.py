"""定时调度模块 —— 独立运行，含任务锁、租约、重试和时区控制。

设计原则 (per 爬虫部署.md):
- 单实例运行 (由 Docker Compose replicas=1 保证)
- 同一任务不重叠 (max_instances=1, coalesce=True, DB 租约)
- 显式 Asia/Shanghai 时区
- 僵尸任务恢复 (启动时清理超时 running 状态)
- 有限重试 (2 次, 指数退避)
- 单家公司独立失败, 不阻断其余公司
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("patches.scheduler")

# ---------------------------------------------------------------------------
# APScheduler
# ---------------------------------------------------------------------------
_scheduler: Any = None
_job_ids: list[str] = []

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
    _HAS_APSCHEDULER = False

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 任务租约配置
LEASE_SECONDS = int(os.getenv("CRAWLER_LEASE_SECONDS", "5400"))   # 90 分钟
MAX_RETRIES = int(os.getenv("CRAWLER_MAX_RETRIES", "2"))
RETRY_BACKOFF_BASE = 60  # 秒

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
# 数据库辅助
# ---------------------------------------------------------------------------

def _get_db_conn():
    import pymysql
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        connect_timeout=5,
    )


def _ensure_schema():
    """幂等创建调度相关的表。"""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crawl_task_leases (
                    task_type VARCHAR(32) NOT NULL PRIMARY KEY,
                    task_id VARCHAR(32) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'running',
                    started_at DATETIME NOT NULL,
                    heartbeat_at DATETIME NOT NULL,
                    lease_expires_at DATETIME NOT NULL,
                    attempt_count INT NOT NULL DEFAULT 1,
                    next_retry_at DATETIME,
                    error_message TEXT,
                    UNIQUE KEY uk_task_type_status (task_type, status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crawl_task_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    task_type VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    result_count INT DEFAULT 0,
                    error_message TEXT,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME NOT NULL,
                    KEY idx_task_type_time (task_type, started_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        conn.commit()
    finally:
        conn.close()


def _recover_zombie_tasks():
    """启动时将超时的 running 任务标记为 failed。"""
    conn = _get_db_conn()
    try:
        now = datetime.now(TZ)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crawl_task_leases
                SET status = 'failed', error_message = 'zombie recovery: lease expired'
                WHERE status = 'running' AND lease_expires_at < %s
            """, (now.strftime("%Y-%m-%d %H:%M:%S"),))
            if cur.rowcount:
                logger.warning("[僵尸恢复] 将 %s 条超时 running 任务标记为 failed", cur.rowcount)
        conn.commit()
    finally:
        conn.close()


def _try_acquire_lease(task_type: str, task_id: str, attempt: int) -> bool:
    """尝试获取任务租约。同一 task_type 已有 running 时返回 False。"""
    conn = _get_db_conn()
    try:
        now = datetime.now(TZ)
        expires = now + timedelta(seconds=LEASE_SECONDS)
        with conn.cursor() as cur:
            # 先清理超时租约
            cur.execute("""
                DELETE FROM crawl_task_leases
                WHERE task_type = %s AND status = 'running' AND lease_expires_at < %s
            """, (task_type, now.strftime("%Y-%m-%d %H:%M:%S")))
            # 尝试插入
            try:
                cur.execute("""
                    INSERT INTO crawl_task_leases
                    (task_type, task_id, status, started_at, heartbeat_at,
                     lease_expires_at, attempt_count)
                    VALUES (%s, %s, 'running', %s, %s, %s, %s)
                """, (
                    task_type, task_id,
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    expires.strftime("%Y-%m-%d %H:%M:%S"),
                    attempt,
                ))
                conn.commit()
                return True
            except Exception:
                # 唯一键冲突: 已有 running 任务
                conn.rollback()
                return False
    finally:
        conn.close()


def _release_lease(task_type: str, task_id: str, status: str,
                   result_count: int = 0, error: str = ""):
    """释放租约并写入历史记录。"""
    conn = _get_db_conn()
    try:
        now = datetime.now(TZ)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM crawl_task_leases WHERE task_type = %s AND task_id = %s",
                (task_type, task_id))
            cur.execute("""
                INSERT INTO crawl_task_history
                (task_type, task_id, status, result_count, error_message,
                 started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                task_type, task_id, status, result_count, error or "",
                now.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
            ))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 任务执行函数映射
# ---------------------------------------------------------------------------

def _execute_company(task_id: str, user_id: int, params: dict) -> int:
    from unified_api.services.company_service import run_company_crawl
    return run_company_crawl(
        company_name=params.get("company_name", "all"),
        platform=params.get("platform"),
        task_id=task_id,
        user_id=user_id,
    )


def _execute_boss(task_id: str, user_id: int, params: dict) -> int:
    from unified_api.services.boss_service import run_boss_crawl
    return run_boss_crawl(
        keyword=params["keyword"],
        city=params["city"],
        pages=params.get("pages", 5),
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


def _execute_pipeline(task_id: str, user_id: int, params: dict) -> int:
    from deploy.daily_pipeline import main
    return main()


_EXECUTORS = {
    "company": _execute_company,
    "boss": _execute_boss,
    "liepin": _execute_liepin,
    "pipeline": _execute_pipeline,
    "export_only": _execute_pipeline,
}


# ---------------------------------------------------------------------------
# 带重试的任务包装
# ---------------------------------------------------------------------------

def _run_with_retry(task_type: str, task_id: str, user_id: int,
                    params: dict, executor, name: str) -> int:
    """执行任务，带有限重试和指数退避。"""
    last_error = ""
    max_attempts = MAX_RETRIES + 1  # 1 次主尝试 + N 次重试

    for attempt in range(1, max_attempts + 1):
        # 获取租约
        if not _try_acquire_lease(task_type, task_id, attempt):
            logger.warning(
                "[跳过重叠] %s — 同类型任务正在运行中, task_type=%s", name, task_type)
            return 0

        try:
            logger.info("[执行] %s (attempt=%s/%s, task_id=%s)",
                        name, attempt, max_attempts, task_id)
            count = executor(task_id=task_id, user_id=user_id, params=params)
            _release_lease(task_type, task_id, "completed", result_count=count)
            logger.info("[完成] %s — %s 条数据", name, count)
            return count

        except Exception as exc:
            last_error = str(exc)[:500]
            logger.exception("[失败] %s (attempt=%s): %s", name, attempt, last_error)
            _release_lease(task_type, task_id, "failed", error=last_error)

            if attempt <= MAX_RETRIES:
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.info("[重试] %s 将在 %s 秒后重试", name, backoff)
                time.sleep(backoff)
            else:
                logger.error("[最终失败] %s 已达最大重试次数: %s", name, last_error)

    return 0


# ---------------------------------------------------------------------------
# 定时任务函数创建
# ---------------------------------------------------------------------------

def _create_job_func(schedule: dict):
    name = schedule.get("name", "未命名")
    task_type = schedule["task_type"]
    user_id = schedule.get("user_id", 1)
    params = schedule.get("params", {})
    executor = _EXECUTORS.get(task_type)

    if executor is None:
        raise ValueError(f"不支持的 task_type: {task_type!r}（任务: {name}）")

    def _run():
        task_id = f"sched_{uuid.uuid4().hex[:8]}"
        logger.info("[定时触发] %s (type=%s, task_id=%s)", name, task_type, task_id)
        _run_with_retry(task_type, task_id, user_id, params, executor, name)

    _run.__name__ = f"sched_{task_type}"
    return _run


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def start_scheduler(config_path: str | Path | None = None,
                    *, blocking: bool = False):
    if not _HAS_APSCHEDULER:
        raise ImportError("APScheduler 未安装")

    global _scheduler, _job_ids

    # 确保数据库 schema 存在
    _ensure_schema()
    try:
        from unified_api.database import ensure_schema as ensure_api_schema
        ensure_api_schema()
        logger.info("[调度器] unified_api schema 已就绪")
    except Exception as exc:
        logger.warning("[调度器] unified_api schema 初始化警告: %s", exc)

    # 恢复僵尸任务
    _recover_zombie_tasks()

    schedules = _load_schedules(config_path)
    enabled = [s for s in schedules if s.get("enabled", True)]

    if not enabled:
        logger.info("[调度器] 没有启用的任务")
        return None

    _scheduler = BackgroundScheduler(
        daemon=True,
        timezone=TZ,
        job_defaults={
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 3600,
        },
    )
    _job_ids = []

    for schedule in enabled:
        name = schedule.get("name", "未命名")
        cron_expr = schedule["cron"]
        try:
            parts = cron_expr.strip().split()
            if len(parts) != 5:
                raise ValueError(f"cron 必须为 5 字段: {cron_expr!r}")
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                timezone=TZ,
            )
        except Exception as exc:
            logger.error("[调度器] 跳过 %s — cron 无效 (%s): %s",
                         name, cron_expr, exc)
            continue

        job_func = _create_job_func(schedule)
        job = _scheduler.add_job(
            job_func,
            trigger=trigger,
            id=name,
            name=name,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        _job_ids.append(job.id)
        logger.info("[调度器] 已注册: %s (type=%s, cron=%s)",
                     name, schedule["task_type"], cron_expr)

    _scheduler.start()
    logger.info("[调度器] 已启动, %s 个任务, TZ=Asia/Shanghai", len(_job_ids))

    if blocking:
        import signal
        import sys

        def _shutdown(sig, frame):
            logger.info("[调度器] 收到信号 %s, 正在关闭...", sig)
            if _scheduler:
                _scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logger.info("[调度器] 下次触发时间:")
        for job in _scheduler.get_jobs():
            logger.info("  %s -> %s", job.name, job.next_run_time)
        while True:
            time.sleep(60)

    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[调度器] 已停止")


def list_jobs():
    if _scheduler is None:
        return []
    return [{
        "id": job.id,
        "name": job.name,
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        "trigger": str(job.trigger),
    } for job in _scheduler.get_jobs()]


def get_status() -> dict:
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
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    start_scheduler(blocking=True)
