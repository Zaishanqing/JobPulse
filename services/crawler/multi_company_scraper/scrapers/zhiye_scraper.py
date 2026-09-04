from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.http_client import RateLimitedClient
from multi_company_scraper.normalizer import Normalizer


class ZhiyeScraper(BaseScraper):
    """Zhiye (智联 ATS) recruitment platform scraper.

    Zhiye (zhiye.com, operated by Zhaopin) is a third-party career site
    platform used by many Chinese companies (e.g. 科大讯飞, 海康威视, etc.).
    Each company gets a subdomain: ``<company>.zhiye.com``.

    Site structure (2026-07-08):
      The platform uses server-rendered HTML for job listing pages.
      URLs typically follow the pattern:
        - List:  https://<company>.zhiye.com/Social  (社招)
        - List:  https://<company>.zhiye.com/Campus   (校招)
        - Detail: https://<company>.zhiye.com/jobdetail/<id>

    This scraper attempts to fetch and parse the HTML job listing page.
    It also tries a JSON API endpoint as an alternative.

    IMPORTANT: The zhiye.com platform may use anti-scraping measures
    (CAPTCHAs, IP rate-limiting, obfuscated HTML, or JavaScript rendering
    requirements).  This scraper is a best-effort scaffold:
      - If the HTML is parseable, jobs will be extracted.
      - If the API endpoint is accessible, jobs will be parsed.
      - If blocked, the scraper logs a warning and returns an empty list.
    """

    name = "zhiye"

    # Alternative API endpoint (may or may not be available)
    API_PATH = "/api/position/list"
    MAX_PAGES = 100

    def __init__(self):
        self.client = RateLimitedClient()

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "zhiye"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        """Scrape all job listings from a zhiye.com company page."""
        base_url = company.base_url.rstrip("/")
        if not base_url:
            logger.error(f"No base_url configured for {company.name}")
            return []

        logger.info(
            f"Zhiye scraping {company.name} (base_url={base_url})"
        )

        # Try API approach first, fall back to HTML
        jobs = self._scrape_api(base_url, company.name)
        if not jobs:
            logger.info(
                f"Zhiye API approach yielded no results for "
                f"{company.name}, trying HTML parsing"
            )
            jobs = self._scrape_html(base_url, company.name)

        if not jobs:
            logger.warning(
                "Zhiye scraper returned no jobs for "
                f"{company.name}.  The site may use "
                "JavaScript rendering or anti-scraping measures."
            )

        logger.info(
            f"Zhiye {company.name}: scraping complete, "
            f"{len(jobs)} jobs total"
        )
        return jobs

    # ------------------------------------------------------------------
    # API approach
    # ------------------------------------------------------------------

    def _scrape_api(self, base_url: str, company_name: str) -> list[JobData]:
        """Try the JSON API endpoint for zhiye.com."""
        api_url = urljoin(base_url, self.API_PATH)
        jobs: list[JobData] = []
        page = 1

        while True:
            data = self._fetch_api_page(api_url, page)

            if not data:
                break

            items = data.get("list", data.get("data", []))
            if isinstance(items, dict):
                items = items.get("list", [])

            if not items:
                break

            for item in items:
                try:
                    jobs.append(
                        self._parse_api_job(item, company_name, base_url)
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to parse Zhiye job: {e}"
                    )

            logger.info(
                f"Zhiye {company_name}: API page {page} done, "
                f"{len(jobs)} jobs"
            )

            if len(jobs) >= self.MAX_JOBS_PER_COMPANY:
                logger.info(
                    f"Zhiye {company_name}: hit cap of "
                    f"{self.MAX_JOBS_PER_COMPANY} jobs, stopping"
                )
                break

            # Stop if we got a partial page (end of results)
            if len(items) < 20:
                break

            page += 1
            if page > self.MAX_PAGES:
                break

        return jobs

    def _fetch_api_page(self, api_url: str, page: int) -> dict:
        """POST to the Zhiye API and return the parsed ``data`` dict.

        Returns an empty dict if the request fails.
        """
        try:
            resp = self.client.post(
                api_url,
                json={
                    "pageNo": page,
                    "pageSize": 20,
                },
            )
        except Exception as e:
            logger.error(
                f"Zhiye API request failed on page {page}: {e}"
            )
            return {}

        if not resp.ok:
            logger.warning(
                f"Zhiye API returned HTTP {resp.status_code} on page "
                f"{page}.  The API may not exist or may require auth."
            )
            return {}

        try:
            body = resp.json()
        except Exception:
            logger.debug(
                f"Zhiye API page {page} did not return JSON; "
                f"likely HTML-only platform"
            )
            return {}

        return body.get("data", body)

    def _parse_api_job(
        self, item: dict, company_name: str, base_url: str
    ) -> JobData:
        """Parse a single job dict from the Zhiye API into a JobData."""
        job_id = str(item.get("id", item.get("positionId", "")))
        source_url = ""
        if job_id:
            source_url = urljoin(base_url, f"/jobdetail/{job_id}")

        raw = {
            "job_title": item.get("name", item.get("title", "")),
            "job_id": job_id,
            "department": item.get("departmentName", item.get("deptName", "")),
            "city": item.get("workPlaceName", item.get("cityName", "")),
            "district": "",
            "job_type": item.get("jobType", item.get("recruitType", "")),
            "experience": str(item.get("experience", item.get("workYear", ""))),
            "education": str(item.get("education", item.get("degree", ""))),
            "salary_desc": item.get("salary", item.get("salaryDesc", "")),
            "jd_text": item.get("description", item.get("jobDesc", "")),
            "skill_tags": "",
            "benefits": "",
            "publish_date": str(item.get("publishTime", item.get("createTime", ""))),
            "source_url": source_url,
            "raw_payload": dict(item),
        }
        return Normalizer.normalize_raw(raw, company_name, "zhiye")

    # ------------------------------------------------------------------
    # HTML approach
    # ------------------------------------------------------------------

    def _scrape_html(
        self, base_url: str, company_name: str
    ) -> list[JobData]:
        """Parse the HTML job listing page from zhiye.com."""
        jobs: list[JobData] = []

        # Zhiye typically has /Social and /Campus sub-paths
        for subpath in ("/Social", "/Campus", ""):
            list_url = urljoin(base_url, subpath) if subpath else base_url
            soup = self._fetch_html_page(list_url)
            if soup is None:
                continue

            page_jobs = self._parse_html_list(soup, base_url, company_name)
            jobs.extend(page_jobs)

            if page_jobs:
                logger.info(
                    f"Zhiye {company_name}: HTML {subpath or '/'} "
                    f"yielded {len(page_jobs)} jobs"
                )
                # We got jobs from one subpath; stop
                break

        return jobs

    def _fetch_html_page(self, url: str) -> BeautifulSoup | None:
        """GET the HTML page and return parsed BeautifulSoup.

        Returns None if the request fails.
        """
        try:
            resp = self.client.get(url)
            return BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.error(
                f"Zhiye HTML request failed for {url}: {e}"
            )
            return None

    def _parse_html_list(
        self, soup: BeautifulSoup, base_url: str, company_name: str
    ) -> list[JobData]:
        """Parse job cards from the zhiye.com HTML page."""
        jobs: list[JobData] = []

        # Try common selectors for job cards on zhiye.com
        cards = soup.select(
            ".position-item, .job-item, .job-list .item, "
            ".position-list li, .post-item, "
            "table tr[class*='item'], .list-item"
        )

        if not cards:
            # Fallback: find any link that looks like a job detail link
            cards = soup.find_all(
                "a",
                href=lambda h: h
                and ("jobdetail" in h or "position" in h or "job/" in h),
            )

        for card in cards:
            try:
                job = self._parse_html_card(
                    card, base_url, company_name
                )
                if job is not None:
                    jobs.append(job)
            except Exception as e:
                logger.warning(
                    f"Failed to parse Zhiye HTML card: {e}"
                )

        return jobs

    def _parse_html_card(
        self, card, base_url: str, company_name: str
    ) -> JobData | None:
        """Parse a single HTML job card element into a JobData.

        Returns None if the card does not contain enough information.
        """
        # If the card itself is an <a> tag, use it directly
        if card.name == "a":
            title_el = card
        else:
            title_el = card.select_one(
                ".title, .job-title, .name, .position-name, a"
            )
        if title_el is None:
            return None

        title = title_el.get_text(strip=True)
        if not title:
            return None

        # URL
        job_url = ""
        if title_el.name == "a":
            job_url = title_el.get("href", "")
        elif title_el.parent and title_el.parent.name == "a":
            job_url = title_el.parent.get("href", "")
        elif card.name == "a":
            job_url = card.get("href", "")

        if job_url and not job_url.startswith("http"):
            job_url = urljoin(base_url, job_url)

        # City
        city_el = card.select_one(
            ".city, .work-place, .location, .area"
        )
        city = city_el.get_text(strip=True) if city_el else ""

        # Department
        dept_el = card.select_one(
            ".department, .dept, .org"
        )
        department = dept_el.get_text(strip=True) if dept_el else ""

        # Date
        date_el = card.select_one(
            ".date, .time, .publish-time, .post-date"
        )
        publish_date = date_el.get_text(strip=True) if date_el else ""

        raw = {
            "job_title": title,
            "job_id": job_url,
            "city": city,
            "department": department,
            "publish_date": publish_date,
            "source_url": job_url,
            "raw_html": str(card),
        }
        return Normalizer.normalize_raw(raw, company_name, "zhiye")
