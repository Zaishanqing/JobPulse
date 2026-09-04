from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class BaiduScraper(BaseScraper):
    """Baidu talent recruitment scraper.

    Baidu uses a public JSON API at talent.baidu.com.  The scraper POSTs
    to the /httservice/getPostListNew endpoint with page-based pagination.

    API details (2026-07-08):
      Endpoint:  POST https://talent.baidu.com/httservice/getPostListNew
      Request:   {"pageNo": <int>, "pageSize": 20, "postType": 0}
      Response:  {"data": {"list": [...], "total": N}}

    Fields in each list item:
      name, id, departmentName, workPlaceList, serviceCondition,
      education, jobDesc, publishTime, etc.
    """

    name = "baidu"

    API_URL = "https://talent.baidu.com/httservice/getPostListNew"
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGES = 100

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "baidu"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings from Baidu talent."""
        logger.info(f"Baidu scraping {company.name}")
        jobs: list[JobData] = []
        page = 1

        while True:
            items = self._fetch_page(page)

            if not items:
                break

            for item in items:
                try:
                    jobs.append(self._parse_job(item, company.name))
                except Exception as e:
                    logger.warning(
                        f"Failed to parse Baidu job in {company.name}: {e}"
                    )

            logger.info(
                f"Baidu {company.name}: page {page} done, "
                f"{len(jobs)} jobs total"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Baidu {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            page += 1

            if page > self.MAX_PAGES:
                break

        logger.info(
            f"Baidu {company.name}: scraping complete, "
            f"{len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, page: int) -> list[dict]:
        """POST to the Baidu API and return the list of job items.

        Returns an empty list if the request fails or returns no data.
        """
        try:
            resp = self.client.post(
                self.API_URL,
                json={
                    "pageNo": page,
                    "pageSize": self.DEFAULT_PAGE_SIZE,
                    "postType": 0,  # 0 = 社招
                },
            )
            data = resp.json()
            return data.get("data", {}).get("list", [])
        except Exception as e:
            logger.error(f"Baidu API request failed on page {page}: {e}")
            return []

    def _parse_job(self, item: dict, company_name: str) -> JobData:
        """Parse a single job dict from the Baidu API into a JobData."""
        raw = {
            "job_title": item.get("name", ""),
            "job_id": str(item.get("id", "")),
            "department": item.get("departmentName", ""),
            "city": (
                item.get("workPlaceList", [""])[0]
                if item.get("workPlaceList")
                else ""
            ),
            "experience": str(item.get("serviceCondition", "")),
            "education": item.get("education", ""),
            "salary_desc": "",
            "jd_text": item.get("jobDesc", ""),
            "skill_tags": "",
            "publish_date": item.get("publishTime", ""),
            "source_url": (
                f"https://talent.baidu.com/job/detail/{item.get('id', '')}"
            ),
            # Keep the complete API object so later extraction can be replayed
            # without asking the crawler to infer semantic fields.
            "raw_payload": dict(item),
        }
        return Normalizer.normalize_raw(raw, company_name, "baidu")
