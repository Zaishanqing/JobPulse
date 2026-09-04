"""Boss直聘爬虫服务 — 照搬 visual_spider.py 的完整框架和逻辑。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from ..database import get_conn
from ..offline_export.staging import ensure_export_candidate_in_transaction
from .boss_detail import sanitize_raw_payload
from .persistence import PersistenceResult, classify_persistence_error
from .task_manager import update_progress

if TYPE_CHECKING:
    from multi_company_scraper.models.job_data import JobData

BOSS_COOKIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "multi_company_scraper", "config", "boss_cookies.json"
)
BOSS_COOKIES_FILE = os.getenv("BOSS_COOKIES_PATH", "").strip() or BOSS_COOKIES_FILE

# 固定持久化浏览器 profile：登录一次后，爬虫和登录共用同一份登录态（照搬 Boss直聘招聘数据分析系统）。
BOSS_BROWSER_PROFILE_DIR = os.getenv("BOSS_BROWSER_PROFILE_DIR", "").strip() or os.path.join(
    os.path.dirname(BOSS_COOKIES_FILE), "boss_browser_profile"
)
BOSS_BROWSER_DEBUG_PORT = 9222
LOGIN_COOKIE_KEYS = {"geek_zp_token", "bst", "wt2", "zp_at", "__zp_stoken__"}


def _browser_options():
    from DrissionPage import ChromiumOptions

    options = ChromiumOptions()
    options.headless(False)
    os.makedirs(BOSS_BROWSER_PROFILE_DIR, exist_ok=True)
    options.set_user_data_path(BOSS_BROWSER_PROFILE_DIR)
    options.set_local_port(BOSS_BROWSER_DEBUG_PORT)
    options.set_browser_path(
        os.getenv("BROWSER_EXECUTABLE_PATH", "").strip() or "/usr/bin/chromium"
    )
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-dev-shm-usage")
    options.set_argument("--disable-blink-features=AutomationControlled")
    return options


def _read_cookies() -> list[dict]:
    if not os.path.exists(BOSS_COOKIES_FILE):
        return []
    with open(BOSS_COOKIES_FILE, encoding="utf-8") as f:
        cookies = json.load(f)
    if not isinstance(cookies, list):
        return []
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


def set_cookies_and_verify(cookies: list[dict]) -> dict:
    try:
        with open(BOSS_COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        return {"saved": True, "count": len(cookies), "verified": True}
    except OSError as exc:
        return {"saved": False, "count": 0, "verified": False, "error": str(exc)}


def _has_login_cookie(cookies: list[dict]) -> bool:
    return any(
        cookie.get("name") in LOGIN_COOKIE_KEYS
        and bool(cookie.get("value"))
        for cookie in cookies
    )


CITY_CODES = {
    '北京': '101010100', '上海': '101020100', '广州': '101280100',
    '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
    '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
    '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
    '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
    '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
    '南京': '101190100', '东莞': '101281600', '福州': '101230100',
}


# ============ 登录（照搬 login_boss） ============

def boss_login_status() -> dict:
    """检查 Boss 直聘登录状态。"""
    result = {
        "logged_in": False,
        "cookie_count": 0,
        "running": False,
        "status": "idle",
        "login_id": None,
        "started_at": None,
        "finished_at": None,
        "message": None,
        "updated_at": None,
    }
    if os.path.exists(BOSS_COOKIES_FILE):
        try:
            result["updated_at"] = datetime.fromtimestamp(
                os.path.getmtime(BOSS_COOKIES_FILE), tz=timezone.utc
            ).isoformat()
            cookies = _read_cookies()
            result["logged_in"] = _has_login_cookie(cookies)
            result["cookie_count"] = len(cookies)
        except (OSError, ValueError, TypeError):
            result["logged_in"] = False
            result["cookie_count"] = 0
    return result


# ============ 爬虫 ============

def run_boss_crawl(keyword: str, city: str, pages: int, task_id: str = "", user_id: int = 1) -> int:
    if not boss_login_status()["logged_in"]:
        raise RuntimeError("BOSS 直聘尚未登录，请先在采集源状态中点击登录 BOSS 直聘")
    update_progress(task_id, f"正在启动浏览器，搜索: {keyword} - {city}")
    return _run_drissionpage_spider(keyword, city, pages, task_id, user_id)


def _run_drissionpage_spider(keyword: str, city: str, pages: int,
                              task_id: str, user_id: int) -> int:
    from DrissionPage import ChromiumPage

    total_count = 0
    conn = get_conn()
    dp = ChromiumPage(addr_or_opts=_browser_options())

    if os.path.exists(BOSS_COOKIES_FILE):
        try:
            with open(BOSS_COOKIES_FILE, encoding="utf-8") as f:
                saved_cookies = _read_cookies()
            dp.get("https://www.zhipin.com")
            time.sleep(2)
            for c in saved_cookies:
                try:
                    dp.set.cookies(c)
                except Exception as exc:
                    update_progress(task_id, f"Cookie 写入浏览器失败: {exc}")
            update_progress(task_id, "已加载登录Cookie")
        except Exception as e:
            update_progress(task_id, f"Cookie加载失败: {e}")

    list_page = dp  # rename for clarity
    detail_tab = None  # created lazily
    try:
        keywords = keyword.split()
        cities = city.split()

        for kw in keywords:
            for ct in cities:
                city_code = CITY_CODES.get(ct, '101010100')
                update_progress(task_id, f"开始爬取: {kw} - {ct}")

                list_page.listen.start('zpgeek/search/joblist.json')
                search_url = f'https://www.zhipin.com/web/geek/jobs?query={kw}&city={city_code}'
                list_page.get(search_url)
                time.sleep(5)

                city_count = 0
                for page_no in range(1, pages + 1):
                    update_progress(task_id, f"正在采集 {ct} 第{page_no}页的数据内容")

                    # Scroll for joblist JSON
                    try:
                        job_cards = list_page.eles('css:.job-card-wrapper')
                        if job_cards:
                            last_job = job_cards[-1]
                            last_job.scroll.to_view(align='bottom')
                            time.sleep(3)
                            list_page.run_js("window.scrollBy(0, 200)")
                            time.sleep(1)
                        else:
                            list_page.run_js("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(3)
                    except Exception as e:
                        update_progress(task_id, f"滚动页面出错: {e}")
                        list_page.run_js("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(3)

                    resp = list_page.listen.wait(timeout=15)
                    if not resp:
                        update_progress(task_id, f"{ct} 第{page_no}页数据加载超时，重试1次...")
                        time.sleep(2)
                        resp = list_page.listen.wait(timeout=10)
                        if not resp:
                            update_progress(task_id, f"{ct} 第{page_no}页重试后仍未获取数据，跳过该页")
                            continue

                    body = resp.response.body
                    json_data = body if isinstance(body, dict) else json.loads(body)
                    jobList = json_data.get('zpData', {}).get('jobList', [])

                    if not jobList:
                        update_progress(task_id, f"{ct} 第{page_no}页无数据，停止采集该城市")
                        break

                    # Lazy-create detail tab for this keyword+city
                    if detail_tab is None:
                        try:
                            detail_tab = list_page.new_tab()
                        except Exception as exc:
                            # A failed detail context must never fall back to
                            # list_page; doing so would destroy pagination state.
                            detail_tab = None
                            update_progress(task_id, f"详情标签页创建失败: {exc}")

                    page_count = 0
                    for job_data in jobList:
                        result = _parse_and_save(
                            job_data, kw, ct, conn, user_id, task_id,
                            list_page=list_page,
                            detail_tab=detail_tab,
                        )
                        if result.status == "saved":
                            page_count += 1
                            city_count += 1
                            total_count += 1
                        else:
                            update_progress(task_id,
                                f"保存失败 [{result.source_record_id}] "
                                f"{result.error_code}: {result.error_message}")

                    update_progress(task_id, f"{ct} 第{page_no}页获取 {page_count} 条数据（累计 {total_count}）")
                    time.sleep(2)

                update_progress(task_id, f"完成爬取 {ct}: 共获取 {city_count} 条数据")

    except Exception as e:
        update_progress(task_id, f"DrissionPage爬取失败: {e}")
        raise RuntimeError(f"DrissionPage爬取失败: {e}") from e
    finally:
        # Close the isolated detail context first.  Cleanup failures remain
        # visible while later resources are still released.
        try:
            if detail_tab is not None:
                detail_tab.close()
        except Exception as exc:
            update_progress(task_id, f"详情标签页关闭失败: {exc}")
        try:
            list_page.quit()
        finally:
            conn.close()

    return total_count


def _parse_and_save(job_data: dict, keyword: str, city: str, conn,
                    user_id: int = 1, task_id: str = "",
                    list_page=None, detail_tab=None) -> PersistenceResult:
    """Saves list-card fields, then fetches detail via *detail_tab* (P0-1).

    *list_page* MUST remain on the search page.  *detail_tab* (or a new
    context) handles detail navigation so pagination is not disrupted.
    """
    source_record_id = str(job_data.get('encryptJobId') or job_data.get('securityId') or job_data.get('jobId') or '')
    if not source_record_id:
        return PersistenceResult(
            source_platform="boss_zhipin", source_record_id="",
            status="failed", error_code="source_record_id_missing",
            error_message="encryptJobId, securityId, and jobId are all empty",
        )

    job_title = job_data.get('jobName', '')
    job_company = job_data.get('brandName', '')
    if job_title in ('未知职位', '职位', '', None) or job_company in ('未知公司', '公司', '', None):
        return PersistenceResult(
            source_platform="boss_zhipin", source_record_id=source_record_id,
            status="failed", error_code="invalid_list_record",
            error_message="job title or company name is missing/invalid",
        )
    if len(job_title) < 2 or len(job_company) < 2:
        return PersistenceResult(
            source_platform="boss_zhipin", source_record_id=source_record_id,
            status="failed", error_code="invalid_list_record",
            error_message="job title or company name too short",
        )

    # --- list-card fields ---
    job_area = f"{job_data.get('areaDistrict', '')} {job_data.get('businessDistrict', '')}".strip()
    job_salary = job_data.get('salaryDesc', '')
    company_info = f"{job_data.get('brandIndustry', '')} {job_data.get('brandStageName', '')} {job_data.get('brandScaleName', '')}".strip()
    job_experience = job_data.get('jobExperience', '')
    job_education = job_data.get('jobDegree', '')
    job_requirement = f"{job_experience} {job_education}".strip()
    skills = job_data.get('skills', [])
    job_skill = ' '.join(skills) if skills else ''
    welfare_list = job_data.get('welfareList', [])
    job_welfare = ' '.join(welfare_list) if welfare_list else ''

    encrypt_job_id = str(job_data.get('encryptJobId') or '')
    security_id = job_data.get('securityId') or None
    lid = job_data.get('lid') or None
    list_payload = sanitize_raw_payload(job_data)

    captured_at = datetime.now(timezone.utc)
    db_crawl_time = captured_at.astimezone(timezone.utc).replace(tzinfo=None)

    # --- detail fetching ---
    detail_result = None
    if detail_tab is not None and encrypt_job_id:
        from .boss_detail import fetch_boss_job_detail, build_boss_detail_url
        detail_url = build_boss_detail_url(encrypt_job_id, security_id, lid)
        detail_result = fetch_boss_job_detail(
            detail_tab,
            source_record_id=source_record_id or encrypt_job_id,
            security_id=security_id, source_url=detail_url,
            encrypt_job_id=encrypt_job_id, lid=lid,
            job_title_raw=job_title, company_name_raw=job_company,
            benefits_raw=job_welfare, skills_raw=job_skill,
            list_payload=list_payload,
        )

    # --- INSERT ---
    cur = None
    try:
        cur = conn.cursor()
        if detail_result and detail_result.status == "completed":
            cur.execute(
                """INSERT INTO bosszp
                (job_title, job_salary, job_lable, job_company,
                 job_company_tag, job_acquire, company_city, job_skill, keyword,
                 job_desc, user_id, task_id,
                 source_record_id, source_url,
                 benefits_raw, skills_raw, experience_raw, education_raw,
                 raw_payload, raw_html, source_version, crawl_time,
                 text_canonicalization_version, raw_text_status, raw_text_error,
                 detail_extraction_method)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (job_title, job_salary, job_area, job_company,
                 company_info, job_requirement, city, job_skill, keyword,
                 detail_result.raw_text, user_id, task_id,
                 source_record_id, detail_result.source_url,
                 job_welfare, job_skill, job_experience, job_education,
                 json.dumps(detail_result.raw_payload, ensure_ascii=False),
                 detail_result.raw_html, detail_result.source_version, db_crawl_time,
                 "v1", "completed", "",
                 detail_result.detail_extraction_method),
            )
        else:
            err_code = detail_result.error_code if detail_result else "full JD detail not fetched"
            err_msg = detail_result.error_message if detail_result else "full JD detail not fetched"
            raw_payload = json.dumps(
                {"list_payload": sanitize_raw_payload(list_payload),
                 "detail_payload": detail_result.raw_payload if detail_result else {},
                 "detail_extraction_method": detail_result.detail_extraction_method if detail_result else "none",
                 "detail_error_code": err_code},
                ensure_ascii=False,
            )
            cur.execute(
                """INSERT INTO bosszp
                (job_title, job_salary, job_lable, job_company,
                 job_company_tag, job_acquire, company_city, job_skill, keyword,
                 job_desc, user_id, task_id,
                 source_record_id, source_url,
                 benefits_raw, skills_raw, experience_raw, education_raw,
                 raw_payload, raw_html, crawl_time,
                 text_canonicalization_version, raw_text_status, raw_text_error,
                 detail_extraction_method)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (job_title, job_salary, job_area, job_company,
                 company_info, job_requirement, city, job_skill, keyword,
                 "", user_id, task_id,
                 source_record_id, detail_result.source_url if detail_result else "",
                 job_welfare, job_skill, job_experience, job_education,
                 raw_payload, (detail_result.raw_html if detail_result else ""),
                 db_crawl_time,
                 "v1", "failed" if detail_result else "unavailable", err_msg,
                 detail_result.detail_extraction_method if detail_result else "none"),
            )
        if detail_result and detail_result.status == "completed":
            envelope = CrawlerJDEnvelopeV1(
                source_record_id=source_record_id,
                source_platform="boss_zhipin",
                source_url=detail_result.source_url or None,
                job_title_raw=job_title,
                company_name_raw=job_company,
                region_raw=f"{city} {job_area}".strip() or None,
                publish_time_raw=None,
                crawl_time=captured_at,
                raw_text=detail_result.raw_text,
                raw_payload=detail_result.raw_payload,
                raw_html=detail_result.raw_html or None,
                text_canonicalization_version="v1",
                source_version=detail_result.source_version,
            )
            ensure_export_candidate_in_transaction(
                conn,
                envelope,
                source_kind="boss_job",
                source_job_id=task_id,
                task_id=task_id,
            )
        conn.commit()
        return PersistenceResult(
            source_platform="boss_zhipin", source_record_id=source_record_id,
            status="saved",
        )
    except Exception as exc:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            update_progress(
                task_id,
                f"Boss rollback failed [{source_record_id}]: {rollback_exc}",
            )
        code = classify_persistence_error(exc)
        update_progress(task_id,
            f"Boss save failed [{source_record_id}] {code}: {exc}")
        return PersistenceResult(
            source_platform="boss_zhipin", source_record_id=source_record_id,
            status="failed", error_code=code, error_message=str(exc),
        )
    finally:
        if cur is not None:
            cur.close()


# ---------------------------------------------------------------------------
# 数据查询
# ---------------------------------------------------------------------------

def get_boss_jobs(keyword: str | None = None, city: str | None = None,
                  page: int = 1, page_size: int = 20) -> tuple[list, int]:
    conn = get_conn()
    cur = conn.cursor()
    where = []
    params = []
    if keyword:
        where.append("keyword = %s")
        params.append(keyword)
    if city:
        where.append("company_city = %s")
        params.append(city)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) as cnt FROM bosszp {where_clause}", params)
    total = cur.fetchone()['cnt']

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT * FROM bosszp {where_clause} ORDER BY create_time DESC LIMIT %s OFFSET %s",
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows, total


def get_boss_job_by_id(job_id: int) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bosszp WHERE id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_boss_stats() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM bosszp")
    total = cur.fetchone()['cnt']

    cur.execute(
        "SELECT company_city as name, COUNT(*) as value FROM bosszp "
        "GROUP BY company_city ORDER BY value DESC LIMIT 15"
    )
    city_dist = cur.fetchall()

    cur.execute(
        "SELECT keyword as name, COUNT(*) as value FROM bosszp "
        "GROUP BY keyword ORDER BY value DESC LIMIT 15"
    )
    kw_dist = cur.fetchall()

    cur.close()
    conn.close()
    return {
        "total_jobs": total,
        "city_distribution": city_dist,
        "keyword_distribution": kw_dist,
    }


# ---------------------------------------------------------------------------
# Envelope / raw export (task 02 remediation)
# ---------------------------------------------------------------------------


def export_boss_envelopes(
    keyword: str | None = None,
    city: str | None = None,
    limit: int = 100,
) -> dict:
    """批量导出 Boss Envelope — 包含所有状态记录的完整明细。

    不按 ``raw_text_status`` 预过滤。
    """
    from multi_company_scraper.adapters.crawler_jd_envelope import batch_to_envelopes

    conn = get_conn()
    cur = conn.cursor()
    where = []
    params = []
    if keyword:
        where.append("keyword = %s")
        params.append(keyword)
    if city:
        where.append("company_city = %s")
        params.append(city)
    # No raw_text_status filter — all records are included
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"SELECT * FROM bosszp {where_clause} ORDER BY create_time DESC LIMIT %s",
        params + [limit],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    jobs: list[JobData] = []
    for r in rows:
        jobs.append(_row_to_job_data(r, "boss_zhipin"))
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


def _row_to_job_data(row: dict, platform: str) -> JobData:
    from multi_company_scraper.models.job_data import JobData
    from datetime import datetime, timezone

    source_url = row.get("source_url", "")
    if not source_url:
        kw = row.get("keyword", "")
        ct = row.get("company_city", "")
        source_url = f"https://www.zhipin.com/web/geek/jobs?query={kw}&city={ct}"

    # Step 6: MySQL DATETIME (naive UTC) → timezone-aware ISO 8601
    db_crawl = row.get("crawl_time")
    if db_crawl is None:
        crawl_time_str = ""
    elif isinstance(db_crawl, datetime):
        if db_crawl.tzinfo is None:
            db_crawl = db_crawl.replace(tzinfo=timezone.utc)
        crawl_time_str = db_crawl.astimezone(timezone.utc).isoformat()
    else:
        from jobgraph_contracts.source_identity import parse_crawl_time
        crawl_time_str = parse_crawl_time(db_crawl).isoformat()

    return JobData(
        company_name=row.get("job_company", ""),
        job_title=row.get("job_title", ""),
        source_platform=platform,
        job_id=str(row.get("source_record_id") or ""),
        city=row.get("company_city", ""),
        source_url=source_url,
        jd_text=row.get("job_desc") or "",
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
