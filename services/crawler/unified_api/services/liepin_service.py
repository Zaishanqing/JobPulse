"""猎聘爬虫服务 — 包装 LiepinScraper (task 02 final)."""
import json
import os
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from jobgraph_contracts.source_identity import parse_crawl_time
from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

from ..database import get_conn
from ..offline_export.staging import ensure_export_candidate_in_transaction
from .persistence import PersistenceResult, classify_persistence_error
from .task_manager import update_progress

_CRAWLER_ROOT = Path(__file__).resolve().parents[2]
_LIEPIN_CONFIG_PATH = _CRAWLER_ROOT / "multi_company_scraper" / "config" / "liepin_search_params.yaml"
_DEFAULT_LIEPIN_COOKIE_FILE = (
    _CRAWLER_ROOT / "multi_company_scraper" / "config" / "liepin_cookies.local.json"
)
_LIEPIN_COOKIE_FILE = Path(
    os.getenv("LIEPIN_COOKIES_FILE", "").strip()
    or str(_DEFAULT_LIEPIN_COOKIE_FILE)
)


def _load_liepin_search_config() -> dict:
    import yaml

    with _LIEPIN_CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _liepin_cookie_count() -> int:
    if not _LIEPIN_COOKIE_FILE.exists():
        return 0
    try:
        cookies = json.loads(_LIEPIN_COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 0
    return len(cookies) if isinstance(cookies, list) else 0


def set_cookies_and_verify(cookies: list[dict]) -> dict:
    try:
        _LIEPIN_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LIEPIN_COOKIE_FILE.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"saved": True, "count": len(cookies), "verified": True}
    except OSError as exc:
        return {"saved": False, "count": 0, "verified": False, "error": str(exc)}


def liepin_login_status() -> dict:
    result = {
        "logged_in": _liepin_cookie_count() > 0,
        "cookie_count": _liepin_cookie_count(),
        "running": False,
        "status": "idle",
        "login_id": None,
        "started_at": None,
        "finished_at": None,
        "message": None,
        "updated_at": None,
    }
    if _LIEPIN_COOKIE_FILE.exists():
        try:
            result["updated_at"] = datetime.fromtimestamp(
                _LIEPIN_COOKIE_FILE.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            result["updated_at"] = None
    return result


def run_liepin_crawl(keywords: list[str] | None = None,
                     cities: list[str] | None = None,
                     pages: int = 5,
                     task_id: str = "", user_id: int = 1) -> int:
    config = _load_liepin_search_config()
    search_keywords = (
        list(keywords)
        if keywords is not None
        else list(config.get("keywords", ["Java"]))[:50]
    )
    search_cities = (
        list(cities)
        if cities is not None
        else list((config.get("cities") or {}).keys())[:21]
    )

    update_progress(
        task_id,
        f"猎聘爬虫启动: {len(search_keywords)} 关键词 x {len(search_cities)} 城市",
    )
    if _liepin_cookie_count() == 0:
        raise RuntimeError("猎聘尚未登录，请先在采集源状态中点击登录猎聘")

    try:
        from multi_company_scraper.scrapers.liepin_scraper import LiepinScraper
        from multi_company_scraper.models.company_config import CompanyConfig
    except ImportError as e:
        update_progress(task_id, f"导入失败: {e}")
        raise RuntimeError(f"猎聘依赖导入失败: {e}") from e

    scraper = LiepinScraper()
    company = CompanyConfig(
        name="猎聘",
        platform="liepin",
        base_url="https://www.liepin.com/",
        api_config={"search_params_file": "config/liepin_search_params.yaml"},
    )
    try:
        all_jobs = scraper.scrape(
            company,
            keywords=search_keywords,
            cities=search_cities,
            pages=pages,
        )
    except Exception as exc:
        update_progress(task_id, f"猎聘爬取失败: {exc}")
        raise RuntimeError(f"猎聘爬取失败: {exc}") from exc

    update_progress(task_id, f"爬取完成，共 {len(all_jobs)} 条，正在写入数据库...")
    results = _save_liepin_jobs(all_jobs, task_id)
    saved = sum(1 for r in results if r.status == "saved")
    failed = len(results) - saved
    update_progress(task_id, f"数据库写入: {saved} 成功, {failed} 失败")
    return saved


def _save_liepin_jobs(jobs, task_id: str) -> list[PersistenceResult]:
    """逐条事务写入完整 raw/provenance 字段 (task 02 final)."""
    results: list[PersistenceResult] = []
    for job in jobs:
        rid = str(getattr(job, 'job_id', '') or '').strip()
        platform = "liepin"
        if not rid:
            result = PersistenceResult(
                source_platform=platform,
                source_record_id="",
                status="failed",
                error_code="source_record_id_missing",
                error_message="Liepin job_id is required",
            )
            results.append(result)
            update_progress(task_id, result.error_message)
            continue

        conn = None
        cur = None
        try:
            # 严格解析 crawl_time
            try:
                utc_dt = parse_crawl_time(job.crawl_time)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"crawl_time invalid: {exc}") from exc
            db_crawl_time = utc_dt.replace(tzinfo=None)

            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO multi_company_jobs
                (company_name, platform, job_title, salary_min, salary_max,
                 experience, education, jd_text, jd_responsibility, jd_requirement,
                 skill_tags, location, source_url, source_platform, task_id,
                 source_record_id, raw_payload, raw_html, source_version,
                 crawl_time, text_canonicalization_version,
                 raw_text_status, raw_text_error,
                 benefits_raw, skills_raw, experience_raw, education_raw)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (getattr(job, 'company_name', '猎聘'), platform,
                 job.job_title, job.salary_min, job.salary_max,
                 job.experience or getattr(job, 'experience_raw', ''),
                 job.education or getattr(job, 'education_raw', ''),
                 job.jd_text,
                 job.jd_responsibility,
                 job.jd_requirement,
                 job.skill_tags,
                 job.city or '', job.source_url, job.source_platform, task_id,
                 rid,
                 json.dumps(job.raw_payload, ensure_ascii=False) if getattr(job, 'raw_payload', None) else None,
                 getattr(job, 'raw_html', '') or '',
                 getattr(job, 'source_version', '1') or '1',
                 db_crawl_time,
                 getattr(job, 'text_canonicalization_version', 'v1') or 'v1',
                 getattr(job, 'raw_text_status', '') or '',
                 getattr(job, 'raw_text_error', '') or '',
                 getattr(job, 'benefits_raw', '') or '',
                 getattr(job, 'skills_raw', '') or job.skill_tags,
                 getattr(job, 'experience_raw', '') or '',
                getattr(job, 'education_raw', '') or ''),
            )
            if job.raw_text_status == "completed":
                ensure_export_candidate_in_transaction(
                    conn,
                    job_data_to_envelope(job),
                    source_kind="liepin_job",
                    source_job_id=task_id,
                    task_id=task_id,
                )
            conn.commit()
            results.append(PersistenceResult(platform, rid, "saved"))
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception as rollback_exc:
                    update_progress(
                        task_id,
                        f"DB rollback failed [{platform}:{rid}]: {rollback_exc}",
                    )
            code = classify_persistence_error(exc)
            update_progress(task_id, f"DB write failed [{platform}:{rid}] {code}: {exc}")
            results.append(PersistenceResult(
                platform, rid, "failed", error_code=code, error_message=str(exc),
            ))
        finally:
            if cur is not None:
                cur.close()
            if conn is not None:
                conn.close()
    return results
