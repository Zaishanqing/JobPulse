from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.base import BaseScraper
from multi_company_scraper.scrapers.dispatcher import ScraperDispatcher


class MockScraper(BaseScraper):
    name = "mock"

    def supports(self, company: CompanyConfig) -> bool:
        return company.platform == "mock"

    def scrape(self, company: CompanyConfig) -> list[JobData]:
        return [
            JobData(
                company_name=company.name,
                job_title="测试职位",
                source_platform=self.name,
            )
        ]


def test_dispatcher_register_and_find():
    dispatcher = ScraperDispatcher()
    scraper = MockScraper()
    dispatcher.register(scraper)

    config = CompanyConfig(name="测试公司", platform="mock", base_url="https://example.com")
    found = dispatcher._find_scraper(config)
    assert found is scraper


def test_dispatcher_scrape_company():
    dispatcher = ScraperDispatcher()
    dispatcher.register(MockScraper())

    config = CompanyConfig(name="测试公司", platform="mock", base_url="https://example.com")
    results = dispatcher.scrape_company(config)
    assert len(results) == 1
    assert results[0].company_name == "测试公司"


def test_dispatcher_no_scraper_found():
    dispatcher = ScraperDispatcher()
    config = CompanyConfig(name="未知公司", platform="unknown", base_url="https://example.com")
    results = dispatcher.scrape_company(config)
    assert results == []


def test_dispatcher_disabled_company():
    dispatcher = ScraperDispatcher()
    dispatcher.register(MockScraper())
    config = CompanyConfig(name="禁用的公司", platform="mock", base_url="https://example.com", enabled=False)
    results = dispatcher.scrape_company(config)
    assert results == []
