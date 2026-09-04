from unittest.mock import patch, MagicMock

import pytest

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.scrapers.zhiye_scraper import ZhiyeScraper


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------

def test_zhiye_supports_platform():
    """ZhiyeScraper should claim platform='zhiye' companies."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )
    assert scraper.supports(config) is True


def test_zhiye_rejects_other_platforms():
    """ZhiyeScraper must not claim non-Zhiye companies."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )
    assert scraper.supports(config) is False


def test_zhiye_rejects_moka_platform():
    """Moka is a different platform and should not match."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _parse_api_job tests
# ---------------------------------------------------------------------------

def test_zhiye_parse_api_job_full():
    """Parse a fully-populated Zhiye API job item."""
    scraper = ZhiyeScraper()
    api_item = {
        "id": "12345",
        "name": "语音识别算法工程师",
        "departmentName": "讯飞研究院",
        "workPlaceName": "合肥",
        "jobType": "社招",
        "experience": "3-5年",
        "education": "硕士",
        "salary": "20K-40K",
        "description": "岗位职责：负责语音识别算法研发\n任职要求：精通深度学习",
        "publishTime": "2026-07-01",
    }

    job = scraper._parse_api_job(
        api_item, "科大讯飞", "https://iflytek.zhiye.com"
    )

    assert job.company_name == "科大讯飞"
    assert job.job_title == "语音识别算法工程师"
    assert job.job_id == "12345"
    assert job.department == "讯飞研究院"
    assert job.city == "合肥"
    assert job.job_type == "社招"
    assert job.experience_raw == "3-5年"
    assert job.education_raw == "硕士"
    assert job.salary_desc == "20K-40K"
    assert job.salary_min == 0
    assert job.salary_max == 0
    assert "语音识别" in job.jd_text
    assert "深度学习" in job.jd_text
    assert job.raw_payload == api_item
    assert job.source_platform == "zhiye"
    assert "zhiye.com" in job.source_url
    assert "12345" in job.source_url


def test_zhiye_parse_api_job_alternate_keys():
    """Parse job using alternate field names."""
    scraper = ZhiyeScraper()
    api_item = {
        "positionId": "99",
        "title": "产品经理",
        "deptName": "产品中心",
        "cityName": "北京",
        "recruitType": "校招",
        "workYear": "应届",
        "degree": "本科",
        "salaryDesc": "面议",
        "jobDesc": "负责AI产品规划",
        "createTime": "2026-06-15",
    }

    job = scraper._parse_api_job(
        api_item, "科大讯飞", "https://iflytek.zhiye.com"
    )

    assert job.company_name == "科大讯飞"
    assert job.job_title == "产品经理"
    assert job.job_id == "99"
    assert job.department == "产品中心"
    assert job.city == "北京"
    assert job.job_type == "校招"
    assert job.experience_raw == "应届"
    assert job.education_raw == "本科"
    assert job.salary_desc == "面议"
    assert job.salary_min == 0
    assert job.source_platform == "zhiye"


def test_zhiye_parse_api_job_minimal():
    """Parse a bare-minimum job item."""
    scraper = ZhiyeScraper()
    api_item = {
        "id": "1",
        "name": "测试岗",
        "description": "",
    }

    job = scraper._parse_api_job(
        api_item, "测试公司", "https://test.zhiye.com"
    )

    assert job.company_name == "测试公司"
    assert job.job_title == "测试岗"
    assert job.job_id == "1"
    assert job.city == ""
    assert job.salary_min == 0
    assert job.source_platform == "zhiye"


# ---------------------------------------------------------------------------
# scrape() tests (mocked HTTP)
# ---------------------------------------------------------------------------

@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_api_pagination(mock_client_cls):
    """Test scrape via API with two pages."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )

    # Page 1: 20 items (full page, triggers another request)
    page1_items = [
        {"id": str(i), "name": f"职位{i}", "description": ""}
        for i in range(1, 21)
    ]

    resp_page1 = MagicMock()
    resp_page1.status_code = 200
    resp_page1.ok = True
    resp_page1.json.return_value = {
        "data": {"list": page1_items, "total": 22},
    }

    # Page 2: 2 items
    page2_items = [
        {"id": "21", "name": "职位21", "description": ""},
        {"id": "22", "name": "职位22", "description": ""},
    ]
    resp_page2 = MagicMock()
    resp_page2.status_code = 200
    resp_page2.ok = True
    resp_page2.json.return_value = {
        "data": {"list": page2_items, "total": 22},
    }

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp_page1, resp_page2]

    jobs = scraper.scrape(config)

    assert len(jobs) == 22
    assert jobs[0].company_name == "科大讯飞"
    assert jobs[0].job_title == "职位1"
    assert jobs[-1].job_title == "职位22"
    assert mock_client.post.call_count == 2


@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_api_blocked_fallback_to_html(mock_client_cls):
    """When API fails, fall back to HTML parsing."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )

    # API call returns 403 (blocked)
    api_resp = MagicMock()
    api_resp.status_code = 403
    api_resp.ok = False

    # HTML page with job cards
    html_resp = MagicMock()
    html_resp.text = """
    <html><body>
        <div class="position-item">
            <a class="title" href="/jobdetail/1">NLP工程师</a>
            <span class="city">合肥</span>
            <span class="department">AI研究院</span>
        </div>
    </body></html>
    """

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = api_resp
    mock_client.get.return_value = html_resp

    jobs = scraper.scrape(config)

    assert len(jobs) == 1
    assert jobs[0].job_title == "NLP工程师"
    assert jobs[0].city == "合肥"
    assert jobs[0].department == "AI研究院"
    assert "zhiye.com" in jobs[0].source_url
    # Both API and HTML paths were tried
    assert mock_client.post.call_count == 1
    assert mock_client.get.call_count >= 1


@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_api_non_json_response(mock_client_cls):
    """When API returns HTML instead of JSON, fall back to HTML path."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )

    # API returns HTML (not JSON)
    api_resp = MagicMock()
    api_resp.status_code = 200
    api_resp.ok = True
    api_resp.json.side_effect = ValueError("not JSON")

    # HTML page
    html_resp = MagicMock()
    html_resp.text = """
    <html><body>
        <a class="position-name" href="/jobdetail/1">测试工程师</a>
    </body></html>
    """

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = api_resp
    mock_client.get.return_value = html_resp

    jobs = scraper.scrape(config)
    assert len(jobs) == 1
    assert jobs[0].job_title == "测试工程师"


@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_handles_parse_error(mock_client_cls):
    """Per-job parse errors should be logged but not crash the whole scrape."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )

    good_item = {"id": "1", "name": "好职位", "description": ""}

    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {
        "data": {
            "list": [good_item, None, good_item],
            "total": 3,
        },
    }

    # Page 2: empty
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.ok = True
    resp2.json.return_value = {"data": {"list": [], "total": 3}}

    mock_client = mock_client_cls.return_value
    mock_client.post.side_effect = [resp, resp2]

    jobs = scraper.scrape(config)
    assert len(jobs) == 2
    assert all(j.company_name == "科大讯飞" for j in jobs)


@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_no_base_url(mock_client_cls):
    """If base_url is empty, return empty list immediately."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="BadConfig",
        platform="zhiye",
        base_url="",
    )
    jobs = scraper.scrape(config)
    assert jobs == []
    mock_client_cls.return_value.post.assert_not_called()


@patch("multi_company_scraper.scrapers.zhiye_scraper.RateLimitedClient")
def test_zhiye_scrape_both_paths_empty(mock_client_cls):
    """When both API and HTML return nothing, return empty list."""
    scraper = ZhiyeScraper()
    config = CompanyConfig(
        name="科大讯飞",
        platform="zhiye",
        base_url="https://iflytek.zhiye.com/",
    )

    # API returns non-JSON
    api_resp = MagicMock()
    api_resp.status_code = 200
    api_resp.ok = True
    api_resp.json.side_effect = ValueError("not JSON")

    # HTML pages return nothing
    html_resp = MagicMock()
    html_resp.text = "<html><body></body></html>"

    mock_client = mock_client_cls.return_value
    mock_client.post.return_value = api_resp
    mock_client.get.return_value = html_resp

    jobs = scraper.scrape(config)
    assert jobs == []
