"""Tests for LiepinScraper."""

import json

import pytest

pytest.importorskip("playwright", reason="optional browser dependency is not installed")

from multi_company_scraper.scrapers.liepin_scraper import LiepinScraper
from multi_company_scraper.models.company_config import CompanyConfig


class TestLiepinSupports:
    def test_supports_liepin_platform(self):
        scraper = LiepinScraper()
        company = CompanyConfig(
            name="猎聘", platform="liepin", base_url="https://www.liepin.com/"
        )
        assert scraper.supports(company) is True

    def test_rejects_other_platforms(self):
        scraper = LiepinScraper()
        company = CompanyConfig(
            name="字节跳动", platform="playwright", base_url="https://jobs.bytedance.com/"
        )
        assert scraper.supports(company) is False


class TestExtractRawJob:
    def setup_method(self):
        self.scraper = LiepinScraper()

    def test_extract_basic_fields(self):
        api_job = {
            "comp": {"compName": "某科技公司"},
            "job": {
                "title": "Python开发工程师",
                "jobId": "12345",
                "cityName": "北京",
                "salary": "15k-25k",
                "workYear": "3-5年",
                "eduLevel": "本科",
                "pubDate": "2026-07-15",
                "jobDetailUrl": "https://www.liepin.com/job/12345",
            },
        }
        raw = self.scraper._extract_raw_job(api_job)
        assert raw["job_title"] == "Python开发工程师"
        assert raw["job_id"] == "12345"
        assert raw["company_name"] == "某科技公司"
        assert raw["city"] == "北京"
        assert raw["salary_desc"] == "15k-25k"
        assert raw["experience"] == "3-5年"
        assert raw["education"] == "本科"
        assert raw["publish_date"] == "2026-07-15"
        assert raw["source_url"] == "https://www.liepin.com/job/12345"

    def test_extract_missing_fields_returns_empty_strings(self):
        api_job = {"comp": {}, "job": {}}
        raw = self.scraper._extract_raw_job(api_job)
        assert raw["job_title"] == ""
        assert raw["job_id"] == ""


class TestPreserveRawTags:
    def test_preserves_mapping_labels(self):
        api_job = {
            "comp": {},
            "job": {
                "jobLabels": [
                    {"name": "Python"},
                    {"name": "Django"},
                ]
            },
        }
        raw = LiepinScraper()._extract_raw_job(api_job)
        assert raw["raw_payload"]["job"]["jobLabels"] == api_job["job"]["jobLabels"]

    def test_preserves_string_labels(self):
        api_job = {
            "comp": {},
            "job": {"jobLabels": ["Python", "FastAPI"]},
        }
        raw = LiepinScraper()._extract_raw_job(api_job)
        assert raw["raw_payload"]["job"]["jobLabels"] == ["Python", "FastAPI"]

    def test_missing_labels_remain_absent(self):
        api_job = {"comp": {}, "job": {}}
        raw = LiepinScraper()._extract_raw_job(api_job)
        assert "jobLabels" not in raw["raw_payload"]["job"]


class TestParseApiResponse:
    def test_parses_valid_response(self):
        scraper = LiepinScraper()
        api_info = {
            "url": "https://api-c.liepin.com/api/...",
            "body": {
                "data": {
                    "data": {
                        "jobCardList": [
                            {
                                "comp": {"compName": "测试公司"},
                                "job": {
                                    "title": "测试工程师",
                                    "jobId": "111",
                                    "salary": "10k-15k",
                                },
                            }
                        ]
                    }
                }
            },
        }
        jobs, ids = scraper._parse_api_response(api_info, "fallback")
        assert len(jobs) == 1
        assert jobs[0].job_title == "测试工程师"
        assert jobs[0].company_name == "测试公司"
        assert "111" in ids

    def test_empty_body_returns_empty(self):
        scraper = LiepinScraper()
        api_info = {"url": "...", "body": {}}
        jobs, ids = scraper._parse_api_response(api_info, "fb")
        assert jobs == []
        assert ids == set()

    def test_non_list_jobcard_returns_empty(self):
        scraper = LiepinScraper()
        api_info = {
            "url": "...",
            "body": {"data": {"data": {"jobCardList": "not_a_list"}}},
        }
        jobs, ids = scraper._parse_api_response(api_info, "fb")
        assert jobs == []


class TestLoadSearchParams:
    def test_loads_keywords_and_cities(self):
        scraper = LiepinScraper()
        params = scraper._load_search_params()
        assert "keywords" in params
        assert "cities" in params
        assert isinstance(params["keywords"], list)
        assert isinstance(params["cities"], dict)
        assert "Java" in params["keywords"]
        assert "北京" in params["cities"]
