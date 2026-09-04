from unittest.mock import patch, MagicMock

import pytest
from bs4 import BeautifulSoup

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.netease_scraper import NeteaseScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_netease_supports_platform():
    """NeteaseScraper should claim platform='netease' companies."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="网易",
        platform="netease",
        base_url="https://hr.163.com/",
    )
    assert scraper.supports(config) is True


def test_netease_rejects_other_platforms():
    """NeteaseScraper must not claim non-Netease companies."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )
    assert scraper.supports(config) is False


def test_netease_rejects_feishu_platform():
    """Feishu is a different platform and should not match."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _parse_card tests
# ---------------------------------------------------------------------------

def test_netease_parse_card_with_all_fields():
    """Parse a fully-populated job card element."""
    scraper = NeteaseScraper()

    html = """
    <div class="position-item">
        <a class="title" href="/job-detail.html?id=12345">高级Java工程师</a>
        <span class="city">杭州</span>
        <span class="department">网易云音乐</span>
        <span class="publish-time">2026-07-01</span>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one(".position-item")

    job = scraper._parse_card(card, "网易")

    assert job.job_title == "高级Java工程师"
    assert job.job_id == "https://hr.163.com/job-detail.html?id=12345"
    assert job.city == "杭州"
    assert job.department == "网易云音乐"
    assert job.source_url == "https://hr.163.com/job-detail.html?id=12345"
    assert job.source_platform == "netease"
    assert job.company_name == "网易"


def test_netease_parse_card_minimal():
    """Parse a card with only a title link."""
    scraper = NeteaseScraper()

    html = """
    <div class="job-item">
        <a href="/job-detail.html?id=99">实习生</a>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    # select_one on the soup for the <a> since there's no .title span
    card = soup.find("a")

    job = scraper._parse_card(card, "网易")

    assert job.company_name == "网易"
    assert job.job_title == "实习生"
    assert job.source_platform == "netease"
    assert job.source_url == "https://hr.163.com/job-detail.html?id=99"


def test_netease_parse_card_no_title():
    """Card with no title element should return None."""
    scraper = NeteaseScraper()

    html = """
    <div class="position-item">
        <span class="city">北京</span>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one(".position-item")

    job = scraper._parse_card(card, "网易")
    assert job is None


def test_netease_parse_card_title_not_a_link():
    """When the title element is a span (not <a>), url is empty."""
    scraper = NeteaseScraper()
    html = """
    <div class="position-item">
        <span class="title">产品经理</span>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one(".position-item")
    job = scraper._parse_card(card, "网易")
    assert job.job_title == "产品经理"
    assert job.source_url == ""


# ---------------------------------------------------------------------------
# scrape() tests (mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.netease_scraper.RateLimitedClient")
def test_netease_scrape_single_page(mock_client_cls):
    """Test scraping a single page with multiple job cards."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="网易",
        platform="netease",
        base_url="https://hr.163.com/",
    )

    html = """
    <html>
    <body>
        <div class="position-item">
            <a class="title" href="/job-detail.html?id=1">前端工程师</a>
            <span class="city">杭州</span>
            <span class="department">技术部</span>
        </div>
        <div class="position-item">
            <a class="title" href="/job-detail.html?id=2">后端工程师</a>
            <span class="city">北京</span>
            <span class="department">云计算</span>
        </div>
        <div class="pagination">
            <span class="next disabled">下一页</span>
        </div>
    </body>
    </html>
    """

    resp = MagicMock()
    resp.text = html

    mock_client = mock_client_cls.return_value
    mock_client.get.return_value = resp

    jobs = scraper.scrape(config)

    assert len(jobs) == 2
    assert jobs[0].job_title == "前端工程师"
    assert jobs[0].city == "杭州"
    assert jobs[1].job_title == "后端工程师"
    assert jobs[1].city == "北京"
    assert mock_client.get.call_count == 1


@patch("multi_company_scraper.scrapers.netease_scraper.RateLimitedClient")
def test_netease_scrape_fallback_to_detail_links(mock_client_cls):
    """When selectors don't match, fall back to detail-link <a> tags."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="网易",
        platform="netease",
        base_url="https://hr.163.com/",
    )

    html = """
    <html><body>
        <p>No structured cards here</p>
        <a href="/job-detail.html?id=101">神秘岗位</a>
    </body></html>
    """

    resp = MagicMock()
    resp.text = html

    mock_client = mock_client_cls.return_value
    mock_client.get.return_value = resp

    jobs = scraper.scrape(config)
    assert len(jobs) == 1
    assert jobs[0].job_title == "神秘岗位"
    assert "job-detail.html" in jobs[0].source_url


@patch("multi_company_scraper.scrapers.netease_scraper.RateLimitedClient")
def test_netease_scrape_empty_page(mock_client_cls):
    """Empty page should return empty list."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="网易",
        platform="netease",
        base_url="https://hr.163.com/",
    )

    html = "<html><body><p>No jobs found</p></body></html>"
    resp = MagicMock()
    resp.text = html

    mock_client = mock_client_cls.return_value
    mock_client.get.return_value = resp

    jobs = scraper.scrape(config)
    assert jobs == []
    assert mock_client.get.call_count == 1


@patch("multi_company_scraper.scrapers.netease_scraper.RateLimitedClient")
def test_netease_scrape_request_failure(mock_client_cls):
    """HTTP request failure should return empty list."""
    scraper = NeteaseScraper()
    config = CompanyConfig(
        name="网易",
        platform="netease",
        base_url="https://hr.163.com/",
    )

    mock_client = mock_client_cls.return_value
    mock_client.get.side_effect = Exception("Connection error")

    jobs = scraper.scrape(config)
    assert jobs == []
