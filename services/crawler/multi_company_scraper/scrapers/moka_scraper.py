import re
from typing import Optional

from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class MokaScraper(BaseScraper):
    """Moka ATS platform scraper.

    Moka (https://mokahr.com) is a third-party recruitment ATS used by many
    Chinese companies including SHEIN, Sohu, Zhihu, Vipshop, etc.

    API discovery (2026-07-07):
      Endpoint:  POST /api/outer/ats-apply/website/jobs/module
      Request:   {"keyword":"","limit":40,"offset":0,"departmentIds":[],
                  "siteId":<int>,"orgId":"<company_id>",
                  "needGroupType":"zhineng","moduleId":"<str>",
                  "jobEnableFields":["location"],"enableBrandIcon":false,
                  "websitePageId":null,"locale":"zh-CN"}
      Response:  {"data": "<encrypted base64 string>",
                  "necromancer": "<decryption-key-related>"}

    IMPORTANT: As of 2026-07-07, Moka encrypts API response bodies.  The
    "data" field is a base64-encoded encrypted blob.  Without the
    decryption algorithm (which lives in obfuscated client-side JS), the
    raw API endpoint cannot yield plain-text job listings.

    This scraper is therefore implemented as a best-effort scaffold:
      - The endpoint URL and request format are correct.
      - _parse_raw_item and _parse_job use the expected (decrypted) JSON
        field names derived from reverse-engineering the Moka front-end.
      - If Moka removes encryption in the future (or if the decryption
        key is obtained), this scraper will work with minimal changes.
    """

    name = "moka"

    # API endpoint discovered via browser DevTools
    API_URL = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/module"

    # Expected per-page limit (matching what the Moka front-end requests)
    DEFAULT_PAGE_SIZE = 40

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "moka"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings for a Moka-powered company."""
        org_id = self._extract_org_id(company)
        if not org_id:
            logger.error(f"Could not extract Moka orgId for {company.name}")
            return []

        logger.info(f"Moka scraping {company.name} (orgId={org_id})")
        jobs: list[JobData] = []
        offset = 0

        while True:
            body = self._build_request_body(company, org_id, offset)
            resp_data = self._fetch_page(body)

            if not resp_data:
                break

            # Moka API wraps its payload in one of two shapes:
            #   {"data": "<encrypted>"}       -- current (encrypted)
            #   {"data": {"list": [...], ...}} -- unencrypted (hoped-for)
            raw_data = resp_data.get("data", {})

            # Encrypted path: the "data" value is a base64 string
            if isinstance(raw_data, str):
                logger.warning(
                    f"Moka API returned encrypted data for {company.name}. "
                    f"Received {len(raw_data)}-char ciphertext. "
                    f"Cannot parse jobs without decryption key."
                )
                break

            # Unencrypted path: "data" is a dict with a "list" key
            items = raw_data.get("list", [])
            total = raw_data.get("total", 0)

            if not items:
                break

            for item in items:
                try:
                    jobs.append(self._parse_job(item, company.name))
                except Exception as e:
                    logger.warning(
                        f"Failed to parse job in {company.name}: {e}"
                    )

            logger.info(
                f"Moka {company.name}: offset {offset} done, "
                f"{len(jobs)}/{total} jobs"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Moka {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            if len(items) < self.DEFAULT_PAGE_SIZE:
                break
            offset += len(items)

        logger.info(
            f"Moka {company.name}: scraping complete, {len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_org_id(self, company: CompanyConfig) -> str:
        """Extract the Moka organisation ID from the company config.

        Priority:
          1. api_config.moka_company_id
          2. Last path segment of base_url (strip trailing slash)
        """
        explicit = company.api_config.get("moka_company_id", "")
        if explicit:
            return explicit

        # Fallback: parse from base_url
        # e.g. https://app.mokahr.com/apply/shein/  ->  shein
        #      https://app.mokahr.com/campus_apply/sohu/  ->  sohu
        #      https://app.mokahr.com/campus-recruitment/sina/  ->  sina
        m = re.search(
            r"mokahr\.com/(?:apply|campus_apply|campus-recruitment)/([^/]+)",
            company.base_url,
        )
        if m:
            return m.group(1)
        return ""

    def _extract_site_id(self, company: CompanyConfig) -> Optional[int]:
        """Return the Moka siteId if explicitly configured, else None.

        When None the request omits siteId; the Moka API may default to
        the first site belonging to the organisation.
        """
        sid = company.api_config.get("site_id")
        if sid is not None:
            return int(sid)
        return None

    def _build_request_body(
        self,
        company: CompanyConfig,
        org_id: str,
        offset: int,
    ) -> dict:
        """Construct the POST body for the Moka jobs/module endpoint."""
        site_id = self._extract_site_id(company)
        module_id = company.api_config.get("module_id", "")

        body: dict = {
            "keyword": "",
            "limit": self.DEFAULT_PAGE_SIZE,
            "offset": offset,
            "departmentIds": [],
            "orgId": org_id,
            "needGroupType": "zhineng",
            "jobEnableFields": ["location"],
            "enableBrandIcon": False,
            "websitePageId": None,
            "locale": "zh-CN",
        }

        if site_id is not None:
            body["siteId"] = site_id
        if module_id:
            body["moduleId"] = module_id

        return body

    def _fetch_page(self, body: dict) -> dict:
        """POST to the Moka API and return the parsed JSON response."""
        try:
            resp = self.client.post(
                self.API_URL,
                json=body,
            )
            return resp.json()
        except Exception:
            logger.exception(f"Failed to fetch page from Moka API")
            return {}

    def _parse_job(self, item: dict, company_name: str) -> JobData:
        """Parse a single job dict from the Moka API into a JobData.

        The field names below reflect the *decrypted* JSON structure
        observed in the Moka front-end rendering.  Adjust as needed
        if the plain-text structure differs.
        """
        raw = {
            "job_title": item.get("name", ""),
            "job_id": str(item.get("id", "")),
            "department": (
                item.get("department", {}).get("name", "")
                if isinstance(item.get("department"), dict)
                else ""
            ),
            "city": (
                item.get("city", {}).get("name", "")
                if isinstance(item.get("city"), dict)
                else ""
            ),
            "district": (
                item.get("district", {}).get("name", "")
                if isinstance(item.get("district"), dict)
                else ""
            ),
            "job_type": item.get("job_type", item.get("recruitType", "")),
            "experience": item.get("experience", ""),
            "education": item.get("education", ""),
            "salary_desc": item.get("salary", ""),
            "jd_text": item.get("description", ""),
            "skill_tags": (
                ", ".join(item.get("tags", []))
                if isinstance(item.get("tags"), list)
                else ""
            ),
            "benefits": (
                ", ".join(item.get("benefits", []))
                if isinstance(item.get("benefits"), list)
                else ""
            ),
            "publish_date": item.get("publish_time", ""),
            "source_url": (
                f"https://app.mokahr.com/apply/{company_name}"
                f"/job/{item.get('id', '')}"
            ),
            "raw_payload": dict(item),
        }
        return Normalizer.normalize_raw(raw, company_name, "moka")
