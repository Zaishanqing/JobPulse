import json
from unittest.mock import patch, MagicMock

import pytest

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.feishu_scraper import FeishuScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_feishu_supports_platform():
    """FeishuScraper should claim platform='feishu' companies."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
        api_config={"feishu_company_id": "nio"},
    )
    assert scraper.supports(config) is True


def test_feishu_rejects_other_platforms():
    """FeishuScraper must not claim non-Feishu companies."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={"moka_company_id": "shein"},
    )
    assert scraper.supports(config) is False


def test_feishu_rejects_playwright_platform():
    """Playwright platform should not match feishu scraper."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="字节跳动",
        platform="playwright",
        base_url="https://jobs.bytedance.com/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _parse_job tests
# ---------------------------------------------------------------------------

def test_feishu_parse_job_full():
    """Parse a fully-populated Feishu job post item."""
    scraper = FeishuScraper()
    api_post = {
        "id": "7645164709637015817",
        "title": "高级后端工程师",
        "sub_title": None,
        "description": "岗位职责：负责后端系统架构设计与开发",
        "requirement": "精通Python，熟悉分布式系统，5年以上经验",
        "job_category": None,
        "city_info": None,
        "recruit_type": {
            "id": "101",
            "name": "全职",
            "en_name": "Full-time",
            "parent": {
                "id": "1",
                "name": "社招",
                "en_name": "Experienced",
            },
        },
        "publish_time": 1750000000000,
        "job_hot_flag": None,
        "job_subject": None,
        "code": None,
        "department_id": None,
        "job_function": {
            "id": "7257443440471542077",
            "name": "技术部",
            "en_name": "Engineering",
        },
        "job_process_id": "6982453786539772196",
        "recommend_id": None,
        "city_list": [
            {
                "code": "CT_125",
                "name": "上海",
                "en_name": "Shanghai",
            }
        ],
        "job_post_info": {
            "id": None,
            "experience": 5,
            "required_degree": 3,
            "min_salary": 30,
            "max_salary": 60,
            "recruitment_type": {
                "id": "101",
                "name": "全职",
            },
            "address_list": [],
            "job_post_object_value_map": {},
        },
        "tag_list": [
            {
                "id": "7220969923572713765",
                "name": {"name": "Python", "en_name": "Python"},
                "order": 0,
            },
            {
                "id": "7220969923572713766",
                "name": {"name": "Go", "en_name": "Go"},
                "order": 1,
            },
        ],
        "storefront_mode": 1,
        "storefront_list": None,
        "process_type": 1,
    }

    job = scraper._parse_job(api_post, "NIO", "nio")

    assert job.company_name == "NIO"
    assert job.job_title == "高级后端工程师"
    assert job.job_id == "7645164709637015817"
    assert job.department == "技术部"
    assert job.city == "上海"
    assert job.district == ""
    assert job.job_type == "全职"
    assert job.education_raw == "3"
    assert job.salary_desc == "30-60"
    assert "后端系统架构" in job.jd_text
    assert "精通Python" in job.jd_text
    assert "Python" in job.skills_raw
    assert "Go" in job.skills_raw
    assert job.education == ""
    assert job.skill_tags == ""
    assert job.raw_payload == api_post
    assert job.source_platform == "feishu"
    assert "nio.jobs.feishu.cn" in job.source_url
    assert "7645164709637015817" in job.source_url


def test_feishu_parse_job_minimal():
    """Parse a bare-minimum job post (no optional fields)."""
    scraper = FeishuScraper()
    api_post = {
        "id": "99",
        "title": "实习生",
        "description": "",
        "requirement": "",
        "city_list": [],
        "recruit_type": {},
        "job_function": {},
        "job_post_info": {},
        "tag_list": [],
    }

    job = scraper._parse_job(api_post, "Xpeng", "xiaopeng")

    assert job.company_name == "Xpeng"
    assert job.job_title == "实习生"
    assert job.job_id == "99"
    assert job.department == ""
    assert job.city == ""
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert job.source_platform == "feishu"


def test_feishu_parse_job_recruit_type_fallback_to_parent():
    """When recruit_type.name is empty, fall back to parent.name."""
    scraper = FeishuScraper()
    api_post = {
        "id": "1",
        "title": "测试",
        "description": "",
        "requirement": "",
        "city_list": [],
        "recruit_type": {
            "name": "",
            "parent": {"name": "社招"},
        },
        "job_function": {},
        "job_post_info": {},
        "tag_list": [],
    }

    job = scraper._parse_job(api_post, "LiAuto", "lixiang")
    assert job.job_type == "社招"


def test_feishu_parse_job_salary_none():
    """Min/max salary fields that are null/False should produce empty string."""
    scraper = FeishuScraper()
    api_post = {
        "id": "1",
        "title": "无薪岗位",
        "description": "",
        "requirement": "",
        "city_list": [],
        "recruit_type": {},
        "job_function": {},
        "job_post_info": {
            "min_salary": None,
            "max_salary": False,
        },
        "tag_list": [],
    }

    job = scraper._parse_job(api_post, "NIO", "nio")
    assert job.salary_desc == ""
    assert job.salary_min == 0
    assert job.salary_max == 0


def test_feishu_parse_job_publish_time():
    """Verify millisecond timestamp conversion to date string."""
    scraper = FeishuScraper()
    # Use a recent known-good timestamp (2026-06-15 00:00:00 UTC)
    # 2026-06-15 = approx 1783468800 seconds = 1783468800000 ms
    api_post = {
        "id": "1",
        "title": "测试岗位",
        "description": "",
        "requirement": "",
        "city_list": [],
        "recruit_type": {},
        "job_function": {},
        "job_post_info": {},
        "tag_list": [],
        "publish_time": 1783468800000,
    }

    job = scraper._parse_job(api_post, "NIO", "nio")
    # Date should be in local time; verify it is a valid date string
    assert job.publish_date is not None
    assert len(job.publish_date) == 10  # YYYY-MM-DD
    # Year should be 2026 (timestamp is June 2026)
    assert "2026" in job.publish_date


# ---------------------------------------------------------------------------
# scrape() tests (paginated, mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.feishu_scraper.RateLimitedClient")
def test_feishu_scrape_pagination(mock_client_cls):
    """Test full scrape with two pages of results.

    The scraper uses offset-based pagination.  It continues requesting
    pages until it receives fewer items than DEFAULT_PAGE_SIZE (20).
    """
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
        api_config={"feishu_company_id": "nio", "portal_type": 6},
    )

    # Mock CSRF token response
    csrf_mock = MagicMock()
    csrf_mock.json.return_value = {
        "code": 0,
        "data": {"token": "mock-csrf-token"},
    }

    # Page 1: full page of 20 items
    page1_items = [
        {
            "id": str(i),
            "title": f"职位{i}",
            "description": "",
            "requirement": "",
            "city_list": [],
            "recruit_type": {},
            "job_function": {},
            "job_post_info": {},
            "tag_list": [],
        }
        for i in range(1, 21)
    ]

    resp_page1 = MagicMock()
    resp_page1.status_code = 200
    resp_page1.ok = True
    resp_page1.json.return_value = {
        "code": 0,
        "data": {
            "job_post_list": page1_items,
            "count": 25,
        },
    }

    # Page 2: partial page of 5 items (stops pagination)
    page2_items = [
        {
            "id": str(i),
            "title": f"职位{i}",
            "description": "",
            "requirement": "",
            "city_list": [],
            "recruit_type": {},
            "job_function": {},
            "job_post_info": {},
            "tag_list": [],
        }
        for i in range(21, 26)
    ]

    resp_page2 = MagicMock()
    resp_page2.status_code = 200
    resp_page2.ok = True
    resp_page2.json.return_value = {
        "code": 0,
        "data": {
            "job_post_list": page2_items,
            "count": 25,
        },
    }

    mock_client = mock_client_cls.return_value
    # First call: CSRF token, then page 1, then page 2
    mock_client.post.side_effect = [csrf_mock, resp_page1, resp_page2]

    jobs = scraper.scrape(config)

    assert len(jobs) == 25
    assert jobs[0].company_name == "NIO"
    assert jobs[0].job_title == "职位1"
    assert jobs[-1].company_name == "NIO"
    assert jobs[-1].job_title == "职位25"
    # Three API calls: CSRF + page 1 + page 2
    assert mock_client.post.call_count == 3


@patch("multi_company_scraper.scrapers.feishu_scraper.RateLimitedClient")
def test_feishu_scrape_no_company_id(mock_client_cls):
    """If feishu_company_id is missing, return empty list immediately."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="BadConfig",
        platform="feishu",
        base_url="https://example.com/",
        api_config={},
    )
    jobs = scraper.scrape(config)
    assert jobs == []
    mock_client_cls.return_value.post.assert_not_called()


@patch("multi_company_scraper.scrapers.feishu_scraper.RateLimitedClient")
def test_feishu_scrape_405_signature_blocked(mock_client_cls):
    """When API returns 405 (missing signature), scraper returns empty."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
        api_config={"feishu_company_id": "nio"},
    )

    # CSRF token succeeds
    csrf_mock = MagicMock()
    csrf_mock.json.return_value = {
        "code": 0,
        "data": {"token": "mock-csrf-token"},
    }

    # Search returns 405
    search_mock = MagicMock()
    search_mock.status_code = 405
    search_mock.ok = False

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [csrf_mock, search_mock]

    jobs = scraper.scrape(config)
    assert jobs == []
    assert mock_client.post.call_count == 2


@patch("multi_company_scraper.scrapers.feishu_scraper.RateLimitedClient")
def test_feishu_scrape_handles_parse_error(mock_client_cls):
    """Per-job parse errors should be logged but not crash the whole scrape."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
        api_config={"feishu_company_id": "nio"},
    )

    good_item = {
        "id": "1",
        "title": "好职位",
        "description": "",
        "requirement": "",
        "city_list": [],
        "recruit_type": {},
        "job_function": {},
        "job_post_info": {},
        "tag_list": [],
    }

    # CSRF
    csrf_mock = MagicMock()
    csrf_mock.json.return_value = {
        "code": 0,
        "data": {"token": "mock-csrf-token"},
    }

    # Search page with mixed items
    resp_page = MagicMock()
    resp_page.status_code = 200
    resp_page.ok = True
    resp_page.json.return_value = {
        "code": 0,
        "data": {
            "job_post_list": [
                good_item,
                None,  # Will crash _parse_job
                good_item,
            ],
            "count": 3,
        },
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [csrf_mock, resp_page]

    jobs = scraper.scrape(config)
    assert len(jobs) == 2
    assert all(j.company_name == "NIO" for j in jobs)


@patch("multi_company_scraper.scrapers.feishu_scraper.RateLimitedClient")
def test_feishu_scrape_api_error_code(mock_client_cls):
    """When API returns non-zero code, scraper stops and returns empty."""
    scraper = FeishuScraper()
    config = CompanyConfig(
        name="NIO",
        platform="feishu",
        base_url="https://nio.jobs.feishu.cn/",
        api_config={"feishu_company_id": "nio"},
    )

    csrf_mock = MagicMock()
    csrf_mock.json.return_value = {
        "code": 0,
        "data": {"token": "mock-csrf-token"},
    }

    error_mock = MagicMock()
    error_mock.status_code = 200
    error_mock.ok = True
    error_mock.json.return_value = {
        "code": -1,
        "message": "invalid request",
        "data": None,
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [csrf_mock, error_mock]

    jobs = scraper.scrape(config)
    assert jobs == []
