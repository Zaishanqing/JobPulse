from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class TencentScraper(BaseScraper):
    """Tencent recruitment scraper (join.qq.com).

    Tencent uses a modern SPA at join.qq.com with a public JSON API.
    The scraper POSTs to the position listing endpoint with page-based
    pagination.

    API details (best-effort, 2026-07-08):
      Endpoint:   POST https://join.qq.com/api/position/list
      Request:    {"page": <int>, "size": 20, "keyword": ""}
      Response:   {"code": 0, "data": {"list": [...], "total": N}}

    IMPORTANT: As of 2026-07-08, the exact API endpoint and request format
    are based on community research and may have changed.  The Tencent
    join.qq.com site is a client-rendered SPA; the front-end may gate
    API access behind CSRF tokens, signatures, or CORS restrictions.

    This scraper is therefore implemented as a best-effort scaffold:
      - If the API endpoint is accessible and returns valid JSON, jobs
        will be parsed correctly.
      - If the API is blocked (403, 405, encrypted, or requires a
        signature), the scraper logs a warning and returns an empty list.
      - ``_parse_job`` uses field names derived from reverse-engineering
        the join.qq.com front-end.
    """

    name = "tencent"

    # Best-known API endpoint for Tencent recruitment
    API_URL = "https://join.qq.com/api/position/list"
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGES = 100

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "tencent"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings from Tencent join.qq.com."""
        logger.info(f"Tencent scraping {company.name}")
        jobs: list[JobData] = []
        page = 1

        while True:
            data = self._fetch_page(page)

            if not data:
                break

            items = data.get("list", [])
            total = data.get("total", 0)

            if not items:
                break

            for item in items:
                try:
                    jobs.append(self._parse_job(item, company.name))
                except Exception as e:
                    logger.warning(
                        f"Failed to parse Tencent job in {company.name}: {e}"
                    )

            logger.info(
                f"Tencent {company.name}: page {page} done, "
                f"{len(jobs)}/{total} jobs"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Tencent {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            if len(items) < self.DEFAULT_PAGE_SIZE:
                break
            page += 1

            if page > self.MAX_PAGES:
                break

        if not jobs:
            logger.warning(
                "Tencent scraper returned no jobs.  This may mean the API "
                "endpoint is blocked, requires authentication, or has "
                "changed.  Check join.qq.com manually for current API."
            )

        logger.info(
            f"Tencent {company.name}: scraping complete, "
            f"{len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, page: int) -> dict:
        """POST to the Tencent API and return the parsed ``data`` dict.

        Returns an empty dict if the request fails or the API is blocked.
        """
        try:
            resp = self.client.post(
                self.API_URL,
                json={
                    "page": page,
                    "size": self.DEFAULT_PAGE_SIZE,
                    "keyword": "",
                },
            )
        except Exception as e:
            logger.error(
                f"Tencent API request failed on page {page}: {e}"
            )
            return {}

        if not resp.ok:
            logger.warning(
                f"Tencent API returned HTTP {resp.status_code} on page "
                f"{page}.  The endpoint may require authentication or "
                f"a CSRF token."
            )
            return {}

        try:
            body = resp.json()
        except Exception as e:
            logger.error(
                f"Failed to parse Tencent API JSON response on page "
                f"{page}: {e}"
            )
            return {}

        code = body.get("code")
        if code is not None and code != 0:
            msg = body.get("message", "unknown error")
            logger.warning(
                f"Tencent API error code={code}: {msg}"
            )
            return {}

        return body.get("data", {})

    def _parse_job(self, item: dict, company_name: str) -> JobData:
        """Parse a single job dict from the Tencent API into a JobData.

        Field names reflect the expected JSON structure from join.qq.com.
        """
        # Work location: may be a list of city objects or a plain string
        location_list = item.get("workLocationList", item.get("locationList", []))
        if isinstance(location_list, list) and location_list:
            first_loc = location_list[0] or {}
            if isinstance(first_loc, dict):
                city = first_loc.get("name", first_loc.get("city", ""))
            else:
                city = str(first_loc)
        else:
            city = item.get("workLocation", item.get("city", ""))

        # Department: may be a dict with name field
        dept = item.get("department", "")
        if isinstance(dept, dict):
            department = dept.get("name", "")
        else:
            department = str(dept) if dept else ""

        # Job type / recruit type
        recruit_type = item.get("recruitType", item.get("postType", ""))
        if isinstance(recruit_type, dict):
            job_type = recruit_type.get("name", "")
        else:
            job_type = str(recruit_type) if recruit_type else ""

        # Tags
        tags = item.get("tags", item.get("skillTags", []))
        if isinstance(tags, list):
            skill_tags = ", ".join(
                t.get("name", t) if isinstance(t, dict) else str(t)
                for t in tags
            )
        else:
            skill_tags = ""

        # Source URL
        job_id = str(item.get("id", item.get("postId", "")))
        source_url = ""
        if job_id:
            source_url = f"https://join.qq.com/post.html?pid={job_id}"

        raw = {
            "job_title": item.get("title", item.get("name", "")),
            "job_id": job_id,
            "department": department,
            "city": city,
            "district": "",
            "job_type": job_type,
            "experience": str(item.get("experience", item.get("workYear", ""))),
            "education": str(item.get("education", item.get("degree", ""))),
            "salary_desc": item.get("salary", item.get("salaryDesc", "")),
            "jd_text": item.get("description", item.get("desc", "")),
            "skill_tags": skill_tags,
            "benefits": "",
            "publish_date": str(item.get("publishTime", item.get("createTime", ""))),
            "source_url": source_url,
            "raw_payload": dict(item),
        }
        return Normalizer.normalize_raw(raw, company_name, "tencent")
