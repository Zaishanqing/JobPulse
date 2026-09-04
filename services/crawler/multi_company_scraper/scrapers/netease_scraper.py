from bs4 import BeautifulSoup
from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class NeteaseScraper(BaseScraper):
    """Netease HR recruitment scraper.

    Netease uses server-rendered HTML at hr.163.com.  The scraper fetches
    pages via GET with a ``currentPage`` query parameter and parses job
    cards using BeautifulSoup.

    Site structure (2026-07-08):
      List page:   GET https://hr.163.com/position/list.do?currentPage=<N>
      Detail page: https://hr.163.com/job-detail.html?id=<id>

    Job cards are identified by CSS selectors:
      .position-item, .job-item, tr[class*='item']
    With a fallback to any ``<a>`` tag whose href contains "detail".
    """

    name = "netease"

    LIST_URL = "https://hr.163.com/position/list.do"
    MAX_PAGES = 100

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "netease"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings from Netease HR."""
        logger.info(f"Netease scraping {company.name}")
        jobs: list[JobData] = []
        page = 1

        while True:
            soup = self._fetch_page(page)

            if soup is None:
                break

            cards = soup.select(
                ".position-item, .job-item, tr[class*='item']"
            )
            if not cards:
                # fallback: look for any job-like elements
                cards = soup.find_all(
                    "a", href=lambda h: h and "detail" in h
                )

            if not cards:
                break

            for card in cards:
                try:
                    job = self._parse_card(card, company.name)
                    if job is not None:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(
                        f"Failed to parse Netease job card: {e}"
                    )

            logger.info(
                f"Netease {company.name}: page {page} done, "
                f"{len(jobs)} jobs total"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Netease {company.name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            # Check if there's a next page
            next_link = soup.select_one(
                ".next:not(.disabled), a[rel='next']"
            )
            if not next_link:
                break
            page += 1

            if page > self.MAX_PAGES:
                break

        logger.info(
            f"Netease {company.name}: scraping complete, "
            f"{len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, page: int) -> BeautifulSoup | None:
        """GET the list page and return parsed BeautifulSoup.

        Returns None if the request fails.
        """
        try:
            resp = self.client.get(
                self.LIST_URL, params={"currentPage": page}
            )
            return BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.error(
                f"Netease page {page} request failed: {e}"
            )
            return None

    def _parse_card(self, card, company_name: str) -> JobData | None:
        """Parse a single job card element into a JobData.

        Returns None if the card does not contain enough information.
        """
        # If the card itself is an <a> tag, use it directly
        if card.name == "a":
            title_el = card
        else:
            title_el = card.select_one(".title, .job-title, .name, a")

        city_el = card.select_one(".city, .area, .location")
        dept_el = card.select_one(".department, .dept")

        if title_el is None:
            return None

        job_url = ""
        if title_el.name == "a":
            job_url = title_el.get("href", "")
            if job_url and not job_url.startswith("http"):
                job_url = "https://hr.163.com" + job_url

        raw = {
            "job_title": title_el.get_text(strip=True) if title_el else "",
            "job_id": job_url,
            "city": city_el.get_text(strip=True) if city_el else "",
            "department": dept_el.get_text(strip=True) if dept_el else "",
            "source_url": job_url,
            # This path parses server-rendered cards, so the card HTML is the
            # closest reproducible source evidence available to the crawler.
            "raw_html": str(card),
        }
        return Normalizer.normalize_raw(raw, company_name, "netease")
