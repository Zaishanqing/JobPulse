from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright", reason="Playwright API package is required")

from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.liepin_scraper import LiepinScraper
from multi_company_scraper.scrapers.playwright_scraper import PlaywrightScraper


@pytest.mark.parametrize(
    ("payload", "expected_score"),
    [
        ([], 0),
        (["not-a-mapping"], 0),
        ([{"title": "short"}], 0),
        ([{"title": "Engineer", "description": "x" * 101}], 2),
        (
            [
                {
                    "title": "Engineer",
                    "city": "Beijing",
                    "salary": "30k",
                    "id": "job-1",
                    "department": "Platform",
                }
            ],
            5,
        ),
    ],
)
def test_playwright_scores_job_items(payload, expected_score):
    assert PlaywrightScraper()._score_job_items(payload) == expected_score


def test_playwright_finds_nested_and_best_api_response():
    scraper = PlaywrightScraper()
    weak = [{"title": "Engineer", "description": "x" * 101}]
    strong = [
        {
            "title": "Engineer",
            "description": "x" * 101,
            "city": "Beijing",
            "salary": "30k",
        }
    ]
    responses = [
        {"url": "weak", "body": {"data": weak}},
        {"url": "strong", "body": {"result": {"items": strong}}},
    ]

    match = scraper._find_job_api_response(responses)

    assert match is not None
    assert match["url"] == "strong"
    assert scraper._score_job_response({"a": {"b": strong}})[1] == strong
    assert scraper._score_job_response({"a": {"b": []}}) == (0, None)
    assert scraper._score_job_response(strong, depth=6) == (0, None)
    assert scraper._find_job_api_response([{"url": "none", "body": {}}]) is None


def test_playwright_extracts_mapping_and_list_variants():
    raw = PlaywrightScraper()._extract_raw_job(
        {
            "positionName": "Platform Engineer",
            "description": "Build systems",
            "qualification": "Know Python",
            "addressDetailList": [
                {"addressDetail": "Beijing"},
                {"name": "Shanghai"},
                "ignored",
            ],
            "department": [{"name": "Infra"}, {"name": ""}],
            "salary": {"desc": "30k-50k"},
            "jobId": 42,
            "experience": {"name": "3 years"},
            "education": {"name": "BS"},
            "publishTime": 1_700_000_000_000,
            "jobType": {"name": "Full-time"},
            "detailUrl": "/jobs/42",
            "skills": ["Python", "SQL"],
            "benefits": ["Bonus", "Insurance"],
            "job_post_info": {"address": "Haidian"},
        }
    )

    assert raw["job_title"] == "Platform Engineer"
    assert raw["jd_text"] == "Build systems\n\nKnow Python"
    assert raw["city"] == "Beijing, Shanghai"
    assert raw["department"] == "Infra"
    assert raw["salary_desc"] == "30k-50k"
    assert raw["job_id"] == "42"
    assert raw["experience"] == "3 years"
    assert raw["education"] == "BS"
    assert raw["publish_date"]
    assert raw["job_type"] == "Full-time"
    assert raw["skill_tags"] == "Python, SQL"
    assert raw["benefits"] == "Bonus, Insurance"
    assert raw["district"] == "Haidian"


def test_playwright_extracts_scalar_and_fallback_variants():
    raw = PlaywrightScraper()._extract_raw_job(
        {
            "name": "Data Engineer",
            "content": "Build pipelines",
            "city": {"city": "Shenzhen"},
            "dept": {"department": "Data"},
            "salaryDesc": "20k-30k",
            "id": "job-1",
            "workExperience": "2 years",
            "degree": "MS",
            "publishDate": "2026-08-01T10:00:00",
            "employmentType": "Permanent",
            "url": "https://example.test/job-1",
            "tags": "Python",
            "welfare": "Meal",
        }
    )
    fallback = PlaywrightScraper()._extract_raw_job(
        {
            "title": "Fallback",
            "cityList": [{"city": "Guangzhou"}, "Remote"],
            "department": "Engineering",
            "salaryMin": 10,
            "salaryMax": 20,
            "yoe_min": 1,
            "yoe_max": 3,
            "job_post_info": {
                "education": "College",
                "min_salary": 11,
                "max_salary": 22,
            },
        }
    )

    assert raw["city"] == "Shenzhen"
    assert raw["department"] == "Data"
    assert raw["publish_date"] == "2026-08-01"
    assert raw["skill_tags"] == "Python"
    assert raw["benefits"] == "Meal"
    assert fallback["city"] == "Guangzhou, Remote"
    assert fallback["salary_desc"] == "10-20"
    assert fallback["experience"] == "1-3年"
    assert fallback["education"] == "College"


@pytest.mark.parametrize(
    ("body", "request_body", "expected"),
    [
        ({}, json.dumps({"pageNo": 1, "tenant": "a"}), ("POST", None, True)),
        (
            {},
            json.dumps({"data": {"currentPage": 1}, "tenant": "a"}),
            ("POST", None, True),
        ),
        ({"data": {"pageIndex": 1, "pageSize": 50}}, "", ("POST", None, True)),
        ({}, "not-json", ("POST", "pageIndex={page}&pageSize=10", False)),
    ],
)
def test_playwright_detects_pagination_patterns(body, request_body, expected):
    method, form_template, json_template = PlaywrightScraper()._detect_api_pattern(
        body, request_body
    )

    assert method == expected[0]
    assert form_template == expected[1]
    assert (json_template is not None) is expected[2]
    if json_template:
        assert "{page}" in json_template


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"total": 12}, 12),
        ({"count": 3.8}, 3),
        ({"data": {"totalSize": 9}}, 9),
        ({"result": {"totalCount": 7}}, 7),
        ({"total": "unknown"}, None),
        ([], None),
    ],
)
def test_playwright_extracts_total_count(body, expected):
    assert PlaywrightScraper()._extract_total_from_body(body) == expected


def test_playwright_direct_pagination_branches():
    scraper = PlaywrightScraper()
    page = MagicMock()
    responses: list[dict] = []
    page.evaluate.return_value = {"items": []}

    assert scraper._paginate_direct(
        page, "https://example.test/jobs", "POST", None, 1, responses,
        json_template='{"page": "{page}"}',
    ) is True
    assert responses[-1]["body"] == {"items": []}
    assert scraper._paginate_direct(
        page, "https://example.test/jobs", "POST", "page={page}", 2, responses
    ) is True
    assert scraper._paginate_direct(
        page, "https://example.test/jobs", "POST", None, 2, responses
    ) is False

    page.evaluate.return_value = None
    assert scraper._paginate_direct(
        page, "https://example.test/jobs", "POST", "page={page}", 3, responses
    ) is False
    page.evaluate.side_effect = RuntimeError("network")
    assert scraper._paginate_direct(
        page, "https://example.test/jobs", "POST", "page={page}", 4, responses
    ) is False


def test_liepin_response_login_cookie_and_pagination_branches(tmp_path, monkeypatch):
    scraper = LiepinScraper()
    scraper._cookie_file = tmp_path / "cookies.json"
    context = MagicMock()

    assert scraper._load_cookies(context) is False
    scraper._cookie_file.write_text("not-json", encoding="utf-8")
    assert scraper._load_cookies(context) is False
    scraper._cookie_file.write_text('[{"name": "token", "value": "x"}]', encoding="utf-8")
    assert scraper._load_cookies(context) is True
    context.cookies.return_value = [{"name": "token", "value": "x"}]
    scraper._save_cookies(context)

    page = MagicMock()
    page.query_selector.side_effect = [object()]
    assert scraper._is_logged_in(page) is True
    page.query_selector.side_effect = [None, object()]
    assert scraper._is_logged_in(page) is False
    page.query_selector.side_effect = [None, None]
    page.context.cookies.return_value = [
        {"name": "analytics", "value": "x"},
        {"name": "auth_token", "value": "secret"},
    ]
    assert scraper._is_logged_in(page) is True
    page.context.cookies.return_value = []
    assert scraper._is_logged_in(page) is False
    page.query_selector.side_effect = RuntimeError("DOM")
    assert scraper._is_logged_in(page) is False

    responses: list[dict] = []
    page = MagicMock()
    page.evaluate.return_value = {"data": {}}
    assert scraper._paginate_liepin(page, 1, responses, keyword="Python") is True
    page.evaluate.return_value = []
    assert scraper._paginate_liepin(page, 2, responses) is False
    page.evaluate.side_effect = RuntimeError("blocked")
    assert scraper._paginate_liepin(page, 3, responses) is False


def test_liepin_finds_specific_response_and_parses_edge_cards(monkeypatch):
    scraper = LiepinScraper()
    card = {"comp": {}, "job": {"positionId": "p-1", "url": "bad-url"}}
    body = {"data": {"data": {"jobCardList": [card]}}}

    match = scraper._find_job_api_response(
        [
            {"url": "ignored", "body": []},
            {"url": "broken", "body": {"data": []}},
            {"url": "matched", "body": body},
        ]
    )
    jobs, ids = scraper._parse_api_response(
        {"body": {"data": {"data": {"jobCardList": [None, card, {"job": {}}]}}}},
        "Fallback Company",
    )

    assert match is not None and match["url"] == "matched"
    assert ids == {"p-1"}
    assert jobs[0].company_name == "Fallback Company"
    generated = scraper._extract_raw_job({"comp": {}, "job": {"jobId": "42"}})
    assert generated["source_url"].endswith("/42.shtml")
    assert len(scraper._get_user_agents()) == 3
