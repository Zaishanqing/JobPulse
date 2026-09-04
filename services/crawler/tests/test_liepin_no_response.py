"""Regression tests for an empty Liepin API response."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest


CRAWLER_ROOT = Path(__file__).resolve().parents[1]


class FakePage:
    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def goto(
        self,
        _url: str,
        *,
        wait_until: str | None = None,
        timeout: int | None = None,
        **_kwargs,
    ) -> None:
        pass


def test_do_search_returns_empty_on_no_api_response():
    source = (
        CRAWLER_ROOT
        / "multi_company_scraper"
        / "scrapers"
        / "liepin_scraper.py"
    ).read_text(encoding="utf-8")
    do_search = next(
        (
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "_do_search"
        ),
        None,
    )
    assert do_search is not None
    city_refs = [
        (node.lineno, node.col_offset)
        for node in ast.walk(do_search)
        if isinstance(node, ast.Name) and node.id == "city"
    ]
    assert city_refs == []


def test_do_search_runtime_no_nameerror():
    try:
        from multi_company_scraper.scrapers.liepin_scraper import LiepinScraper
    except ModuleNotFoundError as exc:
        if exc.name in {"playwright", "loguru", "yaml"}:
            pytest.skip(f"optional dependency not installed: {exc.name}")
        raise

    finder = MagicMock(return_value=None)
    scraper = LiepinScraper()
    scraper._find_job_api_response = finder

    result = scraper._do_search(
        page=FakePage(),
        keyword="测试关键词",
        api_responses=[],
        company_name="测试公司",
    )

    assert result == []
    assert isinstance(result, list)
    assert finder.call_count == 2
