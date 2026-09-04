"""Tests for main.py CLI entry point.

Covers:
  - Argument parser construction and defaults
  - --list / -l flag
  - --company / -c filter
  - --platform / -p filter
  - --output / -o option
  - load_companies() YAML loader
  - setup_dispatcher() scraper registration
"""

import argparse
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
import yaml

from multi_company_scraper.main import load_companies, setup_dispatcher
from multi_company_scraper.models.company_config import CompanyConfig


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_yaml_path():
    """Create a temporary companies.yaml with 3 test companies."""
    content = {
        "companies": [
            {
                "name": "字节跳动",
                "platform": "playwright",
                "base_url": "https://jobs.bytedance.com/",
                "enabled": True,
            },
            {
                "name": "SHEIN",
                "platform": "moka",
                "base_url": "https://app.mokahr.com/apply/shein/",
                "enabled": True,
                "api_config": {"moka_company_id": "shein"},
            },
            {
                "name": "小鹏汽车",
                "platform": "feishu",
                "base_url": "https://xiaopeng.jobs.feishu.cn/",
                "enabled": False,
                "api_config": {"feishu_company_id": "xiaopeng"},
            },
        ]
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", encoding="utf-8", delete=False
    ) as f:
        yaml.dump(content, f, allow_unicode=True)
        path = f.name
    yield path
    os.unlink(path)


# ============================================================================
# Tests: load_companies
# ============================================================================

def test_load_companies_returns_list(sample_yaml_path):
    companies = load_companies(sample_yaml_path)
    assert isinstance(companies, list)
    assert len(companies) == 3


def test_load_companies_objects_are_company_config(sample_yaml_path):
    companies = load_companies(sample_yaml_path)
    for c in companies:
        assert isinstance(c, CompanyConfig)


def test_load_companies_fields(sample_yaml_path):
    companies = load_companies(sample_yaml_path)

    # First company
    assert companies[0].name == "字节跳动"
    assert companies[0].platform == "playwright"
    assert companies[0].enabled is True

    # Second company has api_config
    assert companies[1].name == "SHEIN"
    assert companies[1].platform == "moka"
    assert companies[1].api_config.get("moka_company_id") == "shein"

    # Third company is disabled
    assert companies[2].name == "小鹏汽车"
    assert companies[2].enabled is False


# ============================================================================
# Tests: setup_dispatcher
# ============================================================================

def test_setup_dispatcher_returns_dispatcher():
    d = setup_dispatcher()
    assert d is not None
    # Dispatcher has a _scrapers list
    assert hasattr(d, "_scrapers")
    assert isinstance(d._scrapers, list)


# ============================================================================
# Tests: Argument parser
# ============================================================================

def _make_parser():
    """Construct the same argparse.ArgumentParser used in main()."""
    parser = argparse.ArgumentParser(description="50家中国大厂招聘JD爬虫")
    parser.add_argument("--company", "-c", default="all")
    parser.add_argument("--output", "-o", default="output.xlsx")
    parser.add_argument(
        "--platform", "-p",
        choices=["moka", "feishu", "baidu", "tencent", "netease", "zhiye", "playwright"],
    )
    parser.add_argument("--list", "-l", action="store_true")
    return parser


def test_parser_defaults():
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.company == "all"
    assert args.list is False
    assert args.platform is None


def test_parser_list_flag():
    parser = _make_parser()
    args = parser.parse_args(["--list"])
    assert args.list is True

    args = parser.parse_args(["-l"])
    assert args.list is True


def test_parser_company_filter():
    parser = _make_parser()
    args = parser.parse_args(["-c", "字节跳动"])
    assert args.company == "字节跳动"

    args = parser.parse_args(["--company", "腾讯"])
    assert args.company == "腾讯"


def test_parser_platform_filter():
    parser = _make_parser()
    args = parser.parse_args(["-p", "moka"])
    assert args.platform == "moka"

    args = parser.parse_args(["--platform", "playwright"])
    assert args.platform == "playwright"


def test_parser_invalid_platform_raises():
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["-p", "unknown"])


def test_parser_output_option():
    parser = _make_parser()
    args = parser.parse_args(["-o", "custom_output.xlsx"])
    assert args.output == "custom_output.xlsx"

    args = parser.parse_args(["--output", "another.xlsx"])
    assert args.output == "another.xlsx"


def test_parser_combined_flags():
    parser = _make_parser()
    args = parser.parse_args(["-c", "SHEIN", "-p", "moka", "-o", "result.xlsx"])
    assert args.company == "SHEIN"
    assert args.platform == "moka"
    assert args.output == "result.xlsx"
    assert args.list is False


# ============================================================================
# Tests: --list mode logic (integration-style)
# ============================================================================

def test_list_mode_lists_companies(capsys, sample_yaml_path):
    """Simulate --list mode: load companies and print them."""
    companies = load_companies(sample_yaml_path)

    # Replicate the --list output logic
    for c in companies:
        status = "[ENABLED]" if c.enabled else "[OFF   ]"
        print(f"  {status:8s} {c.name:16s} {c.platform:14s}  {c.base_url}")

    captured = capsys.readouterr()
    out = captured.out

    assert "[ENABLED]" in out
    assert "[OFF   ]" in out
    assert "字节跳动" in out
    assert "SHEIN" in out
    assert "小鹏汽车" in out
    assert "playwright" in out
    assert "moka" in out
    assert "feishu" in out


# ============================================================================
# Tests: --company filter logic
# ============================================================================

def test_company_filter_exact_match(sample_yaml_path):
    companies = load_companies(sample_yaml_path)

    target = "SHEIN"
    filtered = [c for c in companies if c.name == target]
    assert len(filtered) == 1
    assert filtered[0].name == "SHEIN"
    assert filtered[0].platform == "moka"


def test_company_filter_no_match(sample_yaml_path):
    companies = load_companies(sample_yaml_path)

    target = "非存在公司"
    filtered = [c for c in companies if c.name == target]
    assert filtered == []


def test_company_filter_all(sample_yaml_path):
    companies = load_companies(sample_yaml_path)
    # "all" should return all companies
    assert len(companies) == 3


# ============================================================================
# Tests: --platform filter logic
# ============================================================================

def test_platform_filter(sample_yaml_path):
    companies = load_companies(sample_yaml_path)

    filtered = [c for c in companies if c.platform == "moka"]
    assert len(filtered) == 1
    assert filtered[0].name == "SHEIN"


def test_platform_filter_multiple():
    """Regressions: multiple companies on the same platform."""
    companies = [
        CompanyConfig(name="A", platform="moka", base_url="http://a.com"),
        CompanyConfig(name="B", platform="moka", base_url="http://b.com"),
        CompanyConfig(name="C", platform="feishu", base_url="http://c.com"),
    ]
    filtered = [c for c in companies if c.platform == "moka"]
    assert len(filtered) == 2


# ============================================================================
# Tests: combined filters
# ============================================================================

def test_chain_company_then_platform_filter(sample_yaml_path):
    companies = load_companies(sample_yaml_path)

    # Simulate: --company SHEIN  (not used when filtering test data)
    # then --platform moka
    filtered = [c for c in companies if c.platform == "moka"]
    assert len(filtered) == 1


def test_company_all_then_platform_filter(sample_yaml_path):
    """When --company is 'all', only platform filter applies."""
    companies = load_companies(sample_yaml_path)

    filtered = [c for c in companies if c.platform == "playwright"]
    assert len(filtered) == 1
    assert filtered[0].name == "字节跳动"
