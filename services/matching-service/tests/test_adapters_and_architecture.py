from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from app.infrastructure.http_sources import HttpCVProfileSource
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)

ROOT = Path(__file__).parents[1]


def test_memory_adapters_are_isolated_and_do_not_leak_mutation():
    source = {"cv_1": {"contract_version": "cv-match-profile.v1"}}
    adapter = InMemoryCVProfileSource(source)
    first = adapter.fetch_cv_profile("cv_1")
    assert isinstance(first, dict)
    first["changed"] = True

    assert adapter.fetch_cv_profile("cv_1") == source["cv_1"]
    with pytest.raises(KeyError):
        InMemoryPositionProfileSource().fetch_position_profile("missing")


def test_http_adapter_reads_explicit_contract(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"contract_version": "cv-match-profile.v1"}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, headers):
            assert url == "http://cv-service/contracts/cv/cv%2F1"
            assert headers == {"Accept": "application/json"}
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    adapter = HttpCVProfileSource(
        "http://cv-service/",
        "/contracts/cv/",
        timeout_seconds=2,
    )

    assert adapter.fetch_cv_profile("cv/1")["contract_version"] == (
        "cv-match-profile.v1"
    )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def test_domain_and_application_have_no_framework_database_or_orm_dependency():
    forbidden = ("fastapi", "sqlalchemy", "psycopg", "app.infrastructure")
    violations = {
        f"{path.relative_to(ROOT)}:{module}"
        for layer in ("domain", "application", "ports")
        for path in (ROOT / "app" / layer).glob("*.py")
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == set()


def test_project_declares_only_the_phase12_database_dependencies():
    project = (ROOT / "pyproject.toml").read_text("utf-8").lower()

    assert "sqlalchemy>=2" in project
    assert "alembic>=1" in project
    assert "psycopg[binary]>=3" in project
    assert "django" not in project
