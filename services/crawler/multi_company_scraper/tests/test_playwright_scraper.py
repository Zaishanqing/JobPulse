import pytest

pytest.importorskip("playwright", reason="optional browser dependency is not installed")

from unittest.mock import patch, MagicMock, PropertyMock

from multi_company_scraper.models.company_config import CompanyConfig
from multi_company_scraper.models.job_data import JobData
from multi_company_scraper.scrapers.playwright_scraper import (
    PlaywrightScraper,
    DEFAULT_SELECTORS,
    USER_AGENTS,
)


# ---------------------------------------------------------------------------
# supports() tests
# ---------------------------------------------------------------------------


def test_supports_playwright_platform():
    """PlaywrightScraper should claim platform='playwright' companies."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="字节跳动",
        platform="playwright",
        base_url="https://jobs.bytedance.com/",
    )
    assert scraper.supports(config) is True


def test_supports_rejects_moka_platform():
    """Moka is a different platform and should not match."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="SHEIN",
        platform="moka",
        base_url="https://app.mokahr.com/apply/shein/",
    )
    assert scraper.supports(config) is False


def test_supports_rejects_feishu_platform():
    """Feishu is a different platform and should not match."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="小鹏汽车",
        platform="feishu",
        base_url="https://xiaopeng.jobs.feishu.cn/",
    )
    assert scraper.supports(config) is False


def test_supports_rejects_baidu_platform():
    """Baidu-specific scraper should not be claimed by Playwright."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="百度",
        platform="baidu",
        base_url="https://talent.baidu.com/",
    )
    assert scraper.supports(config) is False


# ---------------------------------------------------------------------------
# _get_selectors() tests
# ---------------------------------------------------------------------------


def test_get_selectors_defaults_only():
    """When company has no selectors, return defaults unchanged."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="京东",
        platform="playwright",
        base_url="https://zhaopin.jd.com/",
    )
    result = scraper._get_selectors(config)
    assert result == DEFAULT_SELECTORS


def test_get_selectors_custom_override():
    """Company-specific selectors should override matching default keys."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="字节跳动",
        platform="playwright",
        base_url="https://jobs.bytedance.com/",
        selectors={
            "job_list_container": ".position-list",
            "job_card": ".position-item",
            "job_title": ".position-title",
        },
    )
    result = scraper._get_selectors(config)
    assert result["job_list_container"] == ".position-list"
    assert result["job_card"] == ".position-item"
    assert result["job_title"] == ".position-title"
    # Non-overridden keys should still use defaults
    assert result["job_city"] == DEFAULT_SELECTORS["job_city"]
    assert result["next_page"] == DEFAULT_SELECTORS["next_page"]


def test_get_selectors_partial_override():
    """Only overrode one key — rest are defaults."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="阿里巴巴",
        platform="playwright",
        base_url="https://talent.alibaba.com/",
        selectors={"job_list_container": ".custom-container"},
    )
    result = scraper._get_selectors(config)
    assert result["job_list_container"] == ".custom-container"
    assert len(result) == len(DEFAULT_SELECTORS)
    # Other keys unchanged
    for key in DEFAULT_SELECTORS:
        assert key in result


def test_get_selectors_empty_dict():
    """Explicit empty dict should be handled same as no selectors."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="未知公司",
        platform="playwright",
        base_url="https://jobs.example.com/",
        selectors={},
    )
    result = scraper._get_selectors(config)
    assert result == DEFAULT_SELECTORS


def test_get_selectors_none_selectors():
    """When selectors is None (not passed), defaults should be returned."""
    scraper = PlaywrightScraper()
    config = CompanyConfig(
        name="未知公司",
        platform="playwright",
        base_url="https://jobs.example.com/",
    )
    result = scraper._get_selectors(config)
    assert result == DEFAULT_SELECTORS


# ---------------------------------------------------------------------------
# _try_extract() tests
# ---------------------------------------------------------------------------


def test_try_extract_inner_text():
    """Should extract inner_text when no attribute is specified."""
    scraper = PlaywrightScraper()
    mock_el = MagicMock()
    mock_el.query_selector.return_value = MagicMock(
        inner_text=MagicMock(return_value="高级工程师")
    )

    result = scraper._try_extract(mock_el, ".job-title")
    assert result == "高级工程师"
    mock_el.query_selector.assert_called_once_with(".job-title")


def test_try_extract_attribute():
    """Should extract the specified attribute from the matched element."""
    scraper = PlaywrightScraper()
    mock_sub_el = MagicMock()
    mock_sub_el.get_attribute.return_value = "/job/12345"
    mock_el = MagicMock()
    mock_el.query_selector.return_value = mock_sub_el

    result = scraper._try_extract(mock_el, "a.detail-link", "href")
    assert result == "/job/12345"
    mock_sub_el.get_attribute.assert_called_once_with("href")


def test_try_extract_no_match():
    """When no element matches, return empty string."""
    scraper = PlaywrightScraper()
    mock_el = MagicMock()
    mock_el.query_selector.return_value = None

    result = scraper._try_extract(mock_el, ".nonexistent")
    assert result == ""


def test_try_extract_query_selector_raises():
    """Exception during query_selector should return empty string."""
    scraper = PlaywrightScraper()
    mock_el = MagicMock()
    mock_el.query_selector.side_effect = Exception("DOM error")

    result = scraper._try_extract(mock_el, ".job-title")
    assert result == ""


def test_try_extract_inner_text_raises():
    """Exception in inner_text() should return empty string."""
    scraper = PlaywrightScraper()
    mock_sub_el = MagicMock()
    mock_sub_el.inner_text.side_effect = Exception("text error")
    mock_el = MagicMock()
    mock_el.query_selector.return_value = mock_sub_el

    result = scraper._try_extract(mock_el, ".job-title")
    assert result == ""


def test_try_extract_multiple_selectors_first_wins():
    """Comma-separated selectors: first match is returned."""
    scraper = PlaywrightScraper()
    mock_el = MagicMock()
    # First selector returns None, second returns a match
    mock_el.query_selector.side_effect = [
        None,
        MagicMock(inner_text=MagicMock(return_value="后端开发")),
    ]

    result = scraper._try_extract(mock_el, ".not-there, .job-title, .other")
    assert result == "后端开发"
    assert mock_el.query_selector.call_count == 2


def test_try_extract_multiple_selectors_none_match():
    """None of the comma-separated selectors match."""
    scraper = PlaywrightScraper()
    mock_el = MagicMock()
    mock_el.query_selector.return_value = None

    result = scraper._try_extract(mock_el, ".a, .b, .c")
    assert result == ""


def test_try_extract_element_without_query_selector():
    """Element that does not have query_selector method returns ''."""
    scraper = PlaywrightScraper()
    plain_obj = object()  # no query_selector

    result = scraper._try_extract(plain_obj, ".anything")
    assert result == ""


# ---------------------------------------------------------------------------
# _resolve_url tests
# ---------------------------------------------------------------------------


def test_resolve_url_absolute():
    """Absolute URLs should be returned as-is."""
    scraper = PlaywrightScraper()
    result = scraper._resolve_url(
        "https://jobs.bytedance.com/",
        "https://jobs.bytedance.com/position/123",
    )
    assert result == "https://jobs.bytedance.com/position/123"


def test_resolve_url_relative():
    """Relative URLs should be resolved against base_url."""
    scraper = PlaywrightScraper()
    result = scraper._resolve_url(
        "https://jobs.bytedance.com/",
        "/position/123",
    )
    assert result == "https://jobs.bytedance.com/position/123"


def test_resolve_url_relative_no_leading_slash():
    """Relative URLs without leading slash should still resolve correctly."""
    scraper = PlaywrightScraper()
    result = scraper._resolve_url(
        "https://jobs.bytedance.com/list",
        "detail/123",
    )
    assert result == "https://jobs.bytedance.com/detail/123"


# ---------------------------------------------------------------------------
# scrape() mock-based tests
# ---------------------------------------------------------------------------


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_single_page_no_detail_pages(mock_sync_pw):
    """Scrape a single listing page where no job has a detail link."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="字节跳动",
        platform="playwright",
        base_url="https://jobs.bytedance.com/",
    )

    # --- Build mock Playwright hierarchy ---
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    # Mock: page has a job-list container with 2 cards
    mock_card_1 = MagicMock()
    mock_card_2 = MagicMock()

    # Card 1: "前端工程师", no detail link
    mock_card_1.query_selector.side_effect = lambda sel: {
        ".position-title": MagicMock(
            inner_text=MagicMock(return_value="前端工程师")
        ),
        ".position-city": MagicMock(
            inner_text=MagicMock(return_value="北京")
        ),
        ".position-dept": MagicMock(
            inner_text=MagicMock(return_value="技术部")
        ),
        ".position-salary": MagicMock(
            inner_text=MagicMock(return_value="30K-50K")
        ),
        ".position-exp": MagicMock(
            inner_text=MagicMock(return_value="3-5年")
        ),
        ".position-edu": MagicMock(
            inner_text=MagicMock(return_value="本科")
        ),
        ".position-tags": MagicMock(
            inner_text=MagicMock(return_value="React")
        ),
        ".position-link": None,  # no detail link
    }.get(sel.split(",")[0].strip())

    # Card 2: "后端工程师", has detail link
    mock_card_2.query_selector.side_effect = lambda sel: {
        ".position-title": MagicMock(
            inner_text=MagicMock(return_value="后端工程师")
        ),
        ".position-city": MagicMock(
            inner_text=MagicMock(return_value="上海")
        ),
        ".position-dept": MagicMock(
            inner_text=MagicMock(return_value="后端组")
        ),
        ".position-salary": MagicMock(
            inner_text=MagicMock(return_value="40K-60K")
        ),
        ".position-exp": MagicMock(
            inner_text=MagicMock(return_value="5-10年")
        ),
        ".position-edu": MagicMock(
            inner_text=MagicMock(return_value="硕士")
        ),
        ".position-tags": MagicMock(
            inner_text=MagicMock(return_value="Go, Python")
        ),
        ".position-link": MagicMock(
            get_attribute=MagicMock(return_value="/job/54321")
        ),
    }.get(sel.split(",")[0].strip())

    # Container with cards
    mock_container = MagicMock()
    mock_container.query_selector_all.return_value = [
        mock_card_1,
        mock_card_2,
    ]
    mock_page.query_selector.return_value = mock_container

    # No next page
    mock_page.query_selector.side_effect = lambda sel, **kw: {
        ".position-list": mock_container,
        ".pagination .next": None,
    }.get(sel.split(",")[0].strip())

    # Company uses custom selectors
    config.selectors = {
        "job_list_container": ".position-list",
        "job_card": ".position-item",
        "job_title": ".position-title",
        "job_city": ".position-city",
        "job_department": ".position-dept",
        "job_salary": ".position-salary",
        "job_experience": ".position-exp",
        "job_education": ".position-edu",
        "job_tags": ".position-tags",
        "detail_link": ".position-link",
    }

    jobs = scraper.scrape(config)

    assert len(jobs) == 2
    assert jobs[0].company_name == "字节跳动"
    assert jobs[0].job_title == "前端工程师"
    assert jobs[0].city == "北京"
    assert jobs[1].job_title == "后端工程师"
    assert jobs[1].city == "上海"
    # All jobs should have source_platform set
    for job in jobs:
        assert job.source_platform == "playwright"
    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_with_detail_pages(mock_sync_pw):
    """Scrape jobs where each card links to a detail page."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="京东",
        platform="playwright",
        base_url="https://zhaopin.jd.com/",
    )

    # --- Build mock Playwright hierarchy ---
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_list_page = MagicMock()
    mock_detail_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    # List page
    mock_context.new_page.return_value = mock_list_page

    # Card with detail URL
    mock_card = MagicMock()
    mock_card.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="数据分析师")
        ),
        ".job-city": MagicMock(
            inner_text=MagicMock(return_value="深圳")
        ),
        ".job-dept": MagicMock(
            inner_text=MagicMock(return_value="数据部")
        ),
        ".job-salary": MagicMock(
            inner_text=MagicMock(return_value="25K-45K")
        ),
        ".job-exp": MagicMock(
            inner_text=MagicMock(return_value="1-3年")
        ),
        ".job-edu": MagicMock(
            inner_text=MagicMock(return_value="本科")
        ),
        ".job-tags": MagicMock(
            inner_text=MagicMock(return_value="SQL, Python")
        ),
        ".job-link": MagicMock(
            get_attribute=MagicMock(
                return_value="https://zhaopin.jd.com/detail/999"
            )
        ),
    }.get(sel.split(",")[0].strip())

    mock_container = MagicMock()
    mock_container.query_selector_all.return_value = [mock_card]
    mock_list_page.query_selector.side_effect = lambda sel, **kw: {
        ".job-list": mock_container,
        ".pagination .next": None,
    }.get(sel.split(",")[0].strip())

    # Detail page setup — context.new_page() is called again for detail
    mock_context.new_page.side_effect = [mock_list_page, mock_detail_page]
    mock_detail_page.query_selector.return_value = MagicMock(
        inner_text=MagicMock(
            return_value=(
                "岗位职责：负责数据分析平台建设\n"
                "任职要求：精通SQL，熟悉Python"
            )
        )
    )

    # Company uses custom selectors
    config.selectors = {
        "job_list_container": ".job-list",
        "job_card": ".job-item",
        "job_title": ".job-title",
        "job_city": ".job-city",
        "job_department": ".job-dept",
        "job_salary": ".job-salary",
        "job_experience": ".job-exp",
        "job_education": ".job-edu",
        "job_tags": ".job-tags",
        "detail_link": ".job-link",
        "detail_jd": ".job-desc",
    }

    jobs = scraper.scrape(config)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company_name == "京东"
    assert job.job_title == "数据分析师"
    assert job.city == "深圳"
    assert job.salary_desc == "25K-45K"
    assert job.source_url == "https://zhaopin.jd.com/detail/999"
    assert "数据分析平台" in job.jd_text
    assert "精通SQL" in job.jd_text
    assert job.jd_responsibility == ""
    assert job.jd_requirement == ""
    assert job.source_platform == "playwright"

    # Detail page should have been opened and closed
    mock_detail_page.close.assert_called_once()
    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_no_jobs_found(mock_sync_pw):
    """When no job cards are found, return empty list."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="阿里巴巴",
        platform="playwright",
        base_url="https://talent.alibaba.com/",
    )

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    # No container found, no cards
    mock_page.query_selector.return_value = None
    mock_page.query_selector_all.return_value = []

    jobs = scraper.scrape(config)

    assert jobs == []
    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_navigation_timeout(mock_sync_pw):
    """When page.goto raises, return empty list gracefully."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="超时公司",
        platform="playwright",
        base_url="https://timeout.example.com/",
    )

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    # Simulate navigation timeout
    mock_page.goto.side_effect = Exception("Navigation timeout after 60000ms")

    jobs = scraper.scrape(config)

    assert jobs == []
    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_detail_page_navigation_fails(mock_sync_pw):
    """If a detail page fails to load, the list-page data is preserved."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="容错公司",
        platform="playwright",
        base_url="https://jobs.example.com/",
    )

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_list_page = MagicMock()
    mock_detail_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.side_effect = [mock_list_page, mock_detail_page]

    # Card with detail URL but detail page will fail
    mock_card = MagicMock()
    mock_card.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="测试工程师")
        ),
        ".job-city": MagicMock(
            inner_text=MagicMock(return_value="杭州")
        ),
        ".job-dept": None,
        ".job-salary": None,
        ".job-exp": None,
        ".job-edu": None,
        ".job-tags": None,
        ".job-link": MagicMock(
            get_attribute=MagicMock(return_value="/job/111")
        ),
    }.get(sel.split(",")[0].strip())

    mock_container = MagicMock()
    mock_container.query_selector_all.return_value = [mock_card]
    mock_list_page.query_selector.side_effect = lambda sel, **kw: {
        ".job-list": mock_container,
        ".pagination .next": None,
    }.get(sel.split(",")[0].strip())

    # Detail page goto fails
    mock_detail_page.goto.side_effect = Exception("Connection refused")

    config.selectors = {
        "job_list_container": ".job-list",
        "job_card": ".job-item",
        "job_title": ".job-title",
        "job_city": ".job-city",
        "job_department": ".job-dept",
        "job_salary": ".job-salary",
        "job_experience": ".job-exp",
        "job_education": ".job-edu",
        "job_tags": ".job-tags",
        "detail_link": ".job-link",
    }

    jobs = scraper.scrape(config)

    # Even though detail failed, we should have 1 job from list-page data
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_title == "测试工程师"
    assert job.city == "杭州"
    assert job.company_name == "容错公司"
    assert job.source_platform == "playwright"

    mock_detail_page.close.assert_called_once()
    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_pagination(mock_sync_pw):
    """Scraper should paginate until no next-page element is found.

    We mock out the internal _has_next_page and _go_next_page methods
    directly, since end-to-end mocking of Playwright's page state across
    pagination cycles is fragile with plain MagicMock side_effects.
    """
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="多页公司",
        platform="playwright",
        base_url="https://jobs.multipage.com/",
    )

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_list_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_list_page

    # Page 1 card
    mock_card_p1 = MagicMock()
    mock_card_p1.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="岗位A")
        ),
        ".job-city": MagicMock(
            inner_text=MagicMock(return_value="北京")
        ),
        ".job-dept": None,
        ".job-salary": None,
        ".job-exp": None,
        ".job-edu": None,
        ".job-tags": None,
        ".job-link": None,
    }.get(sel.split(",")[0].strip())

    mock_container_p1 = MagicMock()
    mock_container_p1.query_selector_all.return_value = [mock_card_p1]

    # Page 2 card
    mock_card_p2 = MagicMock()
    mock_card_p2.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="岗位B")
        ),
        ".job-city": MagicMock(
            inner_text=MagicMock(return_value="上海")
        ),
        ".job-dept": None,
        ".job-salary": None,
        ".job-exp": None,
        ".job-edu": None,
        ".job-tags": None,
        ".job-link": None,
    }.get(sel.split(",")[0].strip())

    mock_container_p2 = MagicMock()
    mock_container_p2.query_selector_all.return_value = [mock_card_p2]

    # Container selector returns p1 then p2
    mock_list_page.query_selector.side_effect = [
        mock_container_p1,
        mock_container_p2,
    ]

    config.selectors = {
        "job_list_container": ".job-list",
        "job_card": ".job-item",
        "job_title": ".job-title",
        "job_city": ".job-city",
        "job_department": ".job-dept",
        "job_salary": ".job-salary",
        "job_experience": ".job-exp",
        "job_education": ".job-edu",
        "job_tags": ".job-tags",
        "detail_link": ".job-link",
        "next_page": ".pagination .next",
    }

    # Patch internal pagination methods to simulate exactly 2 pages
    with patch.object(
        scraper, "_has_next_page", side_effect=[True, False]
    ) as mock_has_next, patch.object(
        scraper, "_go_next_page"
    ) as mock_go_next:

        jobs = scraper.scrape(config)

        # Two pages scraped
        assert len(jobs) == 2
        assert jobs[0].job_title == "岗位A"
        assert jobs[0].city == "北京"
        assert jobs[1].job_title == "岗位B"
        assert jobs[1].city == "上海"

        # Pagination methods called correctly
        assert mock_has_next.call_count == 2
        mock_go_next.assert_called_once()

    mock_browser.close.assert_called_once()


@patch("multi_company_scraper.scrapers.playwright_scraper.sync_playwright")
def test_scrape_skips_empty_cards(mock_sync_pw):
    """Cards with no title text should be skipped."""
    scraper = PlaywrightScraper()

    config = CompanyConfig(
        name="测试公司",
        platform="playwright",
        base_url="https://jobs.test.com/",
    )

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_list_page = MagicMock()

    mock_sync_pw.return_value.__enter__.return_value = MagicMock(
        chromium=MagicMock(
            launch=MagicMock(return_value=mock_browser)
        )
    )
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_list_page

    # Empty card (no title)
    mock_empty = MagicMock()
    mock_empty.query_selector.return_value = None  # title not found

    # Valid card
    mock_valid = MagicMock()
    mock_valid.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="有效岗位")
        ),
        ".job-city": MagicMock(
            inner_text=MagicMock(return_value="广州")
        ),
        ".job-dept": None,
        ".job-salary": None,
        ".job-exp": None,
        ".job-edu": None,
        ".job-tags": None,
        ".job-link": None,
    }.get(sel.split(",")[0].strip())

    # Another empty
    mock_empty2 = MagicMock()
    mock_empty2.query_selector.side_effect = lambda sel: {
        ".job-title": MagicMock(
            inner_text=MagicMock(return_value="")  # empty string title
        ),
    }.get(sel.split(",")[0].strip())

    mock_container = MagicMock()
    mock_container.query_selector_all.return_value = [
        mock_empty,
        mock_valid,
        mock_empty2,
    ]
    mock_list_page.query_selector.side_effect = lambda sel, **kw: {
        ".job-list": mock_container,
        ".pagination .next": None,
    }.get(sel.split(",")[0].strip())

    config.selectors = {
        "job_list_container": ".job-list",
        "job_card": ".job-item",
        "job_title": ".job-title",
        "job_city": ".job-city",
        "job_department": ".job-dept",
        "job_salary": ".job-salary",
        "job_experience": ".job-exp",
        "job_education": ".job-edu",
        "job_tags": ".job-tags",
        "detail_link": ".job-link",
    }

    jobs = scraper.scrape(config)

    assert len(jobs) == 1
    assert jobs[0].job_title == "有效岗位"
    assert jobs[0].city == "广州"


# ---------------------------------------------------------------------------
# Class attribute tests
# ---------------------------------------------------------------------------


def test_scraper_name():
    """The name attribute should be 'playwright'."""
    scraper = PlaywrightScraper()
    assert scraper.name == "playwright"


def test_scraper_max_pages():
    """The MAX_PAGES safety valve should be set."""
    scraper = PlaywrightScraper()
    assert 0 < scraper.MAX_PAGES <= 100


def test_user_agents_is_non_empty_list():
    """The local USER_AGENTS list should not be empty."""
    assert len(USER_AGENTS) > 0
    assert all(isinstance(ua, str) for ua in USER_AGENTS)


def test_detail_enrichment_preserves_raw_contract():
    """Detail API data updates raw text, hash, and provenance only."""
    scraper = PlaywrightScraper()
    page = MagicMock()
    detail = {
        "description": "Build data services",
        "requirement": "Source-provided SQL requirement",
    }
    page.evaluate.return_value = [{"code": 0, "data": detail}]
    job = JobData("Example", "Engineer", "playwright", job_id="job-1")

    scraper._enrich_job_details(
        page,
        [job],
        {"https://example.test/job/list": "{}"},
        "https://example.test/job/list",
    )

    assert "[source.description]" in job.jd_text
    assert "[source.requirement]" in job.jd_text
    assert job.raw_text_status == "completed"
    assert job.raw_payload["detail_payload"] == detail
    assert job.jd_requirement == ""


def test_default_selectors_have_required_keys():
    """DEFAULT_SELECTORS should contain all expected keys."""
    required = [
        "job_list_container",
        "job_card",
        "job_title",
        "job_city",
        "job_department",
        "job_salary",
        "job_experience",
        "job_education",
        "job_tags",
        "next_page",
        "detail_link",
        "detail_container",
        "detail_title",
        "detail_jd",
    ]
    for key in required:
        assert key in DEFAULT_SELECTORS, f"Missing key: {key}"
