import yaml
from pathlib import Path
from multi_company_scraper.models.company_config import CompanyConfig


def load_companies(yaml_path: str) -> list[CompanyConfig]:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [CompanyConfig.from_dict(item) for item in data["companies"]]


def test_load_moka_companies():
    yaml_path = Path(__file__).parent.parent / "config" / "companies.yaml"
    companies = load_companies(str(yaml_path))
    moka = [c for c in companies if c.platform == "moka"]
    assert len(moka) >= 7
    for c in moka:
        assert "mokahr.com" in c.base_url


def test_load_feishu_companies():
    yaml_path = Path(__file__).parent.parent / "config" / "companies.yaml"
    companies = load_companies(str(yaml_path))
    feishu = [c for c in companies if c.platform == "feishu"]
    assert len(feishu) >= 5
    for c in feishu:
        assert "feishu.cn" in c.base_url


def test_all_companies_have_required_fields():
    yaml_path = Path(__file__).parent.parent / "config" / "companies.yaml"
    companies = load_companies(str(yaml_path))
    # Task 5 initial config has 19 companies; Task 13 added remaining 31 for 50 total
    # The source catalogue can grow without invalidating the crawler contract.
    assert len(companies) >= 50
    for c in companies:
        assert c.name
        assert c.platform
        assert c.base_url


def test_platform_values_valid():
    yaml_path = Path(__file__).parent.parent / "config" / "companies.yaml"
    companies = load_companies(str(yaml_path))
    valid_platforms = {"moka", "feishu", "baidu", "tencent", "netease", "zhiye", "playwright", "liepin"}
    for c in companies:
        assert c.platform in valid_platforms, f"{c.name} has invalid platform: {c.platform}"
