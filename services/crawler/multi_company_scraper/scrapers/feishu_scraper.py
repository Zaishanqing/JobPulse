from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class FeishuScraper(BaseScraper):
    """Feishu / Lark recruitment platform scraper.

    Feishu (https://www.feishu.cn/) provides an ATS used by many Chinese
    companies including NIO, Xpeng Motors, Li Auto, etc.  Each company
    has its own subdomain: ``<company>.jobs.feishu.cn``.

    API discovery (2026-07-08):
      Endpoint:  POST /api/v1/search/job/posts
      Query:     ?keyword=&limit=10&offset=0&...&portal_type=6&
                 portal_entrance=1&_signature=<hash>
      Body:      {"keyword":"","limit":10,"offset":0,
                  "job_category_id_list":[],"tag_id_list":[],
                  "location_code_list":[],"subject_id_list":[],
                  "recruitment_id_list":[],"portal_type":6,
                  "job_function_id_list":[],"storefront_id_list":[],
                  "portal_entrance":1}
      Response:  {"code":0,"data":{"job_post_list":[...],"count":N}}

    IMPORTANT: As of 2026-07-08, the Feishu recruitment API requires a
    CSRF token (obtained from ``POST /api/v1/csrf/token``) sent as the
    ``x-csrf-token`` header, AND a ``_signature`` query parameter that
    is generated client-side by obfuscated JavaScript.  Without the
    ``_signature`` parameter, the API returns HTTP 405.

    This scraper is therefore implemented as a best-effort scaffold:
      - The endpoint URL and request format are correct.
      - ``_fetch_page`` attempts to obtain a CSRF token first, then
        issues the search request.  If the ``_signature`` mechanism
        blocks the call, the scraper logs a warning and returns empty.
      - ``_parse_job`` uses field names derived from reverse-engineering
        the Feishu front-end response data.
      - If Feishu relaxes the ``_signature`` requirement in the future
        (or if the signature generation algorithm is obtained), this
        scraper will work with minimal changes.
    """

    name = "feishu"

    # API endpoint path (relative to company subdomain)
    API_PATH = "/api/v1/search/job/posts"
    CSRF_PATH = "/api/v1/csrf/token"

    # Expected per-page limit
    DEFAULT_PAGE_SIZE = 20

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "feishu"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings for a Feishu-powered company."""
        company_id = company.api_config.get("feishu_company_id", "")
        if not company_id:
            logger.error(
                f"Feishu company_id not configured for {company.name}"
            )
            return []

        base_url = f"https://{company_id}.jobs.feishu.cn"
        portal_type = company.api_config.get("portal_type", 6)

        logger.info(
            f"Feishu scraping {company.name} "
            f"(company_id={company_id}, portal_type={portal_type})"
        )

        # Attempt to obtain a CSRF token
        csrf_token = self._fetch_csrf_token(base_url)

        jobs: list[JobData] = []
        offset = 0

        while True:
            data = self._fetch_page(base_url, portal_type, offset, csrf_token)

            if not data:
                break

            posts = data.get("job_post_list", [])
            total = data.get("count", 0)

            if not posts:
                break

            for post in posts:
                try:
                    jobs.append(self._parse_job(post, company.name, company_id))
                except Exception as e:
                    logger.warning(
                        f"Failed to parse job in {company.name}: {e}"
                    )

            logger.info(
                f"Feishu {company.name}: offset {offset} done, "
                f"{len(jobs)}/{total} jobs"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Feishu {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            if len(posts) < self.DEFAULT_PAGE_SIZE:
                break
            offset += len(posts)

            # Safety exit: max 100 pages
            if offset >= self.DEFAULT_PAGE_SIZE * 100:
                break

        logger.info(
            f"Feishu {company.name}: scraping complete, "
            f"{len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_csrf_token(self, base_url: str) -> str:
        """Obtain a CSRF token from the Feishu API.

        Returns the token string on success, empty string on failure.
        """
        try:
            resp = self.client.post(
                f"{base_url}{self.CSRF_PATH}",
                json={"portal_entrance": 1},
            )
            body = resp.json()
            token = body.get("data", {}).get("token", "")
            if token:
                logger.debug(f"Obtained Feishu CSRF token")
            else:
                logger.warning(
                    "Feishu CSRF token endpoint returned no token"
                )
            return token
        except Exception as e:
            logger.warning(f"Failed to obtain Feishu CSRF token: {e}")
            return ""

    def _fetch_page(
        self,
        base_url: str,
        portal_type: int,
        offset: int,
        csrf_token: str = "",
    ) -> dict:
        """POST to the Feishu search API and return the parsed JSON data.

        Returns the ``data`` dict from the response on success, or an
        empty dict on failure (including 405 / signature failures).
        """
        url = f"{base_url}{self.API_PATH}"

        params = {
            "keyword": "",
            "limit": self.DEFAULT_PAGE_SIZE,
            "offset": offset,
            "job_category_id_list": "",
            "tag_id_list": "",
            "location_code_list": "",
            "subject_id_list": "",
            "recruitment_id_list": "",
            "portal_type": portal_type,
            "job_function_id_list": "",
            "storefront_id_list": "",
            "portal_entrance": 1,
        }

        body = {
            "keyword": "",
            "limit": self.DEFAULT_PAGE_SIZE,
            "offset": offset,
            "job_category_id_list": [],
            "tag_id_list": [],
            "location_code_list": [],
            "subject_id_list": [],
            "recruitment_id_list": [],
            "portal_type": portal_type,
            "job_function_id_list": [],
            "storefront_id_list": [],
            "portal_entrance": 1,
        }

        headers = {"Content-Type": "application/json"}
        if csrf_token:
            headers["x-csrf-token"] = csrf_token

        try:
            resp = self.client.post(url, params=params, json=body, headers=headers)
        except Exception as e:
            logger.error(f"Feishu API request failed: {e}")
            return {}

        if resp.status_code == 405:
            logger.warning(
                "Feishu API returned 405 (Method Not Allowed). "
                "This typically means the _signature query parameter "
                "is missing or invalid.  The Feishu front-end generates "
                "this signature client-side via obfuscated JS.  "
                "Cannot parse jobs without the signature algorithm."
            )
            return {}

        if not resp.ok:
            logger.warning(
                f"Feishu API returned HTTP {resp.status_code}"
            )
            return {}

        try:
            body = resp.json()
        except Exception as e:
            logger.error(f"Failed to parse Feishu API JSON response: {e}")
            return {}

        code = body.get("code")
        if code != 0:
            msg = body.get("message", "unknown error")
            logger.warning(f"Feishu API error code={code}: {msg}")
            return {}

        return body.get("data", {})

    def _parse_job(
        self, post: dict, company_name: str, company_id: str = ""
    ) -> JobData:
        """Parse a single job post dict from the Feishu API into a JobData.

        Task 02: uses ``normalize_raw()`` — no semantic processing.
        Raw API fields are preserved in ``raw_payload``.
        """
        # --- raw field extraction (no semantic mapping) ---
        city_list = post.get("city_list", [])
        first_city = (city_list[0] or {}) if isinstance(city_list, list) and city_list else {}
        city = first_city.get("name", "")

        recruit_type = post.get("recruit_type", {}) or {}
        job_type = recruit_type.get("name", "")
        if not job_type and isinstance(recruit_type.get("parent"), dict):
            job_type = recruit_type["parent"].get("name", "")

        job_function = post.get("job_function", {}) or {}
        department = job_function.get("name", "")

        jpi = post.get("job_post_info", {}) or {}
        experience_raw = str(jpi.get("experience") or "")
        education_raw = str(jpi.get("required_degree") or "")

        # Feishu exposes display tags directly. Joining their source labels is
        # deterministic transport formatting and does not classify skills.
        tag_names: list[str] = []
        for tag in post.get("tag_list") or []:
            if not isinstance(tag, dict):
                continue
            name = tag.get("name", "")
            if isinstance(name, dict):
                name = name.get("name", "")
            if name:
                tag_names.append(str(name))
        salary_desc = self._format_salary(jpi)

        # Source-provided description & requirement — assemble as stable
        # template text (deterministic, no semantic inference).
        description = post.get("description", "") or ""
        requirement = post.get("requirement", "") or ""
        jd_parts = []
        if description:
            jd_parts.append("【source.description】\n" + description)
        if requirement:
            jd_parts.append("【source.requirement】\n" + requirement)
        jd_text = "\n\n".join(jd_parts)

        # Publish time
        publish_ts = post.get("publish_time")
        publish_date = ""
        if publish_ts:
            from datetime import datetime
            try:
                publish_date = datetime.fromtimestamp(
                    int(publish_ts) / 1000
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        job_id = str(post.get("id", ""))
        source_url = (
            f"https://{company_id}.jobs.feishu.cn/index?jobId={job_id}"
            if company_id and job_id else ""
        )

        # --- raw_payload: preserve the complete API response ---
        raw_payload = dict(post)

        raw = {
            "job_title": post.get("title", ""),
            "job_id": job_id,
            "department": department,
            "city": city,
            "district": "",
            "job_type": job_type,
            "experience": experience_raw,
            "education": education_raw,
            "skills_raw": ", ".join(tag_names),
            "salary_desc": salary_desc,
            "jd_text": jd_text,
            "benefits": "",
            "publish_date": publish_date,
            "source_url": source_url,
            "raw_payload": raw_payload,
        }
        return Normalizer.normalize_raw(raw, company_name, "feishu")

    # ------------------------------------------------------------------
    # Field formatting (deterministic, no semantics)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_salary(jpi: dict) -> str:
        min_sal = jpi.get("min_salary")
        max_sal = jpi.get("max_salary")
        parts = []
        for v in (min_sal, max_sal):
            if v is not None and v is not False:
                try:
                    parts.append(str(int(v)))
                except (ValueError, TypeError):
                    pass
        return "-".join(parts) if parts else ""
