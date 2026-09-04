from abc import ABC, abstractmethod
from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData


class BaseScraper(ABC):
    name: str = "base"
    MAX_JOBS_PER_COMPANY: int = 200

    @abstractmethod
    def supports(self, company: CompanyConfig) -> bool:
        ...

    @abstractmethod
    def scrape(self, company: CompanyConfig) -> list[JobData]:
        ...
