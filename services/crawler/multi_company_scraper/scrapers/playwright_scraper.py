import datetime
import json
import os
import random
from pathlib import Path
from urllib.parse import urljoin

from loguru import logger
from playwright.sync_api import sync_playwright, Page

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.normalizer import (
    Normalizer,
    compute_raw_text,
)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


DEFAULT_SELECTORS = {
    "job_list_container": "[class*='list'], [class*='jobs'], [class*='position'], .job-list, .position-list",
    "job_card": "[class*='card'], [class*='item'], li, .job-item, .position-item",
    "job_title": "[class*='title'], [class*='name'], h3, h4, a",
    "job_city": "[class*='city'], [class*='location'], [class*='area'], [class*='place']",
    "job_department": "[class*='department'], [class*='dept'], [class*='team']",
    "job_salary": "[class*='salary'], [class*='pay'], [class*='money']",
    "job_experience": "[class*='experience'], [class*='exp']",
    "job_education": "[class*='education'], [class*='degree']",
    "job_tags": "[class*='tag'], [class*='skill'], [class*='label']",
    "next_page": (
        "[class*='pagination-next']:not([aria-disabled='true']), "
        "[class*='next']:not([class*='disabled']):not([aria-disabled='true']), "
        ".pagination .next, [rel='next'], "
        "li[title='下一页'], button[aria-label*='next']"
    ),
    "detail_link": "a[href*='detail'], a[href*='job'], a[href*='position']",
    "detail_container": "[class*='detail'], [class*='desc'], [class*='content'], .job-detail",
    "detail_title": "[class*='title'], h1, h2",
    "detail_jd": "[class*='desc'], [class*='content'], [class*='requirement'], .job-desc",
}

# Keys whose presence in a JSON object suggest it represents a job listing.
_JOB_INDICATOR_KEYS = {
    "title", "name", "job_title", "jobTitle", "job_name", "jobName",
    "position", "positionName",
    "description", "jd", "requirement", "city", "location",
    "salary", "id", "job_id", "department",
    # Beisen platform field names — must be present verbatim so
    # _score_job_items can match them (indicator is substring of key).
    "duty", "require", "locnames", "workplace",
}


class PlaywrightScraper(BaseScraper):
    """Generic browser-automation scraper for SPA recruitment sites.

    Strategy (two paths, tried in order):

    1. **API interception** — navigate to the listing page, capture XHR
       responses, auto-detect which one contains job data by scoring JSON
       structure.  If found, parse jobs directly from the structured data
       and paginate via ``page.evaluate(fetch(...))``.

    2. **DOM fallback** — when no API response yields job data (e.g.
       server-rendered pages or heavily obfuscated SPAs), fall back to
       CSS-selector-based extraction.
    """

    name = "playwright"
    MAX_PAGES = 100

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "playwright"

    @staticmethod
    def _launch_chromium(chromium, *, headless: bool, args: list[str] | None = None):
        """Launch Chromium, preferring the system browser in container images."""
        launch_options: dict[str, object] = {"headless": headless}
        if args is not None:
            launch_options["args"] = args
        configured_path = os.getenv("BROWSER_EXECUTABLE_PATH", "").strip()
        executable_path = configured_path or "/usr/bin/chromium"
        if Path(executable_path).is_file():
            launch_options["executable_path"] = executable_path
        return chromium.launch(**launch_options)

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings for *company* via Playwright.

        Returns a (possibly empty) list of JobData.
        """
        selectors = self._get_selectors(company)
        browser = None

        with sync_playwright() as p:
            browser = self._launch_chromium(p.chromium, headless=True)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            # -- capture XHR/JSON responses & requests for API-first path -
            api_responses: list[dict] = []
            api_requests: dict[str, str] = {}  # url -> post_data

            def _on_response(response):
                if response.status != 200:
                    return
                ct = response.headers.get("content-type", "")
                # Some sites return JSON with text/plain or other non-json CT
                if "json" in ct or "javascript" in ct or "text/plain" in ct:
                    try:
                        api_responses.append(
                            {"url": response.url, "body": response.json()}
                        )
                    except Exception as exc:
                        logger.debug(f"Browser response was not JSON: {exc}")

            def _on_request(request):
                if request.method == "POST":
                    try:
                        pd = request.post_data
                        if pd:
                            api_requests[request.url] = pd
                    except Exception as exc:
                        # Binary request bodies are expected on some sites;
                        # retain the reason without interrupting collection.
                        logger.debug(f"Could not capture POST body: {exc}")

            page.on("response", _on_response)
            page.on("request", _on_request)

            try:
                page.goto(
                    company.base_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                # Wait for XHR/API calls to fire (SPAs load data async).
                # Some sites load slowly — retry once with extra wait.
                page.wait_for_timeout(5000)

                # --- Path 1: API interception -------------------------
                api_info = self._find_job_api_response(api_responses)
                if not api_info:
                    logger.info(
                        f"No job API detected for {company.name} after "
                        f"first wait, retrying with extended wait..."
                    )
                    page.wait_for_timeout(8000)
                    api_info = self._find_job_api_response(api_responses)
                if api_info:
                    jobs = self._scrape_via_api(
                        page, api_responses, selectors, company, api_requests
                    )
                    if jobs:
                        return jobs
                    logger.info(
                        f"API path found no parseable jobs for "
                        f"{company.name}, falling back to DOM"
                    )

                # --- Path 2: DOM fallback -----------------------------
                jobs = self._scrape_via_dom(
                    page, context, selectors, company
                )

            except Exception as e:
                logger.error(
                    f"Playwright scraper failed for {company.name}: {e}"
                )
                return []
            finally:
                if browser is not None:
                    browser.close()

        return jobs

    # ------------------------------------------------------------------
    # Selector helpers (unchanged — used by DOM fallback)
    # ------------------------------------------------------------------

    def _get_selectors(self, company: CompanyConfig) -> dict:
        selectors = dict(DEFAULT_SELECTORS)
        if company.selectors:
            selectors.update(company.selectors)
        return selectors

    def _try_extract(
        self,
        element,
        selector: str,
        attribute: str | None = None,
    ) -> str:
        if hasattr(element, "query_selector"):
            for sel in selector.split(", "):
                try:
                    el = element.query_selector(sel.strip())
                    if el:
                        if attribute:
                            value = el.get_attribute(attribute)
                            return value or ""
                        return el.inner_text().strip()
                except Exception:
                    continue
        return ""

    # ------------------------------------------------------------------
    # DOM extraction (unchanged — used as fallback)
    # ------------------------------------------------------------------

    def _extract_list_page(
        self, page: Page, selectors: dict, company_name: str,
    ) -> list[dict]:
        results: list[dict] = []
        container = None
        for sel in selectors["job_list_container"].split(", "):
            container = page.query_selector(sel.strip())
            if container:
                break
        if container is None:
            container = page

        cards: list = []
        for sel in selectors["job_card"].split(", "):
            cards = container.query_selector_all(sel.strip())
            if cards:
                break

        for card in cards:
            try:
                title = self._try_extract(card, selectors["job_title"])
                if not title:
                    continue
                city = self._try_extract(card, selectors["job_city"])
                department = self._try_extract(
                    card, selectors["job_department"]
                )
                salary = self._try_extract(card, selectors["job_salary"])
                experience = self._try_extract(
                    card, selectors["job_experience"]
                )
                education = self._try_extract(
                    card, selectors["job_education"]
                )
                tags = self._try_extract(card, selectors["job_tags"])
                detail_url = self._try_extract(
                    card, selectors["detail_link"], "href"
                )
                results.append({
                    "job_title": title,
                    "city": city,
                    "department": department,
                    "salary_desc": salary,
                    "experience": experience,
                    "education": education,
                    "skill_tags": tags,
                    "source_url": detail_url,
                    "raw_html": card.evaluate("(element) => element.outerHTML"),
                })
            except Exception as e:
                logger.debug(f"Skipping card in {company_name}: {e}")
        return results

    def _extract_detail_page(self, page: Page, selectors: dict) -> dict:
        # Browser-rendered HTML is retained as the source record for later
        # extraction and audit; this operation performs no semantic parsing.
        detail: dict = {"raw_html": page.content()}
        for sel in selectors["detail_jd"].split(", "):
            el = page.query_selector(sel.strip())
            if el:
                detail["jd_text"] = el.inner_text().strip()
                break
        if "jd_text" not in detail:
            detail["jd_text"] = ""
        benefits_sel = (
            "[class*='benefit'], [class*='welfare'], [class*='bonus']"
        )
        for sel in benefits_sel.split(", "):
            el = page.query_selector(sel.strip())
            if el:
                detail["benefits"] = el.inner_text().strip()
                break
        return detail

    def _has_next_page(self, page: Page, selectors: dict) -> bool:
        for sel in selectors["next_page"].split(", "):
            el = page.query_selector(sel.strip())
            if el:
                return True
        return False

    def _go_next_page(self, page: Page, selectors: dict):
        for sel in selectors["next_page"].split(", "):
            el = page.query_selector(sel.strip())
            if el:
                el.click()
                page.wait_for_timeout(3000)
                return

    def _scrape_via_dom(
        self, page: Page, context, selectors: dict, company: CompanyConfig,
    ) -> list[JobData]:
        """DOM-based scraping — the original fallback path."""
        jobs: list[JobData] = []

        page_num = 0
        while page_num < self.MAX_PAGES:
            items = self._extract_list_page(page, selectors, company.name)
            logger.info(
                f"Playwright {company.name}: "
                f"page {page_num + 1}, found {len(items)} items"
            )
            if not items:
                break

            for i, item in enumerate(items):
                try:
                    detail_url = item.get("source_url", "")
                    if detail_url:
                        detail_page = context.new_page()
                        try:
                            self._navigate_detail(
                                detail_page,
                                company.base_url,
                                detail_url,
                            )
                            detail = self._extract_detail_page(
                                detail_page, selectors
                            )
                            item.update(detail)
                        finally:
                            detail_page.close()

                    item["job_id"] = item.get(
                        "source_url", f"{company.name}_{i}"
                    )
                    jobs.append(
                        Normalizer.normalize_raw(
                            item, company.name, "playwright"
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to get detail for job "
                        f"in {company.name}: {e}"
                    )
                    item["job_id"] = item.get(
                        "source_url", f"{company.name}_{i}"
                    )
                    jobs.append(
                        Normalizer.normalize_raw(
                            item, company.name, "playwright"
                        )
                    )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Playwright {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs (DOM), stopping"
                )
                break

            if not self._has_next_page(page, selectors):
                break
            self._go_next_page(page, selectors)
            page_num += 1

        return jobs

    # ------------------------------------------------------------------
    # API interception (new — primary path)
    # ------------------------------------------------------------------

    def _find_job_api_response(
        self, api_responses: list[dict],
    ) -> dict | None:
        """Scan captured JSON responses for one that contains job listings.

        Returns ``{url, jobs_list, body}`` or *None*.
        """
        best_score = 0
        best_match = None

        for resp in api_responses:
            score, jobs_list = self._score_job_response(resp["body"])
            if score > best_score:
                best_score = score
                best_match = {
                    "url": resp["url"],
                    "jobs_list": jobs_list,
                    "body": resp["body"],
                }

        if best_score >= 3:
            return best_match
        return None

    def _score_job_response(
        self, obj, depth: int = 0,
    ) -> tuple[int, list | None]:
        """Recursively search a JSON tree for a list of job-like dicts.

        Returns ``(score, jobs_list)``.  *score* is the number of
        job-indicator keys found in the first item of the list.
        """
        if depth > 5:
            return 0, None

        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
            score = self._score_job_items(obj)
            if score > 0:
                return score, obj

        if isinstance(obj, dict):
            for value in obj.values():
                if (
                    isinstance(value, list)
                    and len(value) > 0
                    and isinstance(value[0], dict)
                ):
                    score = self._score_job_items(value)
                    if score > 0:
                        return score, value
                score, jobs = self._score_job_response(value, depth + 1)
                if score > 0:
                    return score, jobs

        return 0, None

    def _score_job_items(self, items: list) -> int:
        """Score a list of dicts — how many job-indicator keys are present.

        Additionally requires at least one *long* text field (likely a JD
        description) to reject false positives from metadata/dictionary APIs
        whose short values (names, codes) happen to match indicator keys.
        """
        if not items or not isinstance(items[0], dict):
            return 0
        sample = items[0]
        score = 0
        has_long_text = False
        for key, value in sample.items():
            key_normalized = key.lower().replace("_", "")
            for indicator in _JOB_INDICATOR_KEYS:
                if indicator.lower().replace("_", "") in key_normalized:
                    score += 1
                    break
            if isinstance(value, str) and len(value) > 100:
                has_long_text = True
        # Allow high-scoring items without long text (e.g. 米哈游 list API
        # has rich metadata but no JD text — detail comes from a separate call).
        if not has_long_text and score < 5:
            return 0
        return score

    def _extract_raw_job(self, api_job: dict) -> dict:
        """Map an arbitrary API job object to our canonical raw-job dict."""
        # Preserve the full API record before projecting transport fields.
        raw: dict = {"raw_payload": dict(api_job)}

        # -- title -----------------------------------------------------
        for k in (
            "title", "name", "job_title", "jobTitle", "job_name",
            "jobName", "position", "positionNameOpen", "recruitPostName",
            "postName", "positionName", "JobAdName", "jobAdName",
        ):
            if k in api_job:
                raw["job_title"] = api_job[k]
                break

        # -- description / JD text ------------------------------------
        for k in ("description", "desc", "job_desc", "jobDesc",
                  "content", "detail", "jobDuty", "duty", "Duty",
                  "responsibility", "positionDescription",
                  "workContent", "work_content",
                  "qualification", "requirements", "jobRequirements"):
            if k in api_job and isinstance(api_job[k], str) and api_job[k]:
                raw["jd_text"] = api_job[k]
                break
        # Append extra description/qualification block (stable template,
        # no semantic markers — task 02 remediation).
        extra = (
            api_job.get("qualification")
            or api_job.get("positionDemand")
            or api_job.get("Require")
            or api_job.get("require")
            or ""
        )
        if raw.get("jd_text") and extra and isinstance(extra, str) and extra not in raw["jd_text"]:
            raw["jd_text"] = raw["jd_text"] + "\n\n" + extra

        # -- city (also handle addressDetailList — 米哈游-style) ------
        addr_list = api_job.get("addressDetailList") or api_job.get("address_detail_list")
        if isinstance(addr_list, list) and addr_list:
            cities = [a.get("addressDetail") or a.get("name", "") for a in addr_list if isinstance(a, dict)]
            if cities:
                raw["city"] = ", ".join(cities)

        if not raw.get("city"):
            for k in ("city", "location", "city_info", "cityInfo",
                      "work_city", "workCity", "workLocation",
                      "work_location"):
                if k in api_job:
                    v = api_job[k]
                    if isinstance(v, dict):
                        raw["city"] = v.get("name") or v.get("city") or ""
                    elif isinstance(v, str):
                        raw["city"] = v
                    break

        # -- city_list / cityList / job_location_list / workLocations / LocNames / workPlaceNameList ---
        if not raw.get("city"):
            for k in ("city_list", "cityList", "job_location_list",
                      "workLocations", "LocNames", "locNames", "loc_names",
                      "workPlaceNameList", "workPlaceList", "work_place_list"):
                if k in api_job:
                    cl = api_job[k]
                    if isinstance(cl, list) and cl:
                        cities = []
                        for c in cl:
                            if isinstance(c, dict):
                                cities.append(c.get("city") or c.get("name", ""))
                            elif isinstance(c, str):
                                cities.append(c)
                        raw["city"] = ", ".join(cities)
                    break

        # -- department -----------------------------------------------
        for k in ("department", "dept", "job_category", "jobCategory",
                  "category", "jobFamilyGroup", "jobFamily",
                  "postCodeName", "postCode", "positionDeptName",
                  "deptName", "departmentName", "requirement_org_name",
                  "department_name", "competencyType",
                  "Org", "org", "ClassificationOne", "classificationOne",
                  "firstDepName", "first_dep_name"):
            if k in api_job:
                v = api_job[k]
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    depts = [d.get("name", "") for d in v if d.get("name")]
                    raw["department"] = ", ".join(depts) if depts else ""
                elif isinstance(v, dict):
                    raw["department"] = (
                        v.get("name") or v.get("department") or ""
                    )
                elif isinstance(v, str):
                    raw["department"] = v
                break

        # -- salary ---------------------------------------------------
        for k in ("salary", "salary_desc", "salaryDesc", "salary_range",
                  "salaryRange"):
            if k in api_job:
                v = api_job[k]
                if isinstance(v, dict):
                    raw["salary_desc"] = (
                        v.get("desc") or v.get("name") or ""
                    )
                elif isinstance(v, str):
                    raw["salary_desc"] = v
                break
        # salaryMin/salaryMax (快手-style)
        if not raw.get("salary_desc"):
            mn = api_job.get("salaryMin", "")
            mx = api_job.get("salaryMax", "")
            if mn or mx:
                raw["salary_desc"] = f"{mn}-{mx}"

        # -- job_id ---------------------------------------------------
        for k in ("id", "job_id", "jobId", "code", "post_id", "postId",
                  "jobUnionId", "unionId", "jobUnion_id",
                  "JobAdId", "jobAdId"):
            if k in api_job:
                raw["job_id"] = str(api_job[k])
                break

        # -- experience -----------------------------------------------
        for k in ("experience", "exp", "work_experience",
                  "workExperience", "seniority",
                  "reqWorkYearsName", "req_work_years_name"):
            if k in api_job:
                v = api_job[k]
                raw["experience"] = (
                    v.get("name") if isinstance(v, dict) else str(v)
                )
                break
        # combine yoe_min/yoe_max if present (vivo-style)
        if not raw.get("experience"):
            yoe_min = api_job.get("yoe_min", "")
            yoe_max = api_job.get("yoe_max", "")
            if yoe_min or yoe_max:
                raw["experience"] = f"{yoe_min}-{yoe_max}年"

        # -- education ------------------------------------------------
        for k in ("education", "edu", "degree", "education_level",
                  "educationLevel", "degree_range_name",
                  "reqEducationName", "req_education_name"):
            if k in api_job:
                v = api_job[k]
                raw["education"] = (
                    v.get("name") if isinstance(v, dict) else str(v)
                )
                break

        # -- publish_date ---------------------------------------------
        for k in ("publish_time", "publishTime", "create_time",
                  "createTime", "post_date", "postDate", "refreshTime",
                  "update_time", "updateTime", "pushTime",
                  "formatPublishTime", "publishDate"):
            if k in api_job:
                v = api_job[k]
                if isinstance(v, (int, float)) and v > 1000000000:
                    raw["publish_date"] = datetime.datetime.fromtimestamp(
                        v / 1000
                    ).strftime("%Y-%m-%d")
                elif isinstance(v, str):
                    raw["publish_date"] = v[:10]
                break

        # -- job_type -------------------------------------------------
        for k in ("positionTypeName", "recruit_type", "recruitType",
                  "job_type", "jobType", "employment_type",
                  "employmentType", "job_category", "jobCategoryName",
                  "jobNature", "firstPostTypeName", "workType"):
            if k in api_job:
                v = api_job[k]
                raw["job_type"] = (
                    v.get("name") if isinstance(v, dict) else str(v)
                )
                break

        # -- source_url -----------------------------------------------
        for k in ("url", "link", "detail_url", "detailUrl",
                  "apply_url", "applyUrl"):
            if k in api_job:
                raw["source_url"] = str(api_job[k])
                break

        # -- skill_tags -----------------------------------------------
        for k in ("tags", "skills", "skill_tags", "skillTags",
                  "labels", "keywords"):
            if k in api_job and api_job[k]:
                v = api_job[k]
                if isinstance(v, list):
                    raw["skill_tags"] = ", ".join(str(t) for t in v)
                elif isinstance(v, str):
                    raw["skill_tags"] = v
                break

        # -- benefits -------------------------------------------------
        for k in ("benefits", "welfare", "benefit", "welfares",
                  "highLight", "highlight", "highlights"):
            if k in api_job and api_job[k]:
                v = api_job[k]
                if isinstance(v, list):
                    raw["benefits"] = ", ".join(str(t) for t in v)
                elif isinstance(v, str):
                    raw["benefits"] = v
                break

        # -- nested job_post_info (ByteDance-style) -------------------
        jpi = api_job.get("job_post_info")
        if isinstance(jpi, dict):
            if jpi.get("address"):
                raw["district"] = jpi["address"]
            for field, keys in (
                ("education", ("education",)),
                ("experience", ("experience",)),
            ):
                if not raw.get(field):
                    for k in keys:
                        if k in jpi:
                            v = jpi[k]
                            raw[field] = (
                                v.get("name")
                                if isinstance(v, dict)
                                else str(v)
                            )
                            break
            # salary from job_post_info
            if not raw.get("salary_desc"):
                mn = jpi.get("min_salary", "")
                mx = jpi.get("max_salary", "")
                if mn or mx:
                    raw["salary_desc"] = f"{mn}-{mx}"

        return raw

    def _scrape_via_api(
        self,
        page: Page,
        api_responses: list[dict],
        selectors: dict,
        company: CompanyConfig,
        api_requests: dict[str, str] | None = None,
    ) -> list[JobData]:
        """Extract jobs from captured API responses.

        Pagination is driven through the DOM (clicking "next page" buttons)
        so the page's own JS generates valid signatures / CSRF tokens for
        each subsequent API request.

        Falls back to direct ``page.evaluate(fetch(...))`` calls when DOM
        pagination clicks fail to produce new API responses (e.g. JD.com).
        """
        jobs: list[JobData] = []
        seen_ids: set[str] = set()
        page_num = 0
        api_url = None  # saved from page 1 for direct-call fallback
        api_method = "GET"
        api_body_template = None
        total_expected: int | None = None  # from API metadata
        first_page_size = 0  # for partial-page end detection

        while page_num < self.MAX_PAGES:
            # Pick the best API response from whatever came in this round
            api_info = self._find_job_api_response(api_responses)
            if not api_info:
                break

            if page_num == 0:
                api_url = api_info["url"]
                # Detect POST body pattern for direct-call fallback
                req_body = (api_requests or {}).get(api_url, "")
                api_method, api_body_template, json_template = self._detect_api_pattern(
                    api_info["body"], req_body
                )
                first_page_size = len(api_info["jobs_list"])
                total_expected = self._extract_total_from_body(api_info["body"])

            new_count = 0
            current_page_size = len(api_info["jobs_list"])
            for job_data in api_info["jobs_list"]:
                try:
                    raw = self._extract_raw_job(job_data)
                    jid = raw.get("job_id", "")
                    if jid and jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    raw["job_id"] = jid or f"{company.name}_{len(jobs)}"
                    jobs.append(
                        Normalizer.normalize_raw(
                            raw, company.name, "playwright"
                        )
                    )
                    new_count += 1
                except Exception as e:
                    logger.debug(
                        f"Failed to parse API job for {company.name}: {e}"
                    )

            if page_num == 0:
                logger.info(
                    f"Playwright {company.name}: extracted "
                    f"{new_count} jobs from API response (page 1)"
                )
                if total_expected:
                    logger.info(f"  Total expected: {total_expected}")
            if new_count == 0:
                break

            # Stop if we hit the per-company cap
            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Playwright {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            # Stop if we have all expected jobs (from API metadata)
            if total_expected and len(jobs) >= total_expected:
                logger.info(
                    f"Playwright {company.name}: collected all "
                    f"{total_expected} jobs, stopping"
                )
                break

            # Stop if current page is empty (API returned no data)
            if page_num > 0 and current_page_size == 0:
                logger.info(
                    f"Playwright {company.name}: empty page, "
                    f"assuming last page"
                )
                break

            # -- try pagination: DOM click first, then direct API ----
            try:
                dom_worked = False
                if self._has_next_page(page, selectors):
                    api_responses.clear()
                    self._go_next_page(page, selectors)
                    page.wait_for_timeout(3000)
                    dom_worked = bool(self._find_job_api_response(api_responses))

                if not dom_worked:
                    # Direct API pagination
                    api_responses.clear()
                    success = self._paginate_direct(
                        page, api_url, api_method,
                        api_body_template, page_num + 1,
                        api_responses, json_template,
                    )
                    if not success:
                        break
            except Exception:
                logger.info(
                    f"Playwright {company.name}: pagination failed, "
                    f"returning {len(jobs)} jobs from {page_num + 1} page(s)"
                )
                break

            page_num += 1
            logger.info(
                f"Playwright {company.name}: page {page_num + 1}, "
                f"total so far: {len(jobs)}"
            )

        # -- enrich with detail API if list had no JD text ----------
        if jobs and not any(j.jd_text for j in jobs):
            self._enrich_job_details(page, jobs, api_requests, api_url)

        return jobs

    def _enrich_job_details(
        self,
        page: Page,
        jobs: list[JobData],
        api_requests: dict[str, str] | None,
        list_api_url: str,
    ):
        """Fetch detail API for each job when list API has no JD text.

        Detects the detail API pattern from the list API URL (e.g.
        /ats-portal/v1/job/list → /ats-portal/v1/job/info) and calls it
        for each job using page.evaluate(fetch(...)).
        """
        detail_url = list_api_url.replace("/list", "/info").replace("/query", "/info")
        if detail_url == list_api_url:
            return  # can't derive detail URL
        # Only proceed if the URL looks like a job API
        if not any(kw in detail_url.lower() for kw in ("job", "position", "recruit", "ats-portal")):
            return

        # Get extra body params from the list request
        extra_params = {}
        if api_requests:
            req_body = api_requests.get(list_api_url, "")
            if req_body:
                try:
                    body_obj = json.loads(req_body)
                    if isinstance(body_obj, dict):
                        for k, v in body_obj.items():
                            if k not in ("pageNo", "pageIndex", "pageNum", "page", "pageSize", "id"):
                                extra_params[k] = v
                except (json.JSONDecodeError, TypeError):
                    pass

        logger.info(f"Fetching detail for {len(jobs)} jobs via {detail_url}")
        fetched = 0
        batch_size = 8  # concurrent requests per batch
        # Build id → job index mapping
        id_to_idx = {}
        for idx, job in enumerate(jobs):
            jid = getattr(job, "job_id", "") or getattr(job, "jobId", "")
            if jid:
                id_to_idx[str(jid)] = idx
        all_ids = list(id_to_idx.keys())

        # Batch fetch details via single page.evaluate per batch
        for batch_start in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[batch_start:batch_start + batch_size]
            try:
                fetch_calls = []
                for jid in batch_ids:
                    body = {"id": str(jid)}
                    body.update(extra_params)
                    fetch_calls.append(
                        f"fetch('{detail_url}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                        f"body:JSON.stringify({json.dumps(body)})}}).then(r=>r.ok?r.json():null).catch(e=>null)"
                    )
                js = f"async () => {{ return await Promise.all([{','.join(fetch_calls)}]); }}"
                results = page.evaluate(js)
                if isinstance(results, list):
                    for i, result in enumerate(results):
                        if result and isinstance(result, dict) and result.get("code") == 0:
                            data = result.get("data")
                            if isinstance(data, dict):
                                jid = batch_ids[i]
                                job = jobs[id_to_idx[jid]]
                                desc = data.get("description") or data.get("jobSummary") or ""
                                req = data.get("jobRequire") or data.get("requirement") or data.get("qualification") or ""
                                if desc or req:
                                    # Detail enrichment still transports raw
                                    # source text. Stable labels retain field
                                    # provenance without splitting semantics.
                                    parts = []
                                    if desc:
                                        parts.append("[source.description]\n" + str(desc))
                                    if req:
                                        parts.append("[source.requirement]\n" + str(req))
                                    job.jd_text = compute_raw_text("\n\n".join(parts))
                                    job.raw_text_status = "completed"
                                    job.raw_text_error = ""
                                    job.raw_payload = dict(job.raw_payload)
                                    job.raw_payload["detail_payload"] = dict(data)
                                    fetched += 1
                                if not job.city and isinstance(data.get("addressDetailList"), list):
                                    addrs = data["addressDetailList"]
                                    cities = [a.get("addressDetail", "") for a in addrs if isinstance(a, dict)]
                                    if cities:
                                        job.city = ", ".join(cities)
            except Exception as e:
                logger.debug(f"Detail batch failed at {batch_start}: {e}")

        if fetched:
            logger.info(f"  Enriched {fetched}/{len(jobs)} jobs with JD text")

    def _detect_api_pattern(self, body, req_body: str = "") -> tuple[str, str | None, str | None]:
        """Detect pagination pattern from API response & request body.

        Returns (method, form_body_template, json_body_template).
        One of the two templates will be None.
        """
        # Try to use the actual request body as template (preserves extra params)
        if req_body:
            try:
                template_obj = json.loads(req_body)
                if isinstance(template_obj, dict):
                    # Find the page key and replace its value with placeholder
                    for pkey in ("pageNo", "pageIndex", "pageNum", "page", "currentPage", "current"):
                        if pkey in template_obj:
                            template_obj[pkey] = "{page}"
                            return "POST", None, json.dumps(template_obj)
                    # Page key not on root — check nested data
                    for k, v in template_obj.items():
                        if isinstance(v, dict):
                            for pkey in ("pageNo", "pageIndex", "pageNum", "page", "currentPage", "current"):
                                if pkey in v:
                                    v[pkey] = "{page}"
                                    return "POST", None, json.dumps(template_obj)
            except (json.JSONDecodeError, TypeError):
                pass

        if isinstance(body, dict):
            data = body.get("data") or body
            if isinstance(data, dict):
                for pkey in ("pageNo", "pageIndex", "pageNum", "page", "currentPage", "current"):
                    if pkey in data:
                        page_size = data.get("pageSize") or data.get("page_size") or 10
                        return "POST", None, json.dumps({pkey: "{page}", "pageSize": page_size})
        return "POST", "pageIndex={page}&pageSize=10", None

    def _paginate_direct(
        self, page: Page, api_url: str, method: str,
        body_template: str | None, page_idx: int,
        api_responses: list, json_template: str | None = None,
    ) -> bool:
        """Directly call the API via page.evaluate to trigger next page.

        Supports both form-encoded and JSON body templates.
        Returns True if new data was added to api_responses.
        """
        if json_template:
            body_str = json_template.replace("{page}", str(page_idx + 1))
            content_type = "application/json"
        elif body_template:
            body_str = body_template.replace("{page}", str(page_idx + 1))
            content_type = "application/x-www-form-urlencoded"
        else:
            return False

        js_code = f"""
            async () => {{
                const resp = await fetch({json.dumps(api_url)}, {{
                    method: {json.dumps(method)},
                    headers: {{'Content-Type': {json.dumps(content_type)}}},
                    body: {json.dumps(body_str)},
                }});
                const data = await resp.json();
                return data;
            }}
        """
        try:
            result = page.evaluate(js_code)
            if result is not None:
                api_responses.append({"url": api_url, "body": result})
                return True
        except Exception as exc:
            logger.debug(f"Direct API pagination failed: {exc}")
        return False

    def _extract_total_from_body(self, body) -> int | None:
        """Try to extract the total job count from an API response body.

        Checks common pagination-wrapper patterns.
        """
        # Direct total/count on body
        if isinstance(body, dict):
            for k in ("total", "count", "totalCount", "totalElements",
                      "totalRecord", "totalRecords"):
                v = body.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
            # Nested under data / result / page
            for wrapper in ("data", "result", "page", "pageResult"):
                inner = body.get(wrapper)
                if isinstance(inner, dict):
                    for k in ("total", "count", "totalCount", "totalElements",
                              "totalRecord", "totalRecords", "totalSize"):
                        v = inner.get(k)
                        if isinstance(v, (int, float)):
                            return int(v)
        return None

    # ------------------------------------------------------------------
    # Internal helpers (unchanged)
    # ------------------------------------------------------------------

    def _resolve_url(self, base_url: str, detail_url: str) -> str:
        if detail_url.startswith("http"):
            return detail_url
        return urljoin(base_url.rstrip("/"), detail_url.lstrip("/"))

    def _navigate_detail(
        self, detail_page: Page, base_url: str, detail_url: str,
    ) -> None:
        target = self._resolve_url(base_url, detail_url)
        detail_page.goto(target, wait_until="networkidle", timeout=30000)
        detail_page.wait_for_timeout(2000)
