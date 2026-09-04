import json
from unittest.mock import patch, MagicMock

import pytest

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.moka_scraper import MokaScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_moka_supports_platform():
    """MokaScraper should claim platform='moka' companies."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={"moka_company_id": "shein"},
    )
    assert scraper.supports(config) is True


def test_moka_rejects_other_platforms():
    """MokaScraper must not claim non-Moka companies."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="字节跳动",
        platform="playwright",
        base_url="https://jobs.bytedance.com/",
    )
    assert scraper.supports(config) is False


def test_moka_rejects_feishu_platform():
    """Feishu is a different ATS and should not match."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="小鹏汽车",
        platform="feishu",
        base_url="https://xiaopeng.jobs.feishu.cn/",
        api_config={"feishu_company_id": "xiaopeng"},
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _extract_org_id tests
# ---------------------------------------------------------------------------

def test_extract_org_id_from_api_config():
    scraper = MokaScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={"moka_company_id": "shein"},
    )
    assert scraper._extract_org_id(config) == "shein"


def test_extract_org_id_from_base_url_apply():
    """Fallback: parse orgId from /apply/<id>/ URL pattern."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="三七互娱",
        platform="moka",
        base_url="https://app.mokahr.com/apply/37/",
        api_config={},
    )
    assert scraper._extract_org_id(config) == "37"


def test_extract_org_id_from_base_url_campus_apply():
    scraper = MokaScraper()
    config = CompanyConfig(
        name="搜狐",
        platform="moka",
        base_url="https://app.mokahr.com/campus_apply/sohu/",
        api_config={},
    )
    assert scraper._extract_org_id(config) == "sohu"


def test_extract_org_id_from_base_url_campus_recruitment():
    scraper = MokaScraper()
    config = CompanyConfig(
        name="知乎",
        platform="moka",
        base_url="https://app.mokahr.com/campus-recruitment/zhihu/",
        api_config={},
    )
    assert scraper._extract_org_id(config) == "zhihu"


def test_extract_org_id_empty():
    """If base_url does not match any pattern and no api_config, return ''."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="Unknown",
        platform="moka",
        base_url="https://example.com/",
        api_config={},
    )
    assert scraper._extract_org_id(config) == ""


# ---------------------------------------------------------------------------
# _build_request_body tests
# ---------------------------------------------------------------------------

def test_build_request_body_minimal():
    scraper = MokaScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={"moka_company_id": "shein"},
    )
    body = scraper._build_request_body(config, "shein", 0)
    assert body["orgId"] == "shein"
    assert body["limit"] == 40
    assert body["offset"] == 0
    assert body["keyword"] == ""
    assert body["locale"] == "zh-CN"
    assert "departmentIds" in body
    assert "siteId" not in body  # not configured


def test_build_request_body_with_site_and_module():
    scraper = MokaScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={
            "moka_company_id": "shein",
            "site_id": 2933,
            "module_id": "6835049423",
        },
    )
    body = scraper._build_request_body(config, "shein", 40)
    assert body["siteId"] == 2933
    assert body["moduleId"] == "6835049423"
    assert body["offset"] == 40


# ---------------------------------------------------------------------------
# _parse_job tests
# ---------------------------------------------------------------------------

def test_moka_parse_job_full():
    """Parse a fully-populated (decrypted-style) job item."""
    scraper = MokaScraper()
    api_item = {
        "id": "12345",
        "name": "高级后端工程师",
        "department": {"name": "技术部"},
        "city": {"name": "北京"},
        "district": {"name": "海淀区"},
        "job_type": "社招",
        "experience": "3-5年",
        "education": "本科",
        "salary": "30K-60K",
        "description": (
            "岗位职责：负责后端系统架构设计与开发\n"
            "任职要求：精通Python，熟悉分布式系统"
        ),
        "tags": ["Python", "Go", "MySQL"],
        "benefits": ["六险一金", "弹性工作"],
        "publish_time": "2026-07-01",
    }
    job = scraper._parse_job(api_item, "SHEIN")
    assert job.company_name == "SHEIN"
    assert job.job_title == "高级后端工程师"
    assert job.job_id == "12345"
    assert job.department == "技术部"
    assert job.city == "北京"
    assert job.district == "海淀区"
    assert job.job_type == "社招"
    assert job.experience_raw == "3-5年"
    assert job.education_raw == "本科"
    assert job.salary_desc == "30K-60K"
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert "后端系统架构" in job.jd_text
    assert "精通Python" in job.jd_text
    assert "Python" in job.skills_raw
    assert "MySQL" in job.skills_raw
    assert job.raw_payload == api_item
    assert "六险一金" in job.benefits
    assert job.source_platform == "moka"
    assert "SHEIN" in job.source_url


def test_moka_parse_job_minimal():
    """Parse a bare-minimum job item (no optional fields)."""
    scraper = MokaScraper()
    api_item = {
        "id": "99",
        "name": "实习生",
        "description": "",
    }
    job = scraper._parse_job(api_item, "搜狐")
    assert job.company_name == "搜狐"
    assert job.job_title == "实习生"
    assert job.job_id == "99"
    assert job.department == ""
    assert job.city == ""
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert job.source_platform == "moka"


def test_moka_parse_job_city_is_string():
    """Handle city field that is a plain string instead of dict."""
    scraper = MokaScraper()
    api_item = {
        "id": "1",
        "name": "测试岗位",
        "city": "上海",
        "description": "",
    }
    job = scraper._parse_job(api_item, "唯品会")
    # When city is a string, _parse_job handles it gracefully
    assert job.company_name == "唯品会"


def test_moka_parse_job_salary_mianyi():
    """Salary field contains '面议'."""
    scraper = MokaScraper()
    api_item = {
        "id": "5",
        "name": "高管",
        "salary": "面议",
        "description": "",
    }
    job = scraper._parse_job(api_item, "三七互娱")
    assert job.salary_desc == "面议"
    assert job.salary_min == 0
    assert job.salary_max == 0


# ---------------------------------------------------------------------------
# scrape() tests (paginated, mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.moka_scraper.RateLimitedClient")
def test_moka_scrape_pagination(mock_client_cls):
    """Test full scrape with two pages of results.

    The scraper uses offset-based pagination.  It continues requesting
    pages until it receives fewer items than DEFAULT_PAGE_SIZE (40).
    """
    scraper = MokaScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
        api_config={
            "moka_company_id": "shein",
            "site_id": 2933,
        },
    )

    # Build a full page of 40 items to trigger a second page request
    page1_items = [
        {"id": str(i), "name": f"职位{i}", "description": ""}
        for i in range(1, 41)
    ]

    resp_page1 = MagicMock()
    resp_page1.json.return_value = {
        "data": {
            "list": page1_items,
            "total": 42,
        }
    }

    # Page 2: 2 more items (partial page, stops pagination)
    page2_items = [
        {"id": "41", "name": "职位41", "description": ""},
        {"id": "42", "name": "职位42", "description": ""},
    ]
    resp_page2 = MagicMock()
    resp_page2.json.return_value = {
        "data": {
            "list": page2_items,
            "total": 42,
        }
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp_page1, resp_page2]

    jobs = scraper.scrape(config)

    assert len(jobs) == 42
    assert jobs[0].company_name == "SHEIN"
    assert jobs[0].job_title == "职位1"
    assert jobs[-1].company_name == "SHEIN"
    assert jobs[-1].job_title == "职位42"
    # Two API calls: offset=0 (page of 40), offset=40 (page of 2)
    assert mock_client.post.call_count == 2


@patch("multi_company_scraper.scrapers.moka_scraper.RateLimitedClient")
def test_moka_scrape_encrypted_response(mock_client_cls):
    """When API returns encrypted data, scraper breaks early with empty list."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="知乎",
        platform="moka",
        base_url="https://app.mokahr.com/campus-recruitment/zhihu/",
        api_config={"moka_company_id": "zhihu"},
    )

    resp = MagicMock()
    resp.json.return_value = {
        "data": "base64encryptedblobhere==="  # encrypted string
    }
    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = resp

    jobs = scraper.scrape(config)
    assert jobs == []
    assert mock_client.post.call_count == 1


@patch("multi_company_scraper.scrapers.moka_scraper.RateLimitedClient")
def test_moka_scrape_no_org_id(mock_client_cls):
    """If moka_company_id cannot be determined, return empty list immediately."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="BadConfig",
        platform="moka",
        base_url="https://example.com/",
        api_config={},
    )
    jobs = scraper.scrape(config)
    assert jobs == []
    mock_client_cls.return_value.post.assert_not_called()


@patch("multi_company_scraper.scrapers.moka_scraper.RateLimitedClient")
def test_moka_scrape_handles_parse_error(mock_client_cls):
    """Per-job parse errors should be logged but not crash the whole scrape."""
    scraper = MokaScraper()
    config = CompanyConfig(
        name="搜狐",
        platform="moka",
        base_url="https://app.mokahr.com/campus_apply/sohu/",
        api_config={"moka_company_id": "sohu"},
    )

    good_item = {"id": "1", "name": "好职位", "description": ""}

    resp = MagicMock()
    resp.json.return_value = {
        "data": {
            "list": [
                good_item,
                None,  # This will crash _parse_job because None has no .get()
                good_item,
            ],
            "total": 3,
        }
    }

    # Page 2: empty
    resp2 = MagicMock()
    resp2.json.return_value = {"data": {"list": [], "total": 3}}

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp, resp2]

    jobs = scraper.scrape(config)
    # The None item is skipped; good items survive
    assert len(jobs) == 2
    assert all(j.company_name == "搜狐" for j in jobs)
