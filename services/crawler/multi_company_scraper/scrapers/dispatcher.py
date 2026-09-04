from loguru import logger
from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper


class ScraperDispatcher:
    def __init__(self):
        self._scrapers: list[BaseScraper] = []

    def register(self, scraper: BaseScraper):
        self._scrapers.append(scraper)
        logger.info(f"Registered scraper: {scraper.name}")

    def _find_scraper(self, company: CompanyConfig) -> BaseScraper | None:
        for scraper in self._scrapers:
            if scraper.supports(company):
                return scraper
        return None

    def scrape_company(self, company: CompanyConfig) -> list[JobData]:
        if not company.enabled:
            logger.info(f"Skipping disabled company: {company.name}")
            return []

        scraper = self._find_scraper(company)
        if scraper is None:
            logger.warning(f"No scraper found for {company.name} (platform={company.platform})")
            return []

        try:
            logger.info(f"Scraping {company.name} with {scraper.name}")
            return scraper.scrape(company)
        except Exception as e:
            logger.exception(f"Failed to scrape {company.name}")
            return []
