from unittest.mock import patch, MagicMock

import pytest

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.baidu_scraper import BaiduScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_baidu_supports_platform():
    """BaiduScraper should claim platform='baidu' companies."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )
    assert scraper.supports(config) is True


def test_baidu_rejects_other_platforms():
    """BaiduScraper must not claim non-Baidu companies."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )
    assert scraper.supports(config) is False


def test_baidu_rejects_moka_platform():
    """Moka is a different platform and should not match."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _parse_job tests
# ---------------------------------------------------------------------------

def test_baidu_parse_job_full():
    """Parse a fully-populated Baidu job item."""
    scraper = BaiduScraper()
    api_item = {
        "id": "12345",
        "name": "高级算法工程师",
        "departmentName": "百度大搜索",
        "workPlaceList": ["北京", "上海"],
        "serviceCondition": "3-5年",
        "education": "硕士",
        "jobDesc": "岗位职责：负责搜索算法优化\n任职要求：精通NLP/ML",
        "publishTime": "2026-07-01",
    }

    job = scraper._parse_job(api_item, "百度")

    assert job.company_name == "百度"
    assert job.job_title == "高级算法工程师"
    assert job.job_id == "12345"
    assert job.department == "百度大搜索"
    assert job.city == "北京"
    assert job.experience_raw == "3-5年"
    assert job.education_raw == "硕士"
    assert job.jd_text == "岗位职责：负责搜索算法优化\n任职要求：精通NLP/ML"
    assert "搜索算法" in job.jd_text
    assert "NLP/ML" in job.jd_text
    assert job.experience == ""
    assert job.education == ""
    assert job.raw_payload == api_item
    assert job.source_platform == "baidu"
    assert "talent.baidu.com" in job.source_url
    assert "12345" in job.source_url


def test_baidu_parse_job_minimal():
    """Parse a bare-minimum job item."""
    scraper = BaiduScraper()
    api_item = {
        "id": "99",
        "name": "数据分析师",
        "departmentName": "",
        "education": "",
        "jobDesc": "",
    }

    job = scraper._parse_job(api_item, "百度")

    assert job.company_name == "百度"
    assert job.job_title == "数据分析师"
    assert job.job_id == "99"
    assert job.department == ""
    assert job.city == ""
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert job.source_platform == "baidu"


def test_baidu_parse_job_empty_workplace():
    """Handle missing workPlaceList."""
    scraper = BaiduScraper()
    api_item = {
        "id": "1",
        "name": "测试岗",
        "departmentName": "",
        "education": "",
        "jobDesc": "",
    }
    job = scraper._parse_job(api_item, "百度")
    assert job.city == ""


# ---------------------------------------------------------------------------
# scrape() tests (mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.baidu_scraper.RateLimitedClient")
def test_baidu_scrape_pagination(mock_client_cls):
    """Test full scrape with two pages of results."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )

    # Page 1: full page of 20 items
    page1_items = [
        {
            "id": str(i),
            "name": f"职位{i}",
            "departmentName": f"部门{i % 5}",
            "education": "本科",
            "jobDesc": f"描述{i}",
        }
        for i in range(1, 21)
    ]

    resp_page1 = MagicMock()
    resp_page1.json.return_value = {
        "data": {"list": page1_items, "total": 22},
    }

    # Page 2: 2 more items (partial page, stops pagination)
    page2_items = [
        {"id": "21", "name": "职位21", "departmentName": "", "education": "", "jobDesc": ""},
        {"id": "22", "name": "职位22", "departmentName": "", "education": "", "jobDesc": ""},
    ]
    resp_page2 = MagicMock()
    resp_page2.json.return_value = {
        "data": {"list": page2_items, "total": 22},
    }

    # Page 3: empty
    resp_page3 = MagicMock()
    resp_page3.json.return_value = {
        "data": {"list": [], "total": 0},
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp_page1, resp_page2, resp_page3]

    jobs = scraper.scrape(config)

    assert len(jobs) == 22
    assert jobs[0].company_name == "百度"
    assert jobs[0].job_title == "职位1"
    assert jobs[-1].company_name == "百度"
    assert jobs[-1].job_title == "职位22"
    assert mock_client.post.call_count == 3


@patch("multi_company_scraper.scrapers.baidu_scraper.RateLimitedClient")
def test_baidu_scrape_handles_parse_error(mock_client_cls):
    """Per-job parse errors should be logged but not crash the whole scrape."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )

    good_item = {"id": "1", "name": "好职位", "departmentName": "", "education": "", "jobDesc": ""}

    resp = MagicMock()
    resp.json.return_value = {
        "data": {
            "list": [good_item, None, good_item],
            "total": 3,
        }
    }

    # Page 2: empty
    resp2 = MagicMock()
    resp2.json.return_value = {"data": {"list": [], "total": 3}}

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp, resp2]

    jobs = scraper.scrape(config)
    assert len(jobs) == 2
    assert all(j.company_name == "百度" for j in jobs)


@patch("multi_company_scraper.scrapers.baidu_scraper.RateLimitedClient")
def test_baidu_scrape_empty_response(mock_client_cls):
    """When API returns empty list, scraper returns empty."""
    scraper = BaiduScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )

    resp = MagicMock()
    resp.json.return_value = {"data": {"list": [], "total": 0}}

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = resp

    jobs = scraper.scrape(config)
    assert jobs == []
    assert mock_client.post.call_count == 1
