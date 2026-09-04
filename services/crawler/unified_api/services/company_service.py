"""多公司爬虫服务 — 包装 multi_company_scraper。"""
import json
from pathlib import Path
from typing import TYPE_CHECKING

from jobgraph_contracts.source_identity import parse_crawl_time
from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope

from ..database import get_conn
from ..offline_export.staging import ensure_export_candidate_in_transaction
from .persistence import PersistenceResult, classify_persistence_error
from .task_manager import update_progress

if TYPE_CHECKING:
    from multi_company_scraper.models.job_data import JobData
    from multi_company_scraper.scrapers.dispatcher import ScraperDispatcher

_CRAWLER_ROOT = Path(__file__).resolve().parents[2]
_COMPANIES_CONFIG_PATH = _CRAWLER_ROOT / "multi_company_scraper" / "config" / "companies.yaml"


def _load_mc_config():
    import yaml

    if not _COMPANIES_CONFIG_PATH.exists():
        raise FileNotFoundError(f"companies config not found: {_COMPANIES_CONFIG_PATH}")
    with _COMPANIES_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("companies", [])


def run_company_crawl(company_name: str = "all", platform: str = None,
                      task_id: str = "", user_id: int = 1) -> int:
    from multi_company_scraper.models.company_config import CompanyConfig
    from multi_company_scraper.collector import JobCollector

    companies_data = _load_mc_config()
    companies = [CompanyConfig.from_dict(c) for c in companies_data]

    if company_name != "all":
        companies = [c for c in companies if c.name == company_name]
    if platform:
        companies = [c for c in companies if c.platform == platform]

    if not companies:
        update_progress(task_id, "没有匹配的公司")
        return 0

    dispatcher = _setup_company_dispatcher(task_id)
    collector = JobCollector()
    total_jobs = 0

    for i, company in enumerate(companies):
        update_progress(task_id, f"[{i+1}/{len(companies)}] 正在爬取 {company.name}")
        try:
            jobs = dispatcher.scrape_company(company)
        except Exception as e:
            update_progress(task_id, f"{company.name} 爬取失败: {e}")
            continue

        cap = 200
        if len(jobs) > cap:
            jobs = jobs[:cap]
        collector.add_batch(jobs)
        total_jobs += len(jobs)

    update_progress(task_id, f"爬取完成，共 {total_jobs} 条，正在写入数据库...")

    results = _save_jobs_to_db(collector, task_id)
    saved = sum(1 for r in results if r.status == "saved")
    update_progress(task_id, f"数据库写入: {saved} 成功, {len(results) - saved} 失败")
    return saved


def _setup_company_dispatcher(task_id: str = "") -> "ScraperDispatcher":
    from multi_company_scraper.scrapers.dispatcher import ScraperDispatcher

    dispatcher = ScraperDispatcher()
    scraper_classes = []
    import_failures = []
    for mod_name, cls_name in [
        ("multi_company_scraper.scrapers.playwright_scraper", "PlaywrightScraper"),
        ("multi_company_scraper.scrapers.moka_scraper", "MokaScraper"),
        ("multi_company_scraper.scrapers.feishu_scraper", "FeishuScraper"),
        ("multi_company_scraper.scrapers.baidu_scraper", "BaiduScraper"),
        ("multi_company_scraper.scrapers.tencent_scraper", "TencentScraper"),
        ("multi_company_scraper.scrapers.netease_scraper", "NeteaseScraper"),
        ("multi_company_scraper.scrapers.zhiye_scraper", "ZhiyeScraper"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            scraper_classes.append(cls)
        except Exception as exc:
            failure = f"{cls_name}: {exc}"
            import_failures.append(failure)
            update_progress(task_id, f"Scraper 导入失败: {failure}")

    if not scraper_classes:
        raise RuntimeError(
            f"no company scrapers could be imported: {'; '.join(import_failures)}"
        )

    reg_failures = []
    registered_count = 0
    for cls in scraper_classes:
        try:
            dispatcher.register(cls())
            registered_count += 1
        except Exception as exc:
            failure = f"{cls.__name__}: {exc}"
            reg_failures.append(failure)
            update_progress(task_id, f"Scraper 注册失败: {failure}")
    if registered_count == 0:
        details = "; ".join(import_failures + reg_failures)
        raise RuntimeError(f"no company scrapers available: {details}")
    return dispatcher


def _save_jobs_to_db(collector, task_id: str) -> list[PersistenceResult]:
    """Per-row commit: successful rows persist even if a later row fails.

    Returns list[PersistenceResult] — one per job.
    """
    results: list[PersistenceResult] = []
    conn = None
    try:
        for job in collector._jobs:
            platform = getattr(job, 'source_platform', 'company')
            source_record_id = str(getattr(job, 'job_id', '') or '').strip()
            if not source_record_id:
                results.append(PersistenceResult(
                    source_platform=platform, source_record_id="",
                    status="failed", error_code="source_record_id_missing",
                    error_message="job_id is required",
                ))
                continue

            cur = None
            try:
                if conn is None:
                    conn = get_conn()
                try:
                    utc_dt = parse_crawl_time(job.crawl_time)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"crawl_time invalid: {exc}") from exc
                # MySQL DATETIME stores UTC without tzinfo.  Keep a datetime
                # object so the driver performs a lossless temporal binding.
                db_crawl_time = utc_dt.replace(tzinfo=None)

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
                    (job.company_name, platform,
                     job.job_title, job.salary_min, job.salary_max,
                     job.experience or job.experience_raw,
                     job.education or job.education_raw,
                     job.jd_text, job.jd_responsibility, job.jd_requirement,
                     job.skill_tags, job.city or '', job.source_url,
                     job.source_platform, task_id,
                     job.job_id,
                     json.dumps(job.raw_payload, ensure_ascii=False) if job.raw_payload else None,
                     job.raw_html or None,
                     job.source_version or '1',
                     db_crawl_time,
                     job.text_canonicalization_version or 'v1',
                     job.raw_text_status or '',
                     job.raw_text_error or '',
                     job.benefits_raw or '',
                     getattr(job, 'skills_raw', '') or job.skill_tags,
                     job.experience_raw or '',
                    job.education_raw or ''),
                )
                if job.raw_text_status == "completed":
                    ensure_export_candidate_in_transaction(
                        conn,
                        job_data_to_envelope(job),
                        source_kind="multi_company_job",
                        source_job_id=task_id,
                        task_id=task_id,
                    )
                conn.commit()
                results.append(PersistenceResult(
                    source_platform=platform, source_record_id=source_record_id,
                    status="saved",
                ))
            except Exception as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception as rollback_exc:
                        update_progress(
                            task_id,
                            f"DB rollback failed [{platform}:{source_record_id}]: {rollback_exc}",
                        )
                update_progress(task_id,
                    f"DB write failed [{platform}:{source_record_id}]: {exc}")
                results.append(PersistenceResult(
                    source_platform=platform, source_record_id=source_record_id,
                    status="failed",
                    error_code=classify_persistence_error(exc),
                    error_message=str(exc),
                ))
            finally:
                if cur is not None:
                    cur.close()
    finally:
        if conn is not None:
            conn.close()
    saved = sum(1 for r in results if r.status == "saved")
    failed = len(results) - saved
    update_progress(task_id, f"保存完成: {saved} 成功, {failed} 失败 (共 {len(results)})")
    return results


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def get_company_jobs(company_name: str | None = None, platform: str | None = None,
                     page: int = 1, page_size: int = 20) -> tuple[list, int]:
    conn = get_conn()
    cur = conn.cursor()
    where = []
    params = []
    if company_name:
        where.append("company_name = %s")
        params.append(company_name)
    if platform:
        where.append("platform = %s")
        params.append(platform)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) as cnt FROM multi_company_jobs {where_clause}", params)
    total = cur.fetchone()['cnt']

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT * FROM multi_company_jobs {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows, total


def get_company_job_by_id(job_id: int) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM multi_company_jobs WHERE id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def _row_to_job_data(row: dict) -> "JobData":
    from multi_company_scraper.models.job_data import JobData
    from datetime import timezone

    db_crawl = row.get("crawl_time")
    if db_crawl is None:
        crawl_time_str = ""
    elif hasattr(db_crawl, "tzinfo"):
        # MySQL DATETIME is naive UTC.  Aware values are normalized to UTC as
        # well, which keeps fake/test databases and future drivers consistent.
        if db_crawl.tzinfo is None:
            db_crawl = db_crawl.replace(tzinfo=timezone.utc)
        crawl_time_str = db_crawl.astimezone(timezone.utc).isoformat()
    else:
        crawl_time_str = parse_crawl_time(db_crawl).isoformat()

    return JobData(
        company_name=row.get("company_name", ""),
        job_title=row.get("job_title", ""),
        source_platform=row.get("source_platform", "company"),
        job_id=str(row.get("source_record_id") or ""),
        city=row.get("location", ""),
        source_url=row.get("source_url", ""),
        jd_text=row.get("jd_text", ""),
        benefits_raw=row.get("benefits_raw") or "",
        experience_raw=row.get("experience_raw") or "",
        education_raw=row.get("education_raw") or "",
        skills_raw=row.get("skills_raw") or "",
        raw_payload=json.loads(row.get("raw_payload") or "{}") if row.get("raw_payload") else {},
        raw_html=row.get("raw_html") or "",
        source_version=row.get("source_version") or "1",
        text_canonicalization_version=row.get("text_canonicalization_version") or "v1",
        raw_text_status=row.get("raw_text_status") or "",
        raw_text_error=row.get("raw_text_error") or "",
        crawl_time=crawl_time_str,
    )


def export_company_envelopes(
    company_name: str | None = None,
    platform: str | None = None,
    limit: int = 100,
) -> dict:
    """批量导出 Envelope — 包含所有状态记录的完整明细。

    不按 ``raw_text_status`` 预过滤。每条记录都会出现在 ``items`` 中，
    ``requested_count == exported_count + failed_count``。
    """
    from multi_company_scraper.adapters.crawler_jd_envelope import batch_to_envelopes

    conn = get_conn()
    cur = conn.cursor()
    where = []
    params = []
    if company_name:
        where.append("company_name = %s")
        params.append(company_name)
    if platform:
        where.append("platform = %s")
        params.append(platform)
    # No raw_text_status filter — all records are included
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"SELECT * FROM multi_company_jobs {where_clause} ORDER BY created_at DESC LIMIT %s",
        params + [limit],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    jobs = [_row_to_job_data(r) for r in rows]
    successes, failures = batch_to_envelopes(jobs)

    status_counts: dict[str, int] = {}
    for r in rows:
        s = (r.get("raw_text_status") or "").strip() or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "items": [{
            "source_platform": s.source_platform,
            "source_record_id": s.source_record_id,
            "status": "success",
            "envelope": s.model_dump(mode="json"),
        } for s in successes] + [f.to_dict() for f in failures],
        "requested_count": len(rows),
        "processed_count": len(successes) + len(failures),
        "exported_count": len(successes),
        "failed_count": len(failures),
        "status_counts": status_counts,
    }


def get_company_stats() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM multi_company_jobs")
    total = cur.fetchone()['cnt']

    cur.execute(
        "SELECT company_name as name, COUNT(*) as value FROM multi_company_jobs "
        "GROUP BY company_name ORDER BY value DESC LIMIT 20"
    )
    company_dist = cur.fetchall()

    cur.execute(
        "SELECT platform as name, COUNT(*) as value FROM multi_company_jobs "
        "GROUP BY platform ORDER BY value DESC"
    )
    platform_dist = cur.fetchall()

    cur.close()
    conn.close()
    return {
        "total_jobs": total,
        "company_distribution": company_dist,
        "platform_distribution": platform_dist,
    }


def get_company_list() -> list[dict]:
    companies = _load_mc_config()
    return [{"name": c["name"], "platform": c["platform"],
             "base_url": c.get("base_url", ""), "enabled": c.get("enabled", True)}
            for c in companies]
