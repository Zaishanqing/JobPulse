"""Liepin (猎聘) recruitment platform scraper.

Uses Playwright browser automation to handle login, CSRF tokens, and
anti-scraping measures.  Searches by keyword x city, intercepts the
JSON search API, and maps results to JobData via the existing Normalizer.
"""

import json
import os
import random
import time
from pathlib import Path
from urllib.parse import urlencode

import yaml
from loguru import logger
from playwright.sync_api import sync_playwright, Page, BrowserContext

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.playwright_scraper import PlaywrightScraper
from multi_company_scraper.normalizer import Normalizer


def _resolve_cookie_file() -> Path:
    configured = os.getenv("LIEPIN_COOKIES_FILE", "").strip()
    if configured:
        return Path(configured)
    return (
        Path(__file__).resolve().parent.parent
        / "config"
        / "liepin_cookies.local.json"
    )


class LiepinScraper(PlaywrightScraper):
    """猎聘网招聘信息爬虫.

    Inherits API-interception helpers from PlaywrightScraper:
      - _find_job_api_response()
      - _score_job_response() / _score_job_items()
      - _extract_total_from_body()
      - _paginate_direct() / _detect_api_pattern()
    """

    name = "liepin"
    SEARCH_API_URL = (
        "https://api-c.liepin.com/api/"
        "com.liepin.searchfront4c.pc-search-job"
    )
    SEARCH_PAGE_URL = "https://www.liepin.com/zhaopin/"
    MAX_JOBS_TOTAL = 10000
    MAX_PAGES_PER_SEARCH = 100
    PAGE_SIZE = 40
    BROWSER_RESTART_EVERY = 30  # restart browser every N searches to avoid detection
    HEADLESS = False  # The integrated crawler exposes this browser through noVNC.
    _cookie_file: Path | None = None  # resolved lazily from LIEPIN_COOKIES_FILE env var

    @property
    def COOKIE_FILE(self) -> Path:
        if self._cookie_file is None:
            self._cookie_file = _resolve_cookie_file()
        return self._cookie_file

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "liepin"

    # ------------------------------------------------------------------
    # Override _find_job_api_response for Liepin's nested JSON structure
    # ------------------------------------------------------------------

    def _find_job_api_response(self, api_responses: list[dict]) -> dict | None:
        """Override: match Liepin's specific API URL and nested job card structure.

        Liepin's cards have {"comp": {...}, "job": {...}} — neither top-level
        key matches any _JOB_INDICATOR_KEYS, so the parent's generic scoring
        never detects the response.  We directly look for the known API URL
        and navigate to data.data.jobCardList.
        """
        for resp in api_responses:
            body = resp.get("body", {})
            if not isinstance(body, dict):
                continue
            # Check for Liepin's specific data path
            try:
                job_list = body.get("data", {}).get("data", {}).get("jobCardList")
            except AttributeError:
                continue
            if isinstance(job_list, list) and len(job_list) > 0:
                return {"url": resp["url"], "jobs_list": job_list, "body": body}
        # Fall back to parent's generic detection
        return super()._find_job_api_response(api_responses)

    def scrape(
        self,
        company: CompanyConfig,
        keywords: list[str] | None = None,
        cities: list[str] | None = None,
        pages: int | None = None,
    ) -> list[JobData]:
        """Scrape Liepin job listings by keyword and configured city."""
        search_config = company.api_config.get("search_params_file", "")
        params = self._load_search_params(search_config)
        keywords = params["keywords"] if keywords is None else keywords
        city_codes = params.get("cities") or {}
        city_names = list(city_codes) if cities is None else list(cities)
        if not city_names:
            raise ValueError("猎聘搜索至少需要一个城市")
        unknown_cities = [city for city in city_names if city not in city_codes]
        if unknown_cities:
            raise ValueError(f"猎聘不支持城市: {', '.join(unknown_cities)}")

        all_jobs: list[JobData] = []
        seen_ids: set[str] = set()
        total_searches = len(keywords) * len(city_names)
        combo_idx = 0
        searches_since_restart = 0

        with sync_playwright() as p:

            def _launch_browser():
                """Launch browser with anti-detection args."""
                return self._launch_chromium(
                    p.chromium,
                    headless=self.HEADLESS,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

            def _new_context(browser):
                return browser.new_context(
                    user_agent=random.choice(self._get_user_agents()),
                    viewport={"width": 1920, "height": 1080},
                )

            browser = _launch_browser()
            context = _new_context(browser)
            page = context.new_page()

            # -- capture XHR/JSON responses --
            api_responses: list[dict] = []

            def _on_response(response):
                if response.status != 200:
                    return
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct or "text/plain" in ct:
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            api_responses.append({"url": response.url, "body": body})
                    except Exception as exc:
                        logger.debug(f"Liepin response was not JSON: {exc}")

            page.on("response", _on_response)

            try:
                logged_in = self._ensure_login(page, context)
                if not logged_in:
                    logger.warning(
                        "Liepin: proceeding without login — results may be limited"
                    )

                for keyword in keywords:
                    if len(all_jobs) >= self.MAX_JOBS_TOTAL:
                        logger.info(
                            f"Liepin: hit global cap of {self.MAX_JOBS_TOTAL} jobs, stopping"
                        )
                        break

                    for city in city_names:
                        if len(all_jobs) >= self.MAX_JOBS_TOTAL:
                            break

                        # Restart browser periodically to avoid detection.
                        if searches_since_restart >= self.BROWSER_RESTART_EVERY:
                            logger.info(
                                f"Liepin: restarting browser after "
                                f"{searches_since_restart} searches"
                            )
                            browser.close()
                            browser = _launch_browser()
                            context = _new_context(browser)
                            page = context.new_page()
                            page.on("response", _on_response)
                            self._load_cookies(context)
                            searches_since_restart = 0

                        combo_idx += 1
                        searches_since_restart += 1
                        logger.info(
                            f"Liepin [{combo_idx}/{total_searches}] "
                            f"searching: {keyword} ({city})"
                        )

                        api_responses.clear()
                        jobs = self._do_search(
                            page,
                            keyword,
                            api_responses,
                            company.name,
                            city_code=str(city_codes[city]),
                            max_pages=pages,
                        )

                        new_count = 0
                        for job in jobs:
                            jid = job.job_id
                            if jid and jid in seen_ids:
                                continue
                            seen_ids.add(jid)
                            all_jobs.append(job)
                            new_count += 1

                        logger.info(
                            f"  -> {new_count} new jobs "
                            f"(total: {len(all_jobs)})"
                        )

                        if len(all_jobs) >= self.MAX_JOBS_TOTAL:
                            break

                        # Rate limiting between searches (10-20s to avoid detection)
                        delay = random.uniform(10.0, 20.0)
                        logger.debug(f"  sleeping {delay:.1f}s...")
                        time.sleep(delay)

            finally:
                browser.close()

        logger.info(f"Liepin scrape complete: {len(all_jobs)} unique jobs total")
        return all_jobs

    def _load_search_params(self, config_rel_path: str = "") -> dict:
        default = (
            Path(__file__).resolve().parent.parent
            / "config" / "liepin_search_params.yaml"
        )
        if config_rel_path:
            config_path = (
                Path(__file__).resolve().parent.parent / config_rel_path
            )
        else:
            config_path = default
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _save_cookies(self, context: BrowserContext) -> None:
        cookies = context.cookies()
        self.COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False))

    def _load_cookies(self, context: BrowserContext) -> bool:
        if not self.COOKIE_FILE.exists():
            return False
        try:
            cookies = json.loads(self.COOKIE_FILE.read_text())
            context.add_cookies(cookies)
            return True
        except Exception:
            return False

    def _ensure_login(self, page: Page, context: BrowserContext) -> bool:
        """Restore saved cookies or prompt user to log in manually.

        1. Try loading saved cookies -> navigate to homepage -> check if logged in.
        2. If cookies are missing or expired, prompt user to scan QR code.
        3. Wait for login to complete (detect user menu / cookie change).
        4. Save cookies for future runs.
        """
        cookie_loaded = self._load_cookies(context)

        page.goto("https://www.liepin.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        if cookie_loaded and self._is_logged_in(page):
            logger.info("Liepin: restored login session from saved cookies")
            return True

        if cookie_loaded:
            logger.warning("Liepin: saved cookies expired, need re-login")
            # Clear expired cookie file
            self.COOKIE_FILE.unlink(missing_ok=True)

        # Prompt user to log in
        logger.info("Liepin: please scan QR code to login in the browser window...")
        logger.info("Liepin: waiting for login (timeout: 120s)...")

        # Wait for login -- check every 2s for up to 120s
        for _ in range(60):
            time.sleep(2)
            if self._is_logged_in(page):
                logger.info("Liepin: login detected, saving cookies")
                self._save_cookies(context)
                return True

        raise RuntimeError("猎聘登录超时，请在登录窗口完成登录后重试")

    def _is_logged_in(self, page: Page) -> bool:
        """Check if the current page shows a logged-in state."""
        try:
            # Use stable authenticated-state markers; generic class names such as
            # ``user`` also occur in the anonymous login page.
            user_el = page.query_selector(
                ".user-menu, .user-info, .user-avatar, "
                ".header-login-btn.is-login, .nav-login-user, "
                ".login-status, .personal-center, "
                "[data-testid='user-menu'], [data-testid='header-user']"
            )
            if user_el:
                return True

            # Fallback: check for login button (if present, user is NOT logged in)
            login_btn = page.query_selector(
                ".login-btn:not([style*='none']), "
                ".header-login-btn:not(.is-login), "
                "[class*='unlogin']"
            )
            if login_btn:
                return False

            # Analytics cookies are present before login, but auth cookies carry
            # a login/token/session marker.
            cookies = page.context.cookies()
            for c in cookies:
                name = c.get("name", "").lower()
                if (
                    any(marker in name for marker in ("login", "token", "session", "auth"))
                    and c.get("value")
                ):
                    return True

            return False
        except Exception:
            return False

    def _do_search(
        self, page: Page, keyword: str,
        api_responses: list, company_name: str,
        *, city_code: str | None = None, max_pages: int | None = None,
    ) -> list[JobData]:
        """Execute one keyword/city search and return parsed jobs."""
        jobs: list[JobData] = []

        query = {"key": keyword}
        if city_code:
            query["dqs"] = city_code
        search_url = f"{self.SEARCH_PAGE_URL}?{urlencode(query)}"

        # Retry page.goto up to 3 times with exponential backoff
        # (handles net::ERR_NETWORK_CHANGED and other anti-bot blocks)
        for attempt in range(3):
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception as e:
                if attempt < 2:
                    wait = (2 ** attempt) * 10  # 10s, 20s backoff
                    logger.debug(
                        f"page.goto failed (attempt {attempt + 1}/3): {e}, "
                        f"retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    raise

        page.wait_for_timeout(8000)

        # Retry once with extra wait if no API response detected
        api_info = self._find_job_api_response(api_responses)
        if not api_info:
            page.wait_for_timeout(8000)
            api_info = self._find_job_api_response(api_responses)

        if not api_info:
            logger.warning(f"No job API response for keyword={keyword}")
            return jobs

        # Parse first page
        new_jobs, first_page_ids = self._parse_api_response(
            api_info, company_name
        )
        jobs.extend(new_jobs)
        seen_in_search: set = set(first_page_ids)

        total_expected = self._extract_total_from_body(api_info["body"])
        if total_expected:
            logger.debug(f"  API reports {total_expected} total results")

        # Paginate
        page_limit = min(
            max_pages if max_pages is not None else self.MAX_PAGES_PER_SEARCH,
            self.MAX_PAGES_PER_SEARCH,
        )
        for pg in range(1, page_limit):
            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                break
            if total_expected and len(jobs) >= total_expected:
                break

            api_responses.clear()
            success = self._paginate_liepin(
                page, pg, api_responses, keyword=keyword
            )
            if not success:
                break

            api_info = self._find_job_api_response(api_responses)
            if not api_info:
                break

            new_jobs, page_ids = self._parse_api_response(
                api_info, company_name
            )

            # Stop if all jobs on this page are already seen
            if page_ids.issubset(seen_in_search):
                logger.debug(f"  All {len(page_ids)} jobs already seen, end of results")
                break
            seen_in_search.update(page_ids)

            if not new_jobs:
                break

            jobs.extend(new_jobs)
            logger.debug(
                f"  Page {pg + 1}: {len(new_jobs)} jobs "
                f"(search total: {len(jobs)})"
            )

            time.sleep(random.uniform(1.0, 2.0))

        # Enrich jobs with full JD text from detail pages
        if jobs and self.ENRICH_JD:
            self._enrich_jd_details(page, jobs, company_name)

        return jobs

    # ------------------------------------------------------------------
    # JD detail enrichment
    # ------------------------------------------------------------------

    ENRICH_JD = True          # set False to skip detail-page fetching
    MAX_DETAIL_PER_SEARCH = 20  # max detail pages to fetch per search

    def _enrich_jd_details(
        self, page: Page, jobs: list[JobData], company_name: str,
    ) -> None:
        """Fetch full JD text from each job's detail page.

        Task 02 remediation: uses DOM container selectors (not semantic
        markers) to locate JD content.  Sets ``raw_text_status`` to
        ``completed`` on success, ``failed`` otherwise.
        """
        to_enrich = [
            j for j in jobs
            if j.source_url and j.raw_text_status != "completed"
        ][:self.MAX_DETAIL_PER_SEARCH]

        if not to_enrich:
            return

        logger.info(
            f"  Fetching JD details for {len(to_enrich)} jobs "
            f"(of {len(jobs)} total)"
        )

        enriched = 0
        for job in to_enrich:
            try:
                page.goto(
                    job.source_url,
                    wait_until="networkidle",
                    timeout=30000,
                )
                page.wait_for_timeout(3000)

                jd_text = ""
                raw_html = ""
                try:
                    raw_html = page.content()

                    # Try CSS selectors first (deterministic, no semantic keywords)
                    for sel in (
                        ".job-detail-content",
                        ".job-description",
                        ".jd-content",
                        ".job-intro",
                        "[class*='job-detail']",
                        "[class*='jd-content']",
                    ):
                        el = page.query_selector(sel)
                        if el:
                            jd_text = el.inner_text().strip()
                            if len(jd_text) > 50:
                                break
                            jd_text = ""

                    # Fallback: use body text with DOM-anchored truncation
                    if not jd_text:
                        body_text = page.inner_text("body")
                        import re as _re
                        body_text = _re.sub(r'\n{3,}', '\n\n', body_text)
                        # Truncate at common page-footer noise (not semantic JD markers)
                        for noise in ("公司信息", "数据来源", "推荐企业",
                                      "相关推荐", "猜你喜欢", "热门城市"):
                            idx = body_text.find(noise, 200)
                            if idx > 0:
                                body_text = body_text[:idx]
                        lines = body_text.split('\n')
                        kept = []
                        for line in lines:
                            s = line.strip()
                            if not s or s in ('聊一聊', '收藏', '举报', '投递', '分享', '微信分享'):
                                continue
                            if '在线' in s and '已认证' in s:
                                continue
                            kept.append(s)
                        jd_text = '\n'.join(kept)

                except Exception as exc:
                    logger.debug(f"Liepin detail DOM extraction failed: {exc}")

                if jd_text and len(jd_text) > 50:
                    from multi_company_scraper.normalizer import compute_raw_text
                    job.jd_text = compute_raw_text(jd_text)
                    job.raw_html = raw_html
                    job.raw_text_status = "completed"
                    job.raw_text_error = ""
                    enriched += 1
                else:
                    job.raw_text_status = "failed"
                    job.raw_text_error = "detail page JD content too short or empty"

                time.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                job.raw_text_status = "failed"
                job.raw_text_error = f"detail fetch error: {e}"

        if enriched:
            logger.info(f"  Enriched {enriched}/{len(to_enrich)} jobs with JD text")

    def _paginate_liepin(
        self, page: Page, page_idx: int, api_responses: list,
        keyword: str = "",
    ) -> bool:
        """Fetch a subsequent page of results via direct API call.

        Uses page.evaluate(fetch(...)) so the browser's cookie/CSRF state
        is automatically attached to the request.

        Returns True if new data was captured.
        """
        body_obj = {
            "data": {
                "mainSearchPcConditionForm": {
                    "currentPage": page_idx,
                    "pageSize": self.PAGE_SIZE,
                    "key": keyword,
                },
                "passThroughForm": {
                    "scene": "input",
                },
            }
        }
        body_str = json.dumps(body_obj, ensure_ascii=False)

        js_code = f"""
            async () => {{
                const resp = await fetch({json.dumps(self.SEARCH_API_URL)}, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Client-Type': 'web',
                        'X-Fscp-Std-Info': '{{"client_id": "40108"}}',
                        'X-Fscp-Version': '1.1',
                    }},
                    body: {json.dumps(body_str)},
                }});
                const data = await resp.json();
                return data;
            }}
        """
        try:
            result = page.evaluate(js_code)
            if result is not None and isinstance(result, dict):
                api_responses.append({
                    "url": self.SEARCH_API_URL,
                    "body": result,
                })
                return True
        except Exception as e:
            logger.debug(f"Liepin pagination error on page {page_idx + 1}: {e}")

        return False

    @staticmethod
    def _get_user_agents() -> list[str]:
        return [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        ]

    def _extract_raw_job(self, api_job: dict) -> dict:
        """Map Liepin API job card to canonical raw dict for Normalizer.

        Task 02: preserves the complete ``api_job`` as ``raw_payload``.
        Liepin API nests data: jobCard = {"comp": {...}, "job": {...}}
        """
        comp = api_job.get("comp", {})
        job = api_job.get("job", {})

        # source_url — try many possible field names
        source_url = ""
        for k in ("jobDetailUrl", "jobUrl", "detailUrl", "url", "link",
                  "shareUrl", "pcJobDetailUrl", "mobileUrl"):
            v = job.get(k, "")
            if v and isinstance(v, str) and v.startswith("http"):
                source_url = v
                break
        if not source_url:
            jid = job.get("jobId", "")
            if jid:
                source_url = f"https://www.liepin.com/job/{jid}.shtml"

        raw = {
            "job_title": job.get("title", ""),
            "job_id": str(job.get("jobId", job.get("positionId", ""))),
            "company_name": comp.get("compName", ""),
            "city": job.get("cityName", job.get("city", "")),
            "department": job.get("deptName", job.get("department", "")),
            "experience": job.get("workYear", ""),
            "education": job.get("eduLevel", ""),
            "salary_desc": job.get("salary", ""),
            "job_type": job.get("jobType", job.get("emplType", "")),
            "benefits": job.get("welfare", comp.get("compWelfare", "")),
            "publish_date": str(job.get("pubDate", job.get("refreshTime", ""))),
            "source_url": source_url,
            "jd_text": job.get("jobDescription", job.get("description", "")),
            "raw_payload": dict(api_job),  # preserve complete API response
        }
        return raw

    def _parse_api_response(
        self, api_info: dict, company_name: str,
    ) -> tuple[list[JobData], set[str]]:
        """Parse a single API response into JobData list + set of job IDs.

        Returns:
            (jobs, ids) — list of JobData and set of job_id strings
        """
        jobs: list[JobData] = []
        ids: set[str] = set()

        body = api_info["body"]
        # Navigate: data.data.jobCardList
        try:
            job_list = body.get("data", {}).get("data", {}).get("jobCardList", [])
        except AttributeError:
            return jobs, ids

        if not isinstance(job_list, list):
            return jobs, ids

        for item in job_list:
            if not isinstance(item, dict):
                continue
            try:
                raw = self._extract_raw_job(item)
                raw["company_name"] = raw.get("company_name") or company_name
                if not raw.get("job_id"):
                    raw["job_id"] = raw.get("source_url", "").strip()
                if not raw["job_id"]:
                    continue
                ids.add(raw["job_id"])
                jobs.append(
                    Normalizer.normalize_raw(raw, raw["company_name"], "liepin")
                )
            except Exception as e:
                logger.debug(f"Failed to parse Liepin job card: {e}")

        return jobs, ids
