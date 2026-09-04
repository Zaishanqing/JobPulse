from unittest.mock import patch, MagicMock

import pytest

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.tencent_scraper import TencentScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_tencent_supports_platform():
    """TencentScraper should claim platform='tencent' companies."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )
    assert scraper.supports(config) is True


def test_tencent_rejects_other_platforms():
    """TencentScraper must not claim non-Tencent companies."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )
    assert scraper.supports(config) is False


def test_tencent_rejects_moka_platform():
    """Moka is a different platform and should not match."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _parse_job tests
# ---------------------------------------------------------------------------

def test_tencent_parse_job_full():
    """Parse a fully-populated Tencent job item."""
    scraper = TencentScraper()
    api_item = {
        "id": "12345",
        "title": "高级后台开发工程师",
        "department": {"name": "WXG微信事业群"},
        "workLocationList": [
            {"name": "深圳"},
            {"name": "广州"},
        ],
        "recruitType": {"name": "社招"},
        "experience": "3-5年",
        "education": "本科",
        "salaryDesc": "30K-60K",
        "description": "岗位职责：负责微信后台系统开发\n任职要求：精通C++/Go",
        "tags": [
            {"name": "C++"},
            {"name": "Go"},
            {"name": "Linux"},
        ],
        "publishTime": "2026-07-01",
    }

    job = scraper._parse_job(api_item, "腾讯")

    assert job.company_name == "腾讯"
    assert job.job_title == "高级后台开发工程师"
    assert job.job_id == "12345"
    assert job.department == "WXG微信事业群"
    assert job.city == "深圳"
    assert job.job_type == "社招"
    assert job.experience_raw == "3-5年"
    assert job.education_raw == "本科"
    assert job.salary_desc == "30K-60K"
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert "微信后台" in job.jd_text
    assert "C++/Go" in job.jd_text
    assert "C++" in job.skills_raw
    assert "Go" in job.skills_raw
    assert job.raw_payload == api_item
    assert job.source_platform == "tencent"
    assert "join.qq.com" in job.source_url
    assert "12345" in job.source_url


def test_tencent_parse_job_alternate_keys():
    """Parse job using alternate field names (backend may vary)."""
    scraper = TencentScraper()
    api_item = {
        "postId": "99",
        "name": "产品经理",
        "department": "CDG企业发展事业群",
        "locationList": [{"name": "北京"}],
        "postType": "校招",
        "workYear": "应届",
        "degree": "硕士",
        "salary": "面议",
        "desc": "负责产品规划与设计",
        "createTime": "2026-06-15",
    }

    job = scraper._parse_job(api_item, "腾讯")

    assert job.company_name == "腾讯"
    assert job.job_title == "产品经理"
    assert job.job_id == "99"
    assert job.department == "CDG企业发展事业群"
    assert job.city == "北京"
    assert job.job_type == "校招"
    assert job.experience_raw == "应届"
    assert job.education_raw == "硕士"
    assert job.salary_desc == "面议"
    assert job.salary_min == 0  # 面议
    assert job.salary_max == 0
    assert job.source_platform == "tencent"


def test_tencent_parse_job_minimal():
    """Parse a bare-minimum job item."""
    scraper = TencentScraper()
    api_item = {
        "id": "1",
        "title": "实习生",
        "description": "",
    }

    job = scraper._parse_job(api_item, "腾讯")

    assert job.company_name == "腾讯"
    assert job.job_title == "实习生"
    assert job.job_id == "1"
    assert job.department == ""
    assert job.city == ""
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert job.source_platform == "tencent"


def test_tencent_parse_job_department_string():
    """Handle department as a plain string (not dict)."""
    scraper = TencentScraper()
    api_item = {
        "id": "1",
        "title": "测试岗",
        "department": "技术工程事业群",
        "locationList": [{"name": "上海"}],
        "description": "",
    }

    job = scraper._parse_job(api_item, "腾讯")
    assert job.department == "技术工程事业群"
    assert job.city == "上海"


def test_tencent_parse_job_city_string():
    """Handle workLocationList containing plain strings."""
    scraper = TencentScraper()
    api_item = {
        "id": "1",
        "title": "测试岗",
        "workLocationList": ["北京"],
        "department": "",
        "description": "",
    }

    job = scraper._parse_job(api_item, "腾讯")
    assert job.city == "北京"


# ---------------------------------------------------------------------------
# scrape() tests (mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.tencent_scraper.RateLimitedClient")
def test_tencent_scrape_pagination(mock_client_cls):
    """Test full scrape with two pages of results."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )

    # Page 1: full page of 20 items
    page1_items = [
        {"id": str(i), "title": f"职位{i}", "description": ""}
        for i in range(1, 21)
    ]

    resp_page1 = MagicMock()
    resp_page1.status_code = 200
    resp_page1.ok = True
    resp_page1.json.return_value = {
        "code": 0,
        "data": {"list": page1_items, "total": 25},
    }

    # Page 2: 5 more items
    page2_items = [
        {"id": str(i), "title": f"职位{i}", "description": ""}
        for i in range(21, 26)
    ]

    resp_page2 = MagicMock()
    resp_page2.status_code = 200
    resp_page2.ok = True
    resp_page2.json.return_value = {
        "code": 0,
        "data": {"list": page2_items, "total": 25},
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp_page1, resp_page2]

    jobs = scraper.scrape(config)

    assert len(jobs) == 25
    assert jobs[0].company_name == "腾讯"
    assert jobs[0].job_title == "职位1"
    assert jobs[-1].company_name == "腾讯"
    assert jobs[-1].job_title == "职位25"
    assert mock_client.post.call_count == 2


@patch("multi_company_scraper.scrapers.tencent_scraper.RateLimitedClient")
def test_tencent_scrape_handles_parse_error(mock_client_cls):
    """Per-job parse errors should be logged but not crash the whole scrape."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )

    good_item = {"id": "1", "title": "好职位", "description": ""}

    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {
        "code": 0,
        "data": {
            "list": [good_item, None, good_item],
            "total": 3,
        },
    }

    # Page 2: empty
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.ok = True
    resp2.json.return_value = {
        "code": 0,
        "data": {"list": [], "total": 3},
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp, resp2]

    jobs = scraper.scrape(config)
    assert len(jobs) == 2
    assert all(j.company_name == "腾讯" for j in jobs)


@patch("multi_company_scraper.scrapers.tencent_scraper.RateLimitedClient")
def test_tencent_scrape_api_blocked(mock_client_cls):
    """When API returns non-200, scraper returns empty list."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )

    resp = MagicMock()
    resp.status_code = 403
    resp.ok = False

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = resp

    jobs = scraper.scrape(config)
    assert jobs == []


@patch("multi_company_scraper.scrapers.tencent_scraper.RateLimitedClient")
def test_tencent_scrape_api_error_code(mock_client_cls):
    """When API returns non-zero code, scraper stops and returns empty."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )

    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {
        "code": -1,
        "message": "unauthorized",
        "data": None,
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = resp

    jobs = scraper.scrape(config)
    assert jobs == []


@patch("multi_company_scraper.scrapers.tencent_scraper.RateLimitedClient")
def test_tencent_scrape_request_error(mock_client_cls):
    """HTTP request exception should return empty list."""
    scraper = TencentScraper()
    config = CompanyConfig(
        name="腾讯",
        platform="tencent",
        base_url="https://join.qq.com/",
    )

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = Exception("Timeout")

    jobs = scraper.scrape(config)
    assert jobs == []
