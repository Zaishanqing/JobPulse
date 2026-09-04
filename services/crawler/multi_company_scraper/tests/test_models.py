from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.models.company_config import CompanyConfig


def test_job_data_creation():
    jd = JobData(
        company_name="字节跳动",
        job_title="后端开发工程师",
        job_id="A123",
        department="抖音",
        city="北京",
        district="海淀区",
        job_type="社招",
        experience="3-5年",
        education="本科",
        salary_min=30,
        salary_max=60,
        salary_desc="30K-60K·15薪",
        jd_text="负责后端服务开发...\n要求熟悉Python/Go...",
        jd_responsibility="负责后端服务开发...",
        jd_requirement="熟悉Python/Go...",
        skill_tags="Python,Go,MySQL,Redis",
        benefits_raw="六险一金,免费三餐",
        publish_date="2026-07-01",
        source_url="https://jobs.bytedance.com/xxx",
        source_platform="playwright",
    )
    assert jd.company_name == "字节跳动"
    assert jd.salary_min == 30
    assert jd.salary_max == 60


def test_job_data_defaults():
    jd = JobData(
        company_name="测试公司",
        job_title="测试职位",
        source_platform="test",
    )
    assert jd.job_id == ""
    assert jd.salary_min == 0
    assert jd.salary_max == 0
    assert jd.salary_desc == ""
    assert jd.jd_text == ""
    assert jd.source_url == ""


def test_job_data_to_dict():
    jd = JobData(
        company_name="测试公司",
        job_title="测试职位",
        source_platform="test",
    )
    d = jd.to_dict()
    assert d["company_name"] == "测试公司"
    assert d["salary_min"] == 0
    assert "crawl_time" in d


def test_company_config_from_dict():
    data = {
        "name": "字节跳动",
        "platform": "moka",
        "base_url": "https://job.bytedance.com",
        "enabled": False,
        "selectors": {"next": ".next-btn"},
        "api_config": {"api_key": "xxx"},
    }
    cfg = CompanyConfig.from_dict(data)
    assert cfg.name == "字节跳动"
    assert cfg.platform == "moka"
    assert cfg.base_url == "https://job.bytedance.com"
    assert cfg.enabled is False
    assert cfg.selectors == {"next": ".next-btn"}
    assert cfg.api_config == {"api_key": "xxx"}


def test_company_config_defaults():
    data = {
        "name": "测试",
        "platform": "playwright",
        "base_url": "https://example.com",
    }
    cfg = CompanyConfig.from_dict(data)
    assert cfg.enabled is True
    assert cfg.selectors == {}
    assert cfg.api_config == {}
